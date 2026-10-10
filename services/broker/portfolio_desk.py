"""Portfolio desk: runs `kind: portfolio` strategies on the Alpaca paper account.

Once per session, at each strategy's `decide_at` (clamped before early closes), it reads fresh
quotes, asks core.portfolio.plan() for the day's orders, checks each one against the portfolio risk controls
`portfolio:`, and sends day market orders. Sleeve positions are held overnight; the intraday
flatten and the watchdog's deadline flatten skip them. The kill switch still closes everything.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

import httpx

from common import audit, db, funds, registry, sleeves, timeutil as T
from core import funds as F
from common.config import RiskConfig, mode
from core import portfolio as P
from .alpaca import epoch

log = logging.getLogger("portfolio")
ACTOR = "broker"
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}
HISTORY_DAYS = 200
DECIDE_BEFORE_CLOSE = 15 * 60  # decide no later than 15 min before the (possibly early) close
SUBMIT_BEFORE_CLOSE = 5 * 60  # and send orders no later than 5 min before it


class PortfolioDesk:
    def __init__(self, conn, api, risk: RiskConfig):
        self.conn, self.api, self.risk = conn, api, risk
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
        current = {s.hash: (r, s, False) for r, s in registry.runnable(self.conn, self.risk, kind="portfolio")
                   if r["status"] == "paper"}
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
            bars = self.api.bars(stale, "1Day", time.time() - HISTORY_DAYS * 86400, T.open_ts(day))
            with db.tx(self.conn):
                for sym, rows in bars.items():
                    for b in rows:
                        d = T.et(epoch(b["t"])).date()
                        if d < day:
                            self.conn.execute("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)"
                                              " ON CONFLICT(symbol,day) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,v=excluded.v",
                                              (sym, str(d), b["o"], b["h"], b["l"], b["c"], b["v"]))
            audit.log(self.conn, "portfolio.history", ACTOR, {"symbols": stale})
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
            sl = P.start(s, float(account["equity"]), str(day))
        hold, realized, _ = sleeves.holdings(self.conn, h)
        sl.realized = realized
        fund_status, fund_reason = funds.status(self.conn, s.id)
        if fund_status == "paused" and not retire:
            retire = True  # the strategy's fund stop (drawdown or owner pause) sells the sleeve
            log.info("portfolio %s: fund paused (%s), selling the sleeve", s.id, fund_reason)
        quotes = self.quotes(s.symbols, day)
        dp = P.plan(s, sl, quotes, hold, str(day), self.risk, retire=retire)
        status = "pending" if row["mode"] == "manual" else "approved"
        with db.tx(self.conn):
            for o in dp.orders:
                checks = self.checks(h, o, account, positions, intraday_symbols, day)
                ok = all(c.ok for c in checks)
                oid = self.conn.insert(
                    "INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,ref_price,reason,decision,guard,status,mode,"
                    "created_wall,expires_wall,decided_by,decided_wall,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (h, s.id, str(day), time.time(), o.symbol, o.side, o.qty, o.price, o.reason, json.dumps(o.decision),
                     json.dumps([c.as_dict() for c in checks]), status if ok else "auto_rejected", row["mode"], time.time(), cutoff,
                     None if status == "pending" or not ok else "paper-auto", None if status == "pending" or not ok else time.time(),
                     None if ok else ", ".join(c.name for c in checks if not c.ok)))
                self.conn.execute("UPDATE portfolio_orders SET client_id=? WHERE id=?", (f"llama-p-{oid}-{h[:10]}", oid))
            self.conn.execute("INSERT INTO portfolio_days(strategy_hash,day,records,event) VALUES (?,?,?,?)"
                              " ON CONFLICT(strategy_hash,day) DO UPDATE SET records=excluded.records,event=excluded.event",
                              (h, str(day), json.dumps(dp.records), dp.event))
            sleeves.save_state(self.conn, h, s.id, sl)
        audit.log(self.conn, "portfolio.decided", ACTOR, {"hash": h, "sid": s.id, "day": str(day), "session": sl.sessions,
                                                          "orders": len(dp.orders), "event": dp.event, "status": sl.status})

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
            o = P.Order(r["symbol"], r["side"], r["qty"], r["ref_price"], r["reason"])
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
