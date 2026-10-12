"""Broker service: feed -> rule engine -> risk guard -> approval gate -> executor.

No LLM runs here, and this is the only service that would hold broker keys. In the
prototype the feed is the simulator and execution is the simulated exchange.

    python -m services.broker
"""
from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime

from common import audit, db, registry, sim
from common import marketdata as MD
from common import timeutil as T
from common.config import load_risk, load_yaml, mode, universe
from common.units import parse_hhmm, parse_pct
from core import risk_guard as G
from core.engine import Intent
from core.session import HOLD, REJECT, SUBMIT, TradingSession
from dsl.evaluator import DayData, SignalBook

log = logging.getLogger("broker")
ACTOR = "broker"


class Broker:
    def __init__(self):
        self.conn = db.get()
        db.init(self.conn)
        self.risk = load_risk()
        self.cfg = load_yaml("sim.yaml")
        self.speed = float(self.cfg.get("speed", 30))
        self.paused = False
        self.stop_flag = False
        self.intents: dict[int, Intent] = {}
        self.handled: set[int] = set()
        self.last_signal_id = 0
        self.last_reconcile = 0.0
        self.registry_rows: dict[str, dict] = {}
        self.order_ids: dict[int, int] = {}  # exchange order id -> orders.id

    # ---------------------------------------------------------------- setup
    def start_day(self):
        conn, risk = self.conn, self.risk
        audit.log(conn, "broker.start", ACTOR, {"risk_sha256": risk.sha256, "mode": mode(), "feed": "simulator"})
        hist = db.kv_get(conn, "sim.history")
        if not hist:
            from .seed import seed
            log.info("no market history: seeding")
            hist = seed(conn, int(load_yaml("idealab.yaml").get("history_days", 30)), int(self.cfg.get("seed", 7)))
            from services.models.triage import backfill
            backfill(conn)
        self.day = datetime.fromisoformat(hist["session"]).date()
        chain = sim.chain_for(self.day, hist["history_days"])
        self.simday = sim.simulate(chain, int(hist["seed"]))[-1]
        assert self.simday.day == self.day

        prior = T.prior_close_ts(self.day)
        with db.tx(conn):
            for t in db.SESSION_TABLES:
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM bars WHERE ts>=?", (T.open_ts(self.day),))
            conn.execute("DELETE FROM signals WHERE ts>?", (prior,))
            conn.execute("DELETE FROM news WHERE session='live'")
            conn.execute("DELETE FROM equity_history WHERE source='live' AND session=?", (self.day.isoformat(),))
        registry.sync(conn, risk)
        strategies = []
        for row, s in registry.runnable(conn, risk):
            strategies.append(s)
            self.registry_rows[s.hash] = row
        self.syms = [s for s in universe() if s in self.simday.bars]
        self.days = {s: DayData(s, self.day, MD.history(conn, s, self.day)) for s in self.syms}
        # reference-only symbols (peer() reads): replayed with the session, never traded
        ref_syms = sorted({r for s in strategies for r in getattr(s, "references", []) if r in self.simday.bars} - set(self.syms))
        self.refs = {s: DayData(s, self.day, MD.history(conn, s, self.day)) for s in ref_syms}
        self.book = SignalBook()
        # carry the account over from the last recorded session (replay or live), so history is continuous
        last = db.row(conn, "SELECT equity FROM equity_history WHERE ts < ? ORDER BY ts DESC LIMIT 1",
                      (T.open_ts(self.day),))
        cash = float(last["equity"]) if last else float(self.cfg.get("starting_equity", 100_000))
        self.session = TradingSession(risk, strategies, self.days, self.book, cash=cash,
                                      slippage=parse_pct(self.cfg.get("fill_slippage", "0.02%")), gate=self.gate, refs=self.refs)
        ks = db.kv_get(conn, "kill_switch") or {}
        if ks.get("active"):
            self.session.kill, self.session.kill_reason = True, ks.get("reason", "")
        self.sim_now = T.ts_at(self.day, parse_hhmm(self.cfg.get("start", "08:00")))
        self.news_queue = sorted(self.simday.news, key=lambda n: n.ts)
        db.kv_set(conn, "sim.state", {"session": self.day.isoformat(), "phase": "premarket", "speed": self.speed,
                                      "paused": False, "strategies": [s.id + f" v{s.version}" for s in strategies]})
        db.kv_set(conn, "sim.now", self.sim_now)
        db.kv_set(conn, "risk.sha256", risk.sha256)
        self.persist_account()
        log.info("session %s: %d symbols, strategies: %s", self.day, len(self.syms), [s.id for s in strategies])

    # ---------------------------------------------------------------- gate
    def gate(self, strat, intent: Intent, checks) -> str:
        row = self.registry_rows.get(strat.hash, {})
        return SUBMIT if registry.is_auto(row.get("mode")) else HOLD

    # ---------------------------------------------------------------- loop
    def run(self):
        self.start_day()
        while not self.stop_flag:
            self.process_commands()
            self.process_intents()
            self.refresh_signals()
            waiting = self.cfg.get("pause_on_approval", True) and self.session.pending
            if self.paused or waiting or self.sim_now >= T.close_ts(self.day):
                self.heartbeat(waiting=bool(waiting))
                time.sleep(0.25)
                continue
            self.tick()
            premarket = self.sim_now < T.open_ts(self.day)
            speed = float(self.cfg.get("premarket_speed", 600)) if premarket else self.speed
            self.sleep(60.0 / max(speed, 0.1))

    def sleep(self, secs: float):
        end = time.time() + secs
        while time.time() < end and not self.stop_flag:
            time.sleep(min(0.1, end - time.time()))
            # stay responsive to approvals/kill while waiting
            self.process_commands()

    def tick(self):
        self.sim_now += 60
        conn = self.conn
        released = []
        while self.news_queue and self.news_queue[0].ts <= self.sim_now:
            released.append(self.news_queue.pop(0))
        if released:
            with db.tx(conn):
                for n in released:
                    conn.execute(
                        "INSERT INTO news(ext_id, ts, session, symbols, headline, summary, source) VALUES (?,?,?,?,?,?,?)"
                        " ON CONFLICT (ext_id) DO NOTHING",
                        (n.ext_id, n.ts, "live", json.dumps(n.symbols), n.headline, n.summary, n.source))
        bar_start = self.sim_now - 60
        if T.open_ts(self.day) <= bar_start < T.close_ts(self.day):
            i = (bar_start - T.open_ts(self.day)) // 60
            rows = []
            for s in self.syms:
                b = self.simday.bars[s][i]
                self.days[s].append(*b)
                rows.append((s, *b))
            for s, d in self.refs.items():
                d.append(*self.simday.bars[s][i])
            conn.executemany("INSERT INTO bars(symbol, ts, o, h, l, c, v) VALUES (?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO NOTHING", rows)
            self.refresh_signals()
            self.persist_events(self.session.step(i))
            if self.sim_now >= T.close_ts(self.day):
                self.persist_events(self.session.end_of_day())
                audit.log(conn, "session.closed", ACTOR, {"equity": round(self.session.exchange.equity(), 2)})
        db.kv_set(conn, "sim.now", self.sim_now)
        db.kv_set(conn, "feed.last_bar", self.sim_now)
        self.persist_account()  # record our view of positions first, then compare it with the broker's
        self.reconcile()
        self.heartbeat()

    def phase(self) -> str:
        if self.sim_now < T.open_ts(self.day):
            return "premarket"
        if self.sim_now >= T.close_ts(self.day):
            return "closed"
        return "open"

    def heartbeat(self, waiting: bool = False):
        db.kv_set(self.conn, "health.broker", {"pid_ts": time.time()})
        db.kv_set(self.conn, "sim.state", {
            "session": self.day.isoformat(), "phase": self.phase(), "speed": self.speed,
            "paused": self.paused, "waiting_for_approval": waiting,
            "kill": self.session.kill, "kill_reason": self.session.kill_reason,
            "strategies": [f"{s.id} v{s.version}" for s in self.session.strategies.values()],
        })

    # ---------------------------------------------------------------- signals
    def refresh_signals(self):
        rs = db.rows(self.conn, "SELECT id, ts, symbol, name, value FROM signals WHERE id>? AND ts<=? ORDER BY id",
                     (self.last_signal_id, self.sim_now))
        for r in rs:
            self.book.add(r["symbol"], r["name"], r["ts"], json.loads(r["value"]))
            self.last_signal_id = max(self.last_signal_id, r["id"])

    # ---------------------------------------------------------------- web -> broker
    def process_commands(self):
        conn = self.conn
        for c in db.rows(conn, "SELECT * FROM commands WHERE status='pending' ORDER BY id"):
            payload = json.loads(c["payload"] or "{}")
            result = "ok"
            if c["kind"] == "kill":
                reason = f"manual: {payload.get('reason') or 'dashboard'} ({c['issued_by']})"
                self.trigger_kill(reason, c["issued_by"])
            elif c["kind"] == "pause":
                self.paused = True
            elif c["kind"] == "resume":
                self.paused = False
            elif c["kind"] == "speed":
                self.speed = max(1.0, min(600.0, float(payload.get("value", self.speed))))
                result = f"speed={self.speed}"
            elif c["kind"] == "mode":
                h = payload.get("hash")
                if h in self.registry_rows:
                    self.registry_rows[h]["mode"] = payload.get("mode")
            elif c["kind"] == "reload":
                result = self.reload_strategies()
            else:
                result = "unknown command"
            conn.execute("UPDATE commands SET status='done', processed_at=?, result=? WHERE id=?",
                         (time.time(), result, c["id"]))
            audit.log(conn, f"command.{c['kind']}", c["issued_by"], {"payload": payload, "result": result})

    def reload_strategies(self) -> str:
        """Pick up registry changes (promotions, retirements, mode switches) mid-session."""
        current = {s.hash: (row, s) for row, s in registry.runnable(self.conn, self.risk)}
        for h, (row, s) in current.items():
            self.registry_rows[h] = row
            self.session.add_strategy(s)
        for h in list(self.session.runners):
            if h not in current:
                self.session.remove_strategy(h)
        return "running: " + ", ".join(f"{s.id} v{s.version}" for _, s in current.values())

    def demote(self, reason: str):
        for h in registry.demote_live_auto(self.conn, ACTOR, reason):
            if h in self.registry_rows:
                self.registry_rows[h]["mode"] = "live_manual"

    def trigger_kill(self, reason: str, actor: str = ACTOR):
        i = max(self.session.last_index, 0)
        events = self.session.kill_switch(i, reason)
        db.kv_set(self.conn, "kill_switch", {"active": True, "reason": reason, "ts": time.time(), "by": actor})
        self.demote(reason)
        # Alpaca equivalent: cancel all orders, close all positions, set suspend_trade on the account.
        db.kv_set(self.conn, "broker.suspend_trade", True)
        for it_id in self.intents:
            if it_id not in self.handled:
                self.conn.execute("UPDATE intents SET status='void', reason='kill switch' WHERE id=? AND status='pending'", (it_id,))
        self.persist_events(events)
        self.persist_account()

    def process_intents(self):
        conn, now = self.conn, time.time()
        for r in db.rows(conn, "SELECT * FROM intents WHERE status IN ('pending','approved','rejected') AND id NOT IN (%s)"
                         % (",".join(str(x) for x in self.handled) or "0")):
            it = self.intents.get(r["id"])
            if it is None:
                continue
            if r["status"] == "pending" and now > r["expires_wall"]:
                conn.execute("UPDATE intents SET status='expired', decided_wall=? WHERE id=?", (now, r["id"]))
                self.session.release(it)
                self.handled.add(r["id"])
                audit.log(conn, "intent.expired", ACTOR, {"intent_id": r["id"], "symbol": it.symbol})
            elif r["status"] == "rejected":
                self.session.release(it)
                self.handled.add(r["id"])
            elif r["status"] == "approved":
                checks = self.session.recheck(it)
                rc = [c.as_dict() for c in checks]
                if G.passed(checks) and not self.session.kill:
                    order = self.session.submit(it, self.session.last_index)
                    conn.execute("UPDATE intents SET status='submitted', recheck=? WHERE id=?", (json.dumps(rc), r["id"]))
                    self.insert_order(order, it.id)
                    audit.log(conn, "order.submitted", ACTOR, {"intent_id": r["id"], "symbol": it.symbol, "qty": it.qty,
                                                               "limit": it.limit, "stop": it.stop, "target": it.target})
                else:
                    failed = [c["name"] for c in rc if not c["ok"]]
                    conn.execute("UPDATE intents SET status='auto_rejected', recheck=?, reason=? WHERE id=?",
                                 (json.dumps(rc), "recheck failed: " + ", ".join(failed), r["id"]))
                    self.session.release(it)
                    audit.log(conn, "intent.auto_rejected", ACTOR, {"intent_id": r["id"], "failed": failed})
                self.handled.add(r["id"])

    # ---------------------------------------------------------------- persistence
    def insert_intent(self, it: Intent, status: str, guard: list[dict]) -> int:
        now = time.time()
        row = self.registry_rows.get(it.strategy_hash, {})
        it.id = self.conn.insert(
            "INSERT INTO intents(ts, strategy_hash, sid, symbol, side, qty, entry, limit_price, stop, target, risk_usd,"
            " risk_pct, decision, signals, guard, status, mode, created_wall, expires_wall)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (it.ts, it.strategy_hash, it.sid, it.symbol, it.side, it.qty, it.entry, it.limit, it.stop, it.target,
             it.risk_usd, it.risk_pct, json.dumps({"entry": it.decision, "universe": it.universe}),
             json.dumps(self.signal_context(it)), json.dumps(guard), status, row.get("mode", "manual"),
             now, now + self.risk.approval_expiry),
        )
        self.intents[it.id] = it
        return it.id

    def signal_context(self, it: Intent) -> dict:
        out = {}
        for name, val in it.signals.items():
            r = db.row(self.conn, "SELECT ts, model, tier, rationale, news_id FROM signals WHERE symbol=? AND name=? AND ts<=?"
                       " ORDER BY ts DESC LIMIT 1", (it.symbol, name, it.ts))
            out[name] = {"value": val, **(r or {})}
        return out

    def insert_order(self, order, intent_id):
        if order.id in self.order_ids:
            return self.order_ids[order.id]
        oid = self.conn.insert(
            "INSERT INTO orders(intent_id, ts, symbol, side, qty, kind, limit_price, stop, target, status, updated_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (intent_id, self.sim_now, order.symbol, order.side, order.qty, order.kind, order.limit,
             order.stop, order.target, order.status, self.sim_now),
        )
        self.order_ids[order.id] = oid
        return oid

    def persist_events(self, events):
        conn = self.conn
        for ev in events:
            if ev.kind == "intent":
                it = ev.data["intent"]
                iid = self.insert_intent(it, ev.data["status"], ev.data["guard"])
                if ev.data["status"] == "submitted":
                    order = next(o for o in reversed(self.session.exchange.orders) if o.intent_ref is it)
                    self.insert_order(order, iid)
                    self.handled.add(iid)
                audit.log(conn, f"intent.{ev.data['status']}", ACTOR,
                          {"intent_id": iid, "sid": it.sid, "symbol": it.symbol, "qty": it.qty, "entry": it.entry,
                           "stop": it.stop, "target": it.target, "risk_pct": round(it.risk_pct, 5)})
            elif ev.kind == "rejected":
                it = ev.data["intent"]
                iid = self.insert_intent(it, "rejected_guard", ev.data["guard"])
                self.handled.add(iid)
                failed = [c["name"] for c in ev.data["guard"] if not c["ok"]]
                conn.execute("UPDATE intents SET reason=? WHERE id=?", ("risk guard: " + ", ".join(failed), iid))
                audit.log(conn, "intent.rejected_guard", ACTOR, {"intent_id": iid, "symbol": it.symbol, "failed": failed})
            elif ev.kind == "fill":
                f = ev.obj
                oid = self.order_ids.get(f.order.id) if f.order else None
                if oid is None:
                    oid = conn.insert(
                        "INSERT INTO orders(intent_id, ts, symbol, side, qty, kind, status, filled_qty, avg_price, updated_ts)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (getattr((f.trade or {}).get("intent_ref"), "id", None), f.ts, f.symbol, f.side, f.qty,
                         f.kind, "filled", f.qty, f.price, f.ts))
                else:
                    conn.execute("UPDATE orders SET status='filled', filled_qty=?, avg_price=?, updated_ts=? WHERE id=?",
                                 (f.qty, f.price, f.ts, oid))
                ref = f.order.intent_ref if f.order and f.order.intent_ref else (f.trade or {}).get("intent_ref")
                conn.execute("INSERT INTO fills(order_id, intent_id, ts, symbol, side, qty, price, kind) VALUES (?,?,?,?,?,?,?,?)",
                             (oid, getattr(ref, "id", None), f.ts, f.symbol, f.side, f.qty, f.price, f.kind))
                if ref is not None and getattr(ref, "id", None) and f.kind == "entry":
                    conn.execute("UPDATE intents SET status='filled' WHERE id=?", (ref.id,))
                audit.log(conn, "fill", ACTOR, {"symbol": f.symbol, "side": f.side, "qty": f.qty,
                                                "price": round(f.price, 4), "kind": f.kind, "sim_ts": f.ts})
            elif ev.kind == "trade":
                t = ev.data
                ref = t.get("intent_ref")
                narrative = narrate(t, ref)
                conn.execute(
                    "INSERT INTO trades(intent_id, sid, strategy_hash, symbol, side, qty, entry_ts, entry_price, exit_ts,"
                    " exit_price, exit_kind, pnl, r_multiple, narrative) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (getattr(ref, "id", None), getattr(ref, "sid", None), getattr(ref, "strategy_hash", None), t["symbol"],
                     t["side"], t["qty"], t["entry_ts"], t["entry_price"], t["exit_ts"], t["exit_price"], t["exit_kind"],
                     t["pnl"], t["r_multiple"], narrative))
                audit.log(conn, "trade.closed", ACTOR, {"symbol": t["symbol"], "pnl": round(t["pnl"], 2),
                                                        "exit": t["exit_kind"], "intent_id": getattr(ref, "id", None)})
            elif ev.kind == "cancelled":
                conn.execute("UPDATE orders SET status='cancelled', updated_ts=? WHERE id=?",
                             (self.sim_now, self.order_ids.get(ev.data.get("order_id"))))
                ref = getattr(ev.obj, "intent_ref", None)
                if ref is not None and getattr(ref, "id", None):
                    conn.execute("UPDATE intents SET status='unfilled', reason=? WHERE id=? AND status='submitted'",
                                 (ev.data.get("reason"), ref.id))
                audit.log(conn, "order.cancelled", ACTOR, ev.data)
            elif ev.kind == "exit_request":
                for order in self.session.exchange.working(ev.data["symbol"]):
                    if order.kind == "exit":
                        self.insert_order(order, None)
                audit.log(conn, "exit.requested", ACTOR, {k: v for k, v in ev.data.items() if k != "decision"})
            elif ev.kind == "kill":
                db.kv_set(conn, "kill_switch", {"active": True, "reason": ev.data["reason"], "ts": time.time(), "by": ACTOR})
                self.demote(ev.data["reason"])
                db.kv_set(conn, "broker.suspend_trade", True)
                audit.log(conn, "kill_switch.triggered", ACTOR, ev.data)

    def persist_account(self):
        conn, ex = self.conn, self.session.exchange
        eq = ex.equity()
        with db.tx(conn):
            conn.execute("DELETE FROM positions")
            for p in ex.positions.values():
                ref = p.intent_ref
                conn.execute(
                    "INSERT INTO positions(symbol, qty, side, avg_price, stop, target, strategy_hash, sid, intent_id, opened_ts, last_price)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p.symbol, p.qty, p.side, p.entry_price, p.stop, p.target, getattr(ref, "strategy_hash", None),
                     getattr(ref, "sid", None), getattr(ref, "id", None), p.entry_ts, p.last))
            conn.execute("INSERT INTO equity_curve(ts, equity, cash) VALUES (?,?,?) ON CONFLICT (ts) DO UPDATE"
                         " SET equity=excluded.equity, cash=excluded.cash", (self.sim_now, eq, ex.cash))
            if T.open_ts(self.day) < self.sim_now <= T.close_ts(self.day):
                pnl = {k: round(v, 2) for k, v in self.session.pnl_by_strategy().items()}
                conn.execute(
                    "INSERT INTO equity_history(ts, source, session, equity, cash, strat_pnl) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT (source, ts) DO UPDATE SET equity=excluded.equity, cash=excluded.cash,"
                    " strat_pnl=excluded.strat_pnl", (self.sim_now, "live", self.day.isoformat(), eq, ex.cash, json.dumps(pnl)))
        db.kv_set(conn, "account", {
            "equity": eq, "cash": ex.cash, "start_equity": self.session.start_equity,
            "day_pnl": eq - self.session.start_equity, "gross_exposure": ex.gross_exposure(),
            "open_positions": len(ex.positions), "trades_today": self.session.trades_today,
            "sim_ts": self.sim_now,
        })

    def reconcile(self):
        """Compare what the DB says we hold with what the exchange says. Mismatch -> kill switch."""
        if self.sim_now - self.last_reconcile < self.risk.reconcile_every:
            return
        self.last_reconcile = self.sim_now
        db_pos = {r["symbol"]: r["qty"] for r in db.rows(self.conn, "SELECT symbol, qty FROM positions")}
        ex_pos = {s: p.qty for s, p in self.session.exchange.positions.items()}
        ok = db_pos == ex_pos
        db.kv_set(self.conn, "reconcile", {"ok": ok, "sim_ts": self.sim_now, "db": db_pos, "broker": ex_pos})
        if not ok and not self.session.kill:
            self.trigger_kill(f"reconciliation mismatch: db={db_pos} broker={ex_pos}")


def narrate(t: dict, ref) -> str:
    """Deterministic post-trade narrative (Claude can expand on it from the trade page)."""
    why = ""
    if ref is not None:
        conds = [d["cond"] for d in ref.decision]
        why = f" Entered on bar close {T.fmt(ref.ts)} because all of: {'; '.join(conds)}."
    r = t.get("r_multiple")
    return (f"{t['side'].title()} {t['qty']} {t['symbol']} @ {t['entry_price']:.2f}, exited @ {t['exit_price']:.2f} "
            f"via {t['exit_kind']} at {T.fmt(t['exit_ts'])}: P&L {t['pnl']:+,.2f}"
            + (f" ({r:+.2f}R)." if r is not None else ".") + why)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s broker %(levelname)s %(message)s")
    b = Broker()

    def stop(*_):
        b.stop_flag = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    b.run()


if __name__ == "__main__":
    main()
