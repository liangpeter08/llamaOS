"""Portfolio desk: runs `kind: portfolio` strategies on the Alpaca paper account.

Once per session, at each strategy's `decide_at` (clamped before early closes), it reads fresh
quotes, asks core.portfolio.plan() for the day's orders, checks each one against the portfolio risk controls
`portfolio:`, and sends day market orders. Sleeve positions are held overnight; the intraday
flatten and the watchdog's deadline flatten skip them. The kill switch still closes everything.

DSL 0.2 sleeves also read market inputs (an index from the account-configured feed, a ticker basket from
synchronized 1-minute bars), observe macro overlays intraday, cancel strategy buys when a macro trigger
pauses buying, and keep rotation proceeds as locked cash. Overlays act only in enforce mode (file says
enforce and the owner enabled it); in shadow mode their decisions are only logged as portfolio events.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

import httpx

from common import audit, db, funds, market_inputs as MI, marketdata as MD, registry, sleeves, timeutil as T
from core import funds as F
from common.config import RiskConfig, mode
from core import portfolio as P
from core import portfolio_v2 as P2
from .alpaca import epoch
from .index_feed import IndexFeed

log = logging.getLogger("portfolio")
ACTOR = "broker"
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}
HISTORY_DAYS = 400  # calendar days: covers the DSL's 250-session daily features
DECIDE_BEFORE_CLOSE = 15 * 60  # decide no later than 15 min before the (possibly early) close
SUBMIT_BEFORE_CLOSE = 5 * 60  # and send orders no later than 5 min before it
OPEN_ORDER = ("pending", "approved", "rechecking", "submitting", "submitted")


class PortfolioDesk:
    def __init__(self, conn, api, risk: RiskConfig, index_feed=None):
        self.conn, self.api, self.risk = conn, api, risk
        self.index_feed = index_feed or IndexFeed()
        self.observed: dict[str, float] = {}  # strategy hash -> last intraday observation (wall clock)
        self.basket_prior: dict[tuple[str, str], float] = {}  # (day, symbol) -> split-adjusted prior close
        self.strats: dict[str, tuple[dict, object, bool]] = {}  # hash -> (registry row, strategy, retire)
        self.history_at = 0.0
        self.history_syms: set[str] = set()
        self.mismatch = ""
        self.error = ""

    # ---------------------------------------------------------------- strategies and data
    def reload(self) -> str:
        if self.risk.portfolio is None:
            self.strats = {}
            return "portfolio: disabled on the Risk page"
        current = {}
        for r, s in registry.runnable(self.conn, self.risk, kind="portfolio"):
            if r["status"] != "paper":
                continue
            v2 = getattr(s, "v2", None)
            if v2 and v2.overlays and db.kv_get(self.conn, f"scenario_catalog.pin.{s.hash}") != v2.catalog_hash:
                # The scenario catalog changed since promotion: fail closed until the owner promotes again.
                audit.log(self.conn, "portfolio.catalog_changed", ACTOR, {"hash": s.hash, "sid": s.id})
                continue
            current[s.hash] = (r, s, False)
        # Retired sleeves that still hold shares are sold at their next decision time.
        for r in db.rows(self.conn, "SELECT s.* FROM strategies s JOIN portfolio_state p ON p.strategy_hash=s.hash"
                                    " WHERE s.status='retired'"):
            sl = sleeves.load_state(self.conn, r["hash"])
            if sl and sl.status == "active":
                s, _ = registry.load(self.conn, r["hash"], self.risk)
                if s is not None:
                    current[r["hash"]] = (r, s, True)
        self.strats = current
        return "portfolio: " + (", ".join(f"{s.id}{' (retiring)' if ret else ''}" for _, s, ret in current.values()) or "none")

    def ensure_history(self, day: date, force: bool = False):
        """Daily bars for every portfolio symbol, so backtests and prior closes have data."""
        if self.risk.portfolio is None:
            return
        syms = sorted(sleeves.portfolio_symbols(self.conn, self.risk))
        new = set(syms) - self.history_syms
        if not syms or (not force and not new and time.time() - self.history_at < 3600):
            return
        prev = T.prev_weekday(day).isoformat()
        latest = {r["symbol"]: r["d"] for r in db.rows(self.conn, "SELECT symbol, MAX(day) d FROM daily GROUP BY symbol")}
        stale = [s for s in syms if latest.get(s) is None or latest[s] < prev]
        if stale:
            bars = self.api.bars(stale, "1Day", time.time() - HISTORY_DAYS * 86400, T.open_ts(day), adjustment="split")
            with db.tx(self.conn):
                for sym, rows in bars.items():
                    for b in rows:
                        d = T.et(epoch(b["t"])).date()
                        if d < day:
                            self.conn.execute("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)"
                                              " ON CONFLICT(symbol,day) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,v=excluded.v",
                                              (sym, str(d), b["o"], b["h"], b["l"], b["c"], b["v"]))
            audit.log(self.conn, "portfolio.history", ACTOR, {"symbols": stale})
        for inst in sorted(sleeves.portfolio_indexes(self.conn, self.risk)):
            r = db.row(self.conn, "SELECT MAX(day) d FROM index_daily WHERE instrument=?", (inst,))
            if r and r["d"] and r["d"] >= prev:
                continue
            try:
                rows = self.index_feed.history(inst, time.time() - HISTORY_DAYS * 86400, day)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("index history for %s unavailable: %s", inst, type(e).__name__)
                continue
            with db.tx(self.conn):
                MI.store_history(self.conn, inst, rows, MI.feeds().get(inst, {}).get("provider", ""))
            audit.log(self.conn, "portfolio.index_history", ACTOR, {"instrument": inst, "sessions": len(rows)})
        self.history_syms, self.history_at = set(syms), time.time()

    # ---------------------------------------------------------------- account views
    def owned(self) -> dict[str, str]:
        return sleeves.owned_symbols(self.conn)

    def day_pnl(self, positions: list[dict], day: date) -> float:
        """Today's P&L of sleeve holdings, so the intraday daily-loss limit can exclude it.

        sum(qty_now * (price - prior_close)) - sum(today's signed fills * (fill - prior_close))
        """
        owned = self.owned()
        pnl = 0.0
        prior: dict[str, float] = {}

        def pc(sym):
            if sym not in prior:
                r = db.row(self.conn, "SELECT c FROM daily WHERE symbol=? AND day<? ORDER BY day DESC LIMIT 1", (sym, str(day)))
                prior[sym] = r["c"] if r else None
            return prior[sym]

        for p in positions:
            if p["symbol"] in owned and pc(p["symbol"]):
                pnl += float(p["qty"]) * (float(p["current_price"]) - pc(p["symbol"]))
        for r in db.rows(self.conn, "SELECT symbol, side, filled_qty, avg_price FROM portfolio_orders WHERE day=? AND filled_qty>0", (str(day),)):
            if pc(r["symbol"]):
                signed = r["filled_qty"] if r["side"] == "buy" else -r["filled_qty"]
                pnl -= signed * (r["avg_price"] - pc(r["symbol"]))
        return pnl

    def reconcile(self, positions: list[dict]) -> str:
        """Sleeve shares must equal Alpaca's position in every sleeve symbol (no orders in flight)."""
        alpaca = {p["symbol"]: float(p["qty"]) for p in positions}
        expected: dict[str, int] = {}
        for hold in sleeves.all_holdings(self.conn).values():
            for sym, h in hold.items():
                expected[sym] = expected.get(sym, 0) + h.qty
        busy = {r["symbol"] for r in db.rows(self.conn, "SELECT DISTINCT symbol FROM portfolio_orders WHERE status IN ('submitting','submitted')")}
        bad = [f"{sym}: Alpaca {alpaca.get(sym, 0):g} vs sleeves {q}" for sym, q in expected.items()
               if sym not in busy and abs(alpaca.get(sym, 0) - q) > 1e-6 and (q or sym in alpaca)]
        self.mismatch = "; ".join(bad)
        return self.mismatch

    # ---------------------------------------------------------------- loop
    def tick(self, clock: dict, account: dict, positions: list[dict], close_ts: int, kill: bool, paused: bool,
             intraday_symbols: set[str]):
        now = time.time()
        day = T.et(epoch(clock["timestamp"])).date()
        self.track()
        self.conn.execute("UPDATE portfolio_orders SET status='approved' WHERE status='rechecking'")  # interrupted re-check
        self.conn.execute("UPDATE portfolio_orders SET status='expired' WHERE status IN ('pending','approved') AND expires_wall<?", (now,))
        if kill:
            self.halt("kill switch")
            if clock.get("is_open"):
                self.liquidate("kill")
            return
        if not clock.get("is_open") or not close_ts or not self.strats:
            return
        self.reconcile(positions)
        cutoff = close_ts - SUBMIT_BEFORE_CLOSE
        if not paused and mode() == "paper" and not self.mismatch and now < cutoff:
            for h, (row, s, retire) in list(self.strats.items()):
                if not retire and self.observes(s) and now - self.observed.get(h, 0) >= s.v2.monitoring.min_gap:
                    self.observed[h] = now
                    self.observe(h, row, s, day, account, positions, cutoff, intraday_symbols)
                decide = min(T.ts_at(day, s.decide_at), close_ts - DECIDE_BEFORE_CLOSE)
                if now >= decide:
                    self.decide(h, row, s, retire, day, account, positions, cutoff, intraday_symbols)
        if now < cutoff and not self.mismatch:
            self.submit(day, account, positions, intraday_symbols)

    def decide(self, h, row, s, retire, day, account, positions, cutoff, intraday_symbols):
        sl = sleeves.load_state(self.conn, h)
        if sl is not None and (sl.status != "active" or sl.last_day == str(day)):
            return
        if sl is None:
            if retire or (s.start_date and day < s.start_date) or (s.end_date and day >= s.end_date):
                return
            eq = float(account["equity"])
            sl = P.start(s, eq, str(day), funds.sleeve_capital(self.conn, s, eq) if self.risk.funds else None)
        hold, realized, _ = sleeves.holdings(self.conn, h)
        sl.realized = realized
        fund_status, fund_reason = funds.status(self.conn, s.id)
        if fund_status == "paused" and not retire:
            retire = True  # the strategy's fund stop (drawdown or owner pause) sells the sleeve
            log.info("portfolio %s: fund paused (%s), selling the sleeve", s.id, fund_reason)
        quotes = self.quotes([*s.symbols, *[r for r in s.references if r not in s.symbols]], day)
        if s.daily_lookback:
            for sym, q in quotes.items():  # completed sessions for daily-history features
                q.closes = [(h.date, h.c) for h in MD.history(self.conn, sym, day)]
        v2 = getattr(s, "v2", None)
        enforce = sleeves.enforced(self.conn, s)
        if v2 is not None:
            vals, notes = self.inputs(s, day)
            missed = self.missed_sessions(s, sl, day)
            dp = P.plan(s, sl, quotes, hold, str(day), self.risk, retire=retire, inputs=vals, working=self.working_sells(h),
                        open_reductions=self.open_reductions(h), enforce=enforce, locked_usd=sleeves.locked_cash(self.conn, h),
                        blocks=self.assumption_blocks(h) if enforce else [])
            if missed:
                dp.events.insert(0, {"kind": "missed_decision", "sessions": missed, "effect": "skipped, no catch-up trades"})
            db.kv_set(self.conn, f"portfolio.inputs.{h}", {"ts": time.time(), "day": str(day), "values": vals, "notes": notes})
        else:
            dp = P.plan(s, sl, quotes, hold, str(day), self.risk, retire=retire)
        with db.tx(self.conn):
            self.insert_orders(h, row, s, day, dp.orders, account, positions, intraday_symbols, cutoff)
            self.record_events(h, s, day, dp, enforce)
            self.conn.execute("INSERT INTO portfolio_days(strategy_hash,day,records,event) VALUES (?,?,?,?)"
                              " ON CONFLICT(strategy_hash,day) DO UPDATE SET records=excluded.records,event=excluded.event",
                              (h, str(day), json.dumps(dp.records), dp.event))
            sleeves.save_state(self.conn, h, s.id, sl)
        audit.log(self.conn, "portfolio.decided", ACTOR, {"hash": h, "sid": s.id, "day": str(day), "session": sl.sessions,
                                                          "orders": len(dp.orders), "event": dp.event, "status": sl.status})

    def insert_orders(self, h, row, s, day, orders, account, positions, intraday_symbols, cutoff):
        status = "pending" if row["mode"] == "manual" else "approved"
        for o in orders:
            checks = self.checks(h, o, account, positions, intraday_symbols, day)
            ok = all(c.ok for c in checks)
            oid = self.conn.insert(
                "INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,ref_price,reason,decision,guard,status,mode,"
                "created_wall,expires_wall,decided_by,decided_wall,note,reserve_qty,overlay) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, s.id, str(day), time.time(), o.symbol, o.side, o.qty, o.price, o.reason, json.dumps(o.decision),
                 json.dumps([c.as_dict() for c in checks]), status if ok else "auto_rejected", row["mode"], time.time(), cutoff,
                 None if status == "pending" or not ok else "paper-auto", None if status == "pending" or not ok else time.time(),
                 None if ok else ", ".join(c.name for c in checks if not c.ok), o.reserve_qty, o.overlay or None))
            self.conn.execute("UPDATE portfolio_orders SET client_id=? WHERE id=?", (f"llama-p-{oid}-{h[:10]}", oid))
            if not ok and o.side == "sell" and o.overlay:
                # A verified reduction keeps its intent: the engine re-issues the remainder at the next decision.
                self.conn.execute("INSERT INTO portfolio_events(strategy_hash,sid,day,ts,kind,overlay,mode,payload) VALUES (?,?,?,?,?,?,?,?)",
                                  (h, s.id, str(day), time.time(), "reduction_blocked", o.overlay, "enforce",
                                   json.dumps({"symbol": o.symbol, "qty": o.qty, "failed": [c.name for c in checks if not c.ok]})))

    def record_events(self, h, s, day, dp, enforce: bool):
        mode_ = "enforce" if enforce else "shadow"
        rows = [(e["kind"], e.get("overlay"), e) for e in dp.events]
        if dp.shadow:
            rows.append(("shadow", None, {"orders": dp.shadow}))
        for kind, overlay, payload in rows:
            self.conn.execute("INSERT INTO portfolio_events(strategy_hash,sid,day,ts,kind,overlay,mode,payload) VALUES (?,?,?,?,?,?,?,?)",
                              (h, s.id, str(day), time.time(), kind, overlay, mode_, json.dumps(payload, default=str)))
        if enforce and any(e["kind"] == "macro_trigger" and "pause" in e.get("effect", "") for e in dp.events):
            self.cancel_buys(h, "macro overlay paused buys")

    # ---------------------------------------------------------------- 0.2 overlays
    def observes(self, s) -> bool:
        v2 = getattr(s, "v2", None)
        return bool(v2 and v2.monitoring and any(o.kind == "macro" and o.on == "every_fresh_observation" for o in v2.overlays))

    def observe(self, h, row, s, day, account, positions, cutoff, intraday_symbols):
        """One fresh intraday observation of the macro inputs (at most every monitoring.evaluation.min_gap)."""
        sl = sleeves.load_state(self.conn, h)
        if sl is None or sl.status != "active":
            return
        names = {o.metric for o in s.overlays if o.kind == "macro" and o.on == "every_fresh_observation"}
        vals, notes = self.inputs(s, day, names)
        db.kv_set(self.conn, f"portfolio.observed.{h}", {"ts": time.time(), "values": vals, "notes": notes})
        hold, _, _ = sleeves.holdings(self.conn, h)
        prices = {p["symbol"]: float(p["current_price"]) for p in positions if p.get("current_price")}
        enforce = sleeves.enforced(self.conn, s)
        dp = P2.observe(s, sl, vals, hold, prices, str(day), self.working_sells(h), enforce)
        if not dp.events:
            return
        with db.tx(self.conn):
            self.insert_orders(h, row, s, day, dp.orders, account, positions, intraday_symbols, cutoff)
            self.record_events(h, s, day, dp, enforce)
            sleeves.save_state(self.conn, h, s.id, sl)
        audit.log(self.conn, "portfolio.observed", ACTOR, {"hash": h, "sid": s.id, "day": str(day), "events": dp.events,
                                                          "orders": len(dp.orders), "mode": "enforce" if enforce else "shadow"})

    def working_sells(self, h) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in db.rows(self.conn, f"SELECT symbol, qty, filled_qty FROM portfolio_orders WHERE strategy_hash=? AND side='sell'"
                                    f" AND status IN {OPEN_ORDER}", (h,)):
            out[r["symbol"]] = out.get(r["symbol"], 0) + int(r["qty"] - r["filled_qty"])
        return out

    def open_reductions(self, h) -> bool:
        return bool(db.row(self.conn, f"SELECT id FROM portfolio_orders WHERE strategy_hash=? AND reason='macro_trim'"
                                      f" AND status IN {OPEN_ORDER} LIMIT 1", (h,)))

    def assumption_blocks(self, h) -> list[str]:
        b = db.kv_get(self.conn, f"assumption.block.{h}") or {}
        return list(b.get("reasons") or []) if (b.get("until") or 0) > time.time() else []

    def buys_paused(self, h, s) -> bool:
        if not sleeves.enforced(self.conn, s):
            return False
        sl = sleeves.load_state(self.conn, h)
        return bool(sl and any(st.get("phase") in ("reduction_pending", "awaiting_positive_session") for st in sl.overlays.values()))

    def cancel_buys(self, h, why: str):
        """Void the strategy's unsent buys and cancel the ones working at the broker (fills are reconciled by track())."""
        self.conn.execute("UPDATE portfolio_orders SET status='void', note=? WHERE strategy_hash=? AND side='buy'"
                          " AND status IN ('pending','approved','rechecking')", (why, h))
        for r in db.rows(self.conn, "SELECT id, broker_id FROM portfolio_orders WHERE strategy_hash=? AND side='buy'"
                                    " AND status IN ('submitting','submitted') AND broker_id IS NOT NULL", (h,)):
            try:
                self.api.cancel(r["broker_id"])
                audit.log(self.conn, "portfolio.buy_cancelled", ACTOR, {"order_id": r["id"], "why": why})
            except httpx.HTTPError as e:
                log.warning("cancel of sleeve buy %s failed: %s", r["id"], type(e).__name__)

    def missed_sessions(self, s, sl, day) -> int:
        """Sessions with stored daily bars between the last decision and today (decisions are never caught up)."""
        if sl is None or not sl.last_day:
            return 0
        r = db.row(self.conn, "SELECT COUNT(DISTINCT day) n FROM daily WHERE symbol=? AND day>? AND day<?",
                   (s.symbols[0], sl.last_day, str(day)))
        return int(r["n"]) if r else 0

    def inputs(self, s, day: date, names: set[str] | None = None) -> tuple[dict, dict]:
        """Market input values now (None = unknown) and a note per input saying why it is unknown."""
        vals, notes = {}, {}
        for name, inp in s.v2.inputs.items():
            if names is not None and name not in names:
                continue
            try:
                vals[name], notes[name] = (self.index_input if inp.kind == "index" else self.basket_input)(inp, day)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                vals[name], notes[name] = None, f"feed error: {type(e).__name__}"
        return vals, notes

    def index_input(self, inp, day: date) -> tuple[float | None, str]:
        now = time.time()
        q = self.index_feed.quote(inp.instrument)
        if q is None:
            return None, f"no configured feed for {inp.instrument} (config/market_inputs.yaml)"
        value, ts = q
        if ts < T.open_ts(day):
            return None, "no value from the current regular session yet"
        if now - ts > inp.max_age:
            return None, f"stale: {now - ts:.0f}s old, max {inp.max_age:.0f}s"
        prev = db.row(self.conn, "SELECT day, c FROM index_daily WHERE instrument=? AND day<? ORDER BY day DESC LIMIT 1",
                      (inp.instrument, str(day)))
        last_session = db.row(self.conn, "SELECT MAX(day) d FROM daily WHERE day<?", (str(day),))
        if not prev or (last_session and last_session["d"] and prev["day"] < last_session["d"]):
            return None, "previous regular-session close not stored"
        return value / prev["c"] - 1, f"{value:,.2f} vs prior close {prev['c']:,.2f} ({prev['day']})"

    def basket_input(self, inp, day: date) -> tuple[float | None, str]:
        """Equal-weight mean of member returns at the latest completed 1-minute bar that every member has."""
        now = time.time()
        bars = self.api.bars(inp.symbols, "1Min", max(T.open_ts(day), now - 900), now)
        series = {sym: {b["t"]: float(b["c"]) for b in bars.get(sym, [])} for sym in inp.symbols}
        common = set.intersection(*(set(v) for v in series.values())) if series else set()
        done = [t for t in common if epoch(t) + 60 <= now]
        if not done:
            return None, "no synchronized completed 1-minute bar for every member"
        t = max(done, key=epoch)
        if now - (epoch(t) + 60) > inp.max_age:
            return None, f"stale: latest common bar ended {now - epoch(t) - 60:.0f}s ago"
        missing = [sym for sym in inp.symbols if (str(day), sym) not in self.basket_prior]
        if missing:
            daily = self.api.bars(missing, "1Day", T.open_ts(day) - 15 * 86400, T.open_ts(day), adjustment="split")
            for sym in missing:
                prior = [b for b in daily.get(sym, []) if T.et(epoch(b["t"])).date() < day]
                if prior:
                    self.basket_prior[(str(day), sym)] = float(prior[-1]["c"])
        rets = [series[sym][t] / self.basket_prior[(str(day), sym)] - 1 for sym in inp.symbols if (str(day), sym) in self.basket_prior]
        if len(rets) != len(inp.symbols):
            return None, "a member's split-adjusted prior close is missing (never reweighted)"
        return sum(rets) / len(rets), f"{len(rets)} members at {t}"

    def quotes(self, symbols: list[str], day: date) -> dict[str, P.Quote]:
        out = {}
        snaps = self.api.snapshots(symbols)
        for sym in symbols:
            sn = snaps.get(sym) or {}
            trade, prev = sn.get("latestTrade") or {}, sn.get("prevDailyBar") or {}
            if not trade.get("t") or not prev.get("t") or time.time() - epoch(trade["t"]) > self.risk.stale_feed_halt:
                continue  # stale or missing: plan() skips the symbol (fails closed)
            if T.et(epoch(prev["t"])).date() >= day:
                continue
            out[sym] = P.Quote(float(trade["p"]), float(prev["c"]))
        return out

    def checks(self, h, o, account, positions, intraday_symbols, day):
        owned = self.owned()
        hold, _, _ = sleeves.holdings(self.conn, h)
        mv = {p["symbol"]: abs(float(p["market_value"])) for p in positions}
        working = db.rows(self.conn, "SELECT symbol, side, qty, filled_qty, ref_price FROM portfolio_orders WHERE status IN ('approved','submitting','submitted')")
        pending_buys = sum((r["qty"] - r["filled_qty"]) * r["ref_price"] for r in working if r["side"] == "buy")
        pending_sells = sum(r["qty"] - r["filled_qty"] for r in working if r["side"] == "sell" and r["symbol"] == o.symbol)
        held = hold.get(o.symbol, P.Holding()).qty
        book = P.Book(
            equity=float(account["equity"]),
            gross_exposure=sum(mv.values()) + pending_buys,
            sleeve_exposure=sum(v for sym, v in mv.items() if sym in owned) + pending_buys,
            symbol_value=held * o.price + sum((r["qty"] - r["filled_qty"]) * r["ref_price"] for r in working
                                               if r["side"] == "buy" and r["symbol"] == o.symbol),
            held_qty=int(held - pending_sells),
            orders_today=db.row(self.conn, "SELECT COUNT(*) n FROM portfolio_orders WHERE day=? AND status IN ('approved','submitting','submitted','filled','partially_filled')",
                                (str(day),))["n"],
            intraday_symbols=intraday_symbols,
        )
        hist = db.rows(self.conn, "SELECT c, v FROM daily WHERE symbol=? AND day<? ORDER BY day DESC LIMIT 20", (o.symbol, str(day)))
        adv = sum(r["c"] * r["v"] for r in hist) / 20 if len(hist) == 20 else None
        checks = P.check_order(o, book, self.risk, adv)
        if o.side == "buy" and self.risk.funds:
            sid = (db.row(self.conn, "SELECT sid FROM strategies WHERE hash=?", (h,)) or {}).get("sid", "")
            checks += F.check_buy(sid, "portfolio", o.qty * o.price, funds.account(self.conn, self.risk), self.risk.funds)
        from common import monitoring
        checks.append(P.Check("monitoring_enabled", o.side == "sell" or o.symbol in monitoring.enabled(self.conn), "watchlist monitoring required for buys"))
        checks.append(P.Check("buying_power", o.side == "sell" or o.qty * o.price <= float(account.get("buying_power") or 0),
                              "broker buying power"))
        checks.append(P.Check("account_available", not any(account.get(k) for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user")),
                              "account status"))
        return checks

    def submit(self, day, account, positions, intraday_symbols):
        rows = db.rows(self.conn, "SELECT * FROM portfolio_orders WHERE status='approved' ORDER BY (side='buy'), id")
        for r in rows:
            if r["strategy_hash"] not in self.strats:
                self.conn.execute("UPDATE portfolio_orders SET status='void', note='strategy no longer running' WHERE id=?", (r["id"],))
                continue
            if r["side"] == "buy" and self.buys_paused(r["strategy_hash"], self.strats[r["strategy_hash"]][1]):
                self.conn.execute("UPDATE portfolio_orders SET status='void', note='buys paused by a macro overlay' WHERE id=?", (r["id"],))
                continue
            o = P.Order(r["symbol"], r["side"], r["qty"], r["ref_price"], r["reason"], reserve_qty=r.get("reserve_qty") or 0)
            # The order's own approved row counts as working; re-check against everything else.
            self.conn.execute("UPDATE portfolio_orders SET status='rechecking' WHERE id=?", (r["id"],))
            checks = self.checks(r["strategy_hash"], o, account, positions, intraday_symbols, day)
            if not all(c.ok for c in checks):
                self.conn.execute("UPDATE portfolio_orders SET status='auto_rejected', recheck=?, note=? WHERE id=?",
                                  (json.dumps([c.as_dict() for c in checks]), ", ".join(c.name for c in checks if not c.ok), r["id"]))
                continue
            self.conn.execute("UPDATE portfolio_orders SET status='submitting', recheck=? WHERE id=?",
                              (json.dumps([c.as_dict() for c in checks]), r["id"]))
            try:
                order = self.api.market(r["symbol"], r["qty"], r["side"], r["client_id"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 or e.response.status_code == 429:
                    raise  # not proof of rejection: track() finds it by client id
                self.conn.execute("UPDATE portfolio_orders SET status='broker_rejected', note=? WHERE id=?",
                                  (f"Alpaca HTTP {e.response.status_code}", r["id"]))
                audit.log(self.conn, "portfolio.order_rejected", ACTOR, {"order_id": r["id"], "http_status": e.response.status_code})
                continue
            self.conn.execute("UPDATE portfolio_orders SET broker_id=?, status='submitted' WHERE id=?", (order["id"], r["id"]))
            audit.log(self.conn, "portfolio.order_submitted", ACTOR, {"order_id": r["id"], "broker_id": order["id"],
                                                                     "symbol": r["symbol"], "side": r["side"], "qty": r["qty"]})
            self.record(r, order)

    def track(self):
        """Follow submitted sleeve orders to their fills."""
        for r in db.rows(self.conn, "SELECT * FROM portfolio_orders WHERE status IN ('submitting','submitted') ORDER BY id"):
            order = self.api.order(r["broker_id"]) if r["broker_id"] else self.api.by_client_id(r["client_id"])
            if order is None:
                if time.time() - (r["created_wall"] or 0) > 600:
                    self.conn.execute("UPDATE portfolio_orders SET status='void', note='not found at the broker' WHERE id=?", (r["id"],))
                continue
            self.record(r, order)

    def record(self, r: dict, order: dict):
        qty = min(float(order.get("filled_qty") or 0), r["qty"])
        avg = float(order["filled_avg_price"]) if order.get("filled_avg_price") else None
        st = order["status"]
        if st in TERMINAL:
            status = "filled" if qty >= r["qty"] else "partially_filled" if qty > 0 else "cancelled"
        else:
            status = "submitted"
        with db.tx(self.conn):
            if qty > r["filled_qty"] and avg is not None:
                delta = qty - r["filled_qty"]
                # price of this increment from cumulative quantities and averages
                px = (qty * avg - r["filled_qty"] * (r["avg_price"] or 0)) / delta
                ts = epoch(order.get("filled_at") or order.get("updated_at") or order["created_at"])
                before, _, _ = sleeves.holdings(self.conn, r["strategy_hash"])
                cost = before.get(r["symbol"], P.Holding()).cost
                self.conn.execute("INSERT INTO fills(order_id,intent_id,ts,symbol,side,qty,price,kind) VALUES (?,?,?,?,?,?,?,?)",
                                  (None, None, ts, r["symbol"], r["side"], delta, px, "portfolio"))
                if r["side"] == "sell":
                    self.conn.execute(
                        "INSERT INTO trades(intent_id,sid,strategy_hash,symbol,side,qty,entry_ts,entry_price,exit_ts,exit_price,exit_kind,pnl,r_multiple,narrative)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (None, r["sid"], r["strategy_hash"], r["symbol"], "long", delta, None, cost, ts, px, r["reason"],
                         (px - cost) * delta, None, f"Portfolio sleeve sale ({r['reason']}); Alpaca paper order {order['id']}"))
                audit.log(self.conn, "portfolio.fill", ACTOR, {"order_id": r["id"], "qty": delta, "price": px})
            self.conn.execute("UPDATE portfolio_orders SET broker_id=?, filled_qty=?, avg_price=?, status=? WHERE id=?",
                              (order["id"], qty, avg, status, r["id"]))

    def liquidate(self, reason: str):
        """Sell every sleeve share not already being sold (kill switch). Sells skip the entry checks."""
        selling: dict[tuple[str, str], float] = {}
        for r in db.rows(self.conn, "SELECT strategy_hash, symbol, qty, filled_qty FROM portfolio_orders"
                                    " WHERE side='sell' AND status IN ('submitting','submitted')"):
            k = (r["strategy_hash"], r["symbol"])
            selling[k] = selling.get(k, 0) + r["qty"] - r["filled_qty"]
        day = str(T.et(time.time()).date())
        for h, hold in sleeves.all_holdings(self.conn).items():
            sid = (db.row(self.conn, "SELECT sid FROM strategies WHERE hash=?", (h,)) or {}).get("sid", "portfolio")
            for sym, x in hold.items():
                qty = int(x.qty - selling.get((h, sym), 0))
                if qty <= 0:
                    continue
                oid = self.conn.insert(
                    "INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,ref_price,reason,decision,guard,status,mode,"
                    "created_wall,expires_wall,decided_by,decided_wall) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (h, sid, day, time.time(), sym, "sell", qty, x.cost, reason,
                     json.dumps([{"cond": f"{reason}: close everything", "ok": True, "value": True}]), "[]", "submitting", "system",
                     time.time(), time.time() + 3600, ACTOR, time.time()))
                cid = f"llama-p-{oid}-{h[:10]}"
                self.conn.execute("UPDATE portfolio_orders SET client_id=? WHERE id=?", (cid, oid))
                order = self.api.market(sym, qty, "sell", cid)
                self.conn.execute("UPDATE portfolio_orders SET broker_id=?, status='submitted' WHERE id=?", (order["id"], oid))
                audit.log(self.conn, "portfolio.liquidate", ACTOR, {"order_id": oid, "symbol": sym, "qty": qty, "reason": reason})
                self.record(db.row(self.conn, "SELECT * FROM portfolio_orders WHERE id=?", (oid,)), order)

    def record_external(self, order: dict, reason: str):
        """A close sent outside the desk (the watchdog's bulk flatten) sold sleeve shares: book it to the sleeves."""
        day = str(T.et(epoch(order["created_at"])).date())
        for h, hold in sleeves.all_holdings(self.conn).items():
            x = hold.get(order["symbol"])
            if not x or x.qty <= 0:
                continue
            cid = f"ext-{order['id']}-{h[:10]}"
            if db.row(self.conn, "SELECT id FROM portfolio_orders WHERE client_id=?", (cid,)):
                continue
            sid = (db.row(self.conn, "SELECT sid FROM strategies WHERE hash=?", (h,)) or {}).get("sid", "portfolio")
            self.conn.insert(
                "INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,ref_price,reason,decision,guard,status,mode,"
                "client_id,broker_id,created_wall,expires_wall,decided_by,decided_wall) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, sid, day, time.time(), order["symbol"], "sell", x.qty, x.cost, reason, "[]", "[]", "submitted", "system",
                 cid, order["id"], time.time(), time.time() + 3600, reason, time.time()))
        self.track()

    def halt(self, reason: str):
        self.conn.execute("UPDATE portfolio_orders SET status='void', note=? WHERE status IN ('pending','approved')", (reason,))
        for h, (_, s, _) in self.strats.items():
            sl = sleeves.load_state(self.conn, h)
            if sl and sl.status == "active":
                sl.status, sl.reason = "halted", reason
                sleeves.save_state(self.conn, h, s.id, sl)
                audit.log(self.conn, "portfolio.halted", ACTOR, {"hash": h, "reason": reason})
