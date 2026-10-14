"""Owner-managed positions and reconciliation with trades made directly in Alpaca.

Commands (queued by the API, run by the paper broker in its loop):
  manual_open     place an owner trade (market/limit, optional stop and target)
  manual_close    close all or part of a position (sleeve symbols are sold out of the sleeves)
  manual_protect  set, change or remove the stop/target on a position
  manual_adopt    take ownership of a position opened outside llamaOS
  manual_cancel   cancel an open Alpaca order
  manual_hold     let a manual position skip the intraday flatten deadline
  reconcile_accept  apply the suggested fix for reconciliation differences now

Reconciliation runs every loop. A difference must be seen twice, at least SETTLE seconds apart, before
it is booked, so fills that are still being tracked are never double counted. With auto-reconcile on
(the default) closes and opens made in the Alpaca app are booked at their real fill prices.
"""
from __future__ import annotations

import json
import time

import httpx

from common import audit, db, manual as M, sleeves, timeutil as T
from common.config import mode
from .alpaca import epoch, iso, price

TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
SETTLE = 20          # seconds a difference must persist before it is booked
RECONCILE_EVERY = 10  # seconds between reconciliation passes
RETRY_FOR = 180      # seconds a command waits for cancels to land before it gives up
LOOKBACK = 7 * 86400  # how far back to search Alpaca for orders placed outside llamaOS


def live_orders(orders: list[dict]) -> list[dict]:
    """Orders that can still trade: a filled bracket parent is listed while its legs work, so use the legs."""
    out = []
    for o in orders:
        if o.get("status") not in TERMINAL:
            out.append(o)  # cancelling a parent cancels its legs
        else:
            out += [x for x in o.get("legs") or [] if x.get("status") not in TERMINAL]
    return out


class Retry(Exception):
    """Not ready yet (e.g. cancels still landing); the command stays pending and runs again next loop."""


class Rejected(Exception):
    pass


class ManualDesk:
    def __init__(self, broker):
        self.b = broker
        self.items: list[dict] = []
        self.seen: dict[str, float] = {}
        self.last_pass = 0.0
        self._closed: list[dict] | None = None

    @property
    def conn(self):
        return self.b.conn

    @property
    def api(self):
        return self.b.api

    # ---------------------------------------------------------------- helpers
    def pos(self, sym: str) -> dict | None:
        return next((p for p in self.b.positions if p["symbol"] == sym), None)

    def quote(self, sym: str) -> float | None:
        try:
            t = (self.api.snapshots([sym]).get(sym) or {}).get("latestTrade") or {}
            if t.get("p"):
                return float(t["p"])
        except httpx.HTTPError:
            pass
        r = db.row(self.conn, "SELECT c FROM daily WHERE symbol=? ORDER BY day DESC LIMIT 1", (sym,))
        return float(r["c"]) if r else None

    def known(self, oid: str) -> bool:
        return bool(db.row(self.conn, "SELECT 1 FROM paper_orders WHERE broker_id=?", (oid,))
                    or db.row(self.conn, "SELECT 1 FROM portfolio_orders WHERE broker_id=?", (oid,)))

    def closed_orders(self) -> list[dict]:
        """Filled Alpaca orders of the last week, legs flattened (one API call per reconcile pass)."""
        if self._closed is None:
            out = []
            for o in self.api.orders(status="closed", after=time.time() - LOOKBACK) or []:
                out.append(o)
                out += o.get("legs") or []
            self._closed = [o for o in out if float(o.get("filled_qty") or 0) > 0]
        return self._closed

    def external(self, sym: str, side: str, since: float) -> list[dict]:
        """Filled orders on `sym` llamaOS didn't place or doesn't track, oldest first."""
        rows = [o for o in self.closed_orders() if o["symbol"] == sym and o["side"] == side
                and epoch(o.get("filled_at") or o["created_at"]) >= since - 5 and not self.known(o["id"])]
        return sorted(rows, key=lambda o: o.get("filled_at") or o["created_at"])

    def open_intents(self) -> list[dict]:
        """Intents (strategy or manual) whose entry filled and that still hold shares by our books."""
        syms = [r["symbol"] for r in db.rows(self.conn,
                "SELECT DISTINCT i.symbol FROM intents i JOIN paper_orders o ON o.intent_id=i.id AND o.kind='entry'"
                " WHERE o.filled_qty>COALESCE((SELECT SUM(x.filled_qty) FROM paper_orders x WHERE x.intent_id=i.id AND x.kind!='entry'),0)")]
        return [r for r in (self.b.owner_intent(s) for s in syms) if r]

    def open_qty(self, iid: int) -> float:
        r = db.row(self.conn, "SELECT COALESCE(SUM(CASE WHEN kind='entry' THEN filled_qty ELSE -filled_qty END),0) q"
                              " FROM paper_orders WHERE intent_id=?", (iid,))
        return float(r["q"]) if r else 0.0

    def sleeve_holders(self, sym: str) -> list[tuple[str, float]]:
        out = [(h, x.qty) for h, hold in sleeves.all_holdings(self.conn).items() if (x := hold.get(sym)) and x.qty > 0]
        return sorted(out, key=lambda t: -t[1])

    def new_intent(self, sym, side, qty, entry, limit, stop, target, decision, status, actor, cid, body) -> int:
        now = time.time()
        iid = self.conn.insert(
            "INSERT INTO intents(ts,strategy_hash,sid,symbol,side,qty,entry,limit_price,stop,target,risk_usd,risk_pct,decision,signals,"
            "guard,status,mode,created_wall,expires_wall,decided_by,decided_wall) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(now), M.MANUAL, M.MANUAL, sym, side, qty, entry, limit, stop, target,
             qty * abs(entry - stop) if stop and entry else None, None, json.dumps(decision), "{}", "[]",
             status, "manual", now, now + 30 * 86400, actor, now))
        self.conn.execute("INSERT INTO paper_intents(intent_id,body,client_id) VALUES (?,?,?)",
                          (iid, json.dumps(body), cid or f"llama-adopt-{iid}"))
        return iid

    def clear_exits(self, sym: str, closing_side: str | None = None):
        """Cancel working orders on `sym` (only closing-side ones if given); Retry until Alpaca confirms."""
        def working():
            return [o for o in live_orders(self.api.orders() or []) if o["symbol"] == sym
                    and (closing_side is None or o["side"] == closing_side)]
        live = working()
        for o in live:
            try:
                self.api.cancel(o["id"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (404, 422):
                    raise
        if live and working():
            raise Retry(f"waiting for {sym} orders to cancel")

    def protection(self, sym: str) -> dict:
        """Current stop/target prices on `sym`: closing-side working orders and legs (entry parents excluded)."""
        pos = self.pos(sym)
        if not pos:
            return {}
        closing = "sell" if pos["side"] == "long" else "buy"
        out: dict = {}
        for o in self.b.open_orders:
            for x in [o, *(o.get("legs") or [])]:
                if x["symbol"] != sym or x["side"] != closing or x.get("status") in TERMINAL:
                    continue
                if x["type"] in ("stop", "stop_limit") and x.get("stop_price"):
                    out["stop"] = float(x["stop_price"])
                elif x["type"] == "limit" and x.get("limit_price"):
                    out["target"] = float(x["limit_price"])
        return out

    # ---------------------------------------------------------------- commands
    def handle(self, cmd: dict) -> str:
        p = json.loads(cmd["payload"] or "{}")
        actor = cmd["issued_by"] or "owner"
        fn = {"manual_open": self.open, "manual_close": self.close, "manual_protect": self.protect,
              "manual_adopt": self.adopt, "manual_cancel": self.cancel, "manual_hold": self.hold,
              "reconcile_accept": self.accept}[cmd["kind"]]
        try:
            msg = fn(p, cmd, actor)
        except (Retry, httpx.TransportError) as e:
            if time.time() - (cmd["ts"] or 0) > RETRY_FOR:
                return "failed: " + ("Alpaca didn't confirm the cancels in time" if isinstance(e, Retry) else "couldn't reach Alpaca") + "; try again"
            raise Retry(str(e)) from None
        except Rejected as e:
            return f"rejected: {e}"
        except httpx.HTTPStatusError as e:
            try:
                why = e.response.json().get("message")
            except ValueError:
                why = e.response.text[:200]
            return f"rejected by Alpaca ({e.response.status_code}): {why}"
        audit.log(self.conn, "paper." + cmd["kind"], actor, {**p, "result": msg})
        return msg

    def _symbol(self, p) -> str:
        sym = str(p.get("symbol") or "").upper().strip()
        if not sym or not sym.replace(".", "").isalnum() or len(sym) > 10:
            raise Rejected("bad symbol")
        return sym

    def open(self, p, cmd, actor) -> str:
        sym = self._symbol(p)
        side, typ, tif = p.get("side", "long"), p.get("type", "market"), p.get("tif", "day")
        qty = int(p.get("qty") or 0)
        limit = float(p["limit_price"]) if p.get("limit_price") not in (None, "") else None
        stop = float(p["stop"]) if p.get("stop") not in (None, "") else None
        target = float(p["target"]) if p.get("target") not in (None, "") else None
        risk = self.b.risk
        if mode() != "paper":
            raise Rejected("orders are disabled outside paper mode")
        if (db.kv_get(self.conn, "kill_switch") or {}).get("active"):
            raise Rejected("the kill switch is active")
        if qty <= 0 or side not in ("long", "short") or typ not in ("market", "limit") or tif not in ("day", "gtc"):
            raise Rejected("check side, type, time in force and quantity")
        if typ == "limit" and not limit:
            raise Rejected("a limit order needs a limit price")
        if side == "short" and not risk.allow_short:
            raise Rejected("shorting is off in the risk controls")
        if sym in self.b.sleeve_syms:
            raise Rejected(f"{sym} is held by a portfolio sleeve; trade it through that strategy")
        if self.pos(sym):
            raise Rejected(f"there is already a {sym} position; close or adjust it instead")
        if any(o["symbol"] == sym for o in self.b.open_orders):
            raise Rejected(f"{sym} already has a working order")
        if typ == "market" and not (getattr(self.b, "clock", None) or {}).get("is_open"):
            raise Rejected("the market is closed: use a limit order, or wait for the open")
        asset = self.api.asset(sym)
        if not asset.get("tradable") or (side == "short" and not asset.get("shortable")):
            raise Rejected(f"{sym} isn't {'shortable' if side == 'short' else 'tradable'} on Alpaca")
        ref = limit or self.quote(sym)
        if not ref:
            raise Rejected(f"no price for {sym}")
        sign = 1 if side == "long" else -1
        if stop is not None and (stop - ref) * sign >= 0:
            raise Rejected(f"the stop must be {'below' if side == 'long' else 'above'} the entry price")
        if target is not None and (target - ref) * sign <= 0:
            raise Rejected(f"the target must be {'above' if side == 'long' else 'below'} the entry price")
        acct = self.b.account_data
        eq = float(acct.get("equity") or 0)
        notional = qty * ref
        if notional > float(acct.get("buying_power") or 0):
            raise Rejected(f"${notional:,.0f} exceeds buying power")
        if eq and notional > risk.max_position * eq + 1e-6:
            raise Rejected(f"${notional:,.0f} is over the max position ({risk.max_position:.0%} of equity)")
        if eq and self.b.state().gross_exposure + notional > risk.max_gross_exposure * eq + 1e-6:
            raise Rejected(f"would exceed max gross exposure ({risk.max_gross_exposure:.2f}x equity)")
        cid = f"llama-m{cmd['id']}-open"
        body = {"symbol": sym, "qty": str(qty), "side": "buy" if side == "long" else "sell", "type": typ,
                "time_in_force": tif, "client_order_id": cid}
        if limit:
            body["limit_price"] = price(limit)
        if stop is not None and target is not None:
            body.update(order_class="bracket", take_profit={"limit_price": price(target)}, stop_loss={"stop_price": price(stop)})
        elif stop is not None:
            body.update(order_class="oto", stop_loss={"stop_price": price(stop)})
        elif target is not None:
            body.update(order_class="oto", take_profit={"limit_price": price(target)})
        with db.tx(self.conn):
            iid = self.new_intent(sym, side, qty, ref, limit, stop, target, {"manual": True, "type": typ, "tif": tif},
                                  "submitting", actor, cid, body)
        try:
            order = self.api.submit(body)
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500 and e.response.status_code != 429:
                self.conn.execute("UPDATE intents SET status='broker_rejected', reason=? WHERE id=?",
                                  (f"Alpaca HTTP {e.response.status_code}", iid))
            raise
        self.conn.execute("UPDATE paper_intents SET broker_id=?, submitted_at=? WHERE intent_id=?", (order["id"], time.time(), iid))
        self.b.track_order(order, iid, "entry")
        self.conn.execute("UPDATE intents SET status='submitted' WHERE id=?", (iid,))
        return f"{'buy' if side == 'long' else 'short'} {qty} {sym} sent ({typ}{f' @ {limit:g}' if limit else ''})"

    def close(self, p, cmd, actor) -> str:
        sym = self._symbol(p)
        pos = self.pos(sym)
        if not pos:
            raise Rejected(f"no {sym} position at Alpaca")
        total = abs(float(pos["qty"]))
        qty = float(p.get("qty") or total)
        if qty <= 0 or qty > total + 1e-9:
            raise Rejected(f"quantity must be between 1 and {total:g}")
        if sym in self.b.sleeve_syms:
            return self.sleeve_sell(sym, qty, cmd, actor)
        if "_prot" not in p:  # remember the protection before cancelling it, to restore it on a partial close
            p["_prot"] = self.protection(sym)
            self.conn.execute("UPDATE commands SET payload=? WHERE id=?", (json.dumps(p), cmd["id"]))
        self.clear_exits(sym)
        ref = self.b.owner_intent(sym)
        closing = "sell" if pos["side"] == "long" else "buy"
        if qty >= total - 1e-9:
            order = self.api.close_position(sym)
        else:
            order = self.api.submit({"symbol": sym, "qty": str(int(qty)), "side": closing, "type": "market",
                                     "time_in_force": "day", "client_order_id": f"llama-m{cmd['id']}-close"})
        if order:
            self.b.track_order(order, ref["id"] if ref else None, "manual")
        msg = f"closing {qty:g} of {total:g} {sym}"
        if qty < total and p["_prot"]:
            self.conn.execute("INSERT INTO commands(ts, kind, payload, issued_by) VALUES (?,?,?,?)",
                              (time.time(), "manual_protect", json.dumps({"symbol": sym, **p["_prot"], "_after": order and order.get("id")}), actor))
            msg += "; the stop/target is re-placed on the rest once it fills"
        if not (getattr(self.b, "clock", None) or {}).get("is_open"):
            msg += " (queued for the next open)"
        return msg

    def sleeve_sell(self, sym, qty, cmd, actor) -> str:
        if db.row(self.conn, "SELECT id FROM portfolio_orders WHERE symbol=? AND status IN ('submitting','submitted')", (sym,)):
            raise Rejected(f"a portfolio order for {sym} is already working")
        holders = self.sleeve_holders(sym)
        order = self.api.submit({"symbol": sym, "qty": str(int(qty)), "side": "sell", "type": "market", "time_in_force": "day",
                                 "client_order_id": f"llama-m{cmd['id']}-sleeve"})
        self.book_sleeve(sym, qty, holders, order, "manual close", actor, status="submitted")
        return f"selling {qty:g} {sym} out of {', '.join(self.sid(h) for h, _ in holders)} (the strategy may buy back at its next decision unless retired)"

    def sid(self, h: str) -> str:
        return (db.row(self.conn, "SELECT sid FROM strategies WHERE hash=?", (h,)) or {}).get("sid") or h[:10]

    def book_sleeve(self, sym, qty, holders, order, reason, actor, status):
        """Split one sell order across the sleeves holding `sym` (largest first) as portfolio_orders rows."""
        left, now = qty, time.time()
        day = str(T.et(now).date())
        for h, held in holders:
            take = min(left, held)
            if take <= 0:
                break
            left -= take
            r = {"strategy_hash": h, "sid": self.sid(h), "symbol": sym, "side": "sell", "qty": take, "reason": reason,
                 "filled_qty": 0.0, "avg_price": None}
            r["id"] = self.conn.insert(
                "INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,ref_price,reason,decision,guard,status,mode,"
                "client_id,broker_id,created_wall,expires_wall,decided_by,decided_wall) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, r["sid"], day, now, sym, "sell", int(round(take)), float(order.get("filled_avg_price") or 0) or None, reason,
                 "[]", "[]", status, "paper", f"{order['id']}-{h[:10]}", order["id"], now, now + 3600, actor, now))
            if status == "filled":
                self.b.desk.record(r, order)

    def protect(self, p, cmd, actor) -> str:
        sym = self._symbol(p)
        pos = self.pos(sym)
        if not pos:
            if p.get("_after"):
                raise Retry("waiting for the partial close to fill")
            raise Rejected(f"no {sym} position at Alpaca")
        if sym in self.b.sleeve_syms:
            raise Rejected("portfolio sleeves don't use stops or targets")
        if p.get("_after") and any(o["id"] == p["_after"] for o in self.b.open_orders):
            raise Retry("waiting for the partial close to fill")
        stop = float(p["stop"]) if p.get("stop") not in (None, "") else None
        target = float(p["target"]) if p.get("target") not in (None, "") else None
        long_ = pos["side"] == "long"
        last = float(pos.get("current_price") or 0)
        if stop is not None and last and (stop >= last if long_ else stop <= last):
            raise Rejected(f"the stop must be {'below' if long_ else 'above'} the current price {last:g}")
        if target is not None and last and (target <= last if long_ else target >= last):
            raise Rejected(f"the target must be {'above' if long_ else 'below'} the current price {last:g}")
        closing = "sell" if long_ else "buy"
        if any(o["symbol"] == sym and o["side"] != closing for o in live_orders(self.b.open_orders)):
            raise Rejected(f"{sym} has a working entry order; wait for it to fill or cancel it")
        self.clear_exits(sym, closing)
        ref = self.b.owner_intent(sym)
        if ref:
            self.conn.execute("UPDATE intents SET stop=?, target=? WHERE id=?", (stop, target, ref["id"]))
        if stop is None and target is None:
            return f"removed the stop and target on {sym}"
        qty = str(int(abs(float(pos["qty"]))))
        body = {"symbol": sym, "qty": qty, "side": closing, "time_in_force": "gtc", "client_order_id": f"llama-m{cmd['id']}-protect"}
        if stop is not None and target is not None:
            body.update(type="limit", limit_price=price(target), order_class="oco",
                        take_profit={"limit_price": price(target)}, stop_loss={"stop_price": price(stop)})
        elif stop is not None:
            body.update(type="stop", stop_price=price(stop))
        else:
            body.update(type="limit", limit_price=price(target))
        order = self.api.submit(body)
        self.b.track_order(order, ref["id"] if ref else None, "protect_" + ("stop" if body["type"] == "stop" else "target"))
        parts = [f"stop {stop:g}" if stop is not None else "", f"target {target:g}" if target is not None else ""]
        return f"{sym}: {' and '.join(x for x in parts if x)} set (good till cancelled)"

    def adopt(self, p, cmd, actor) -> str:
        sym = self._symbol(p)
        pos = self.pos(sym)
        if not pos:
            raise Rejected(f"no {sym} position at Alpaca")
        if sym in self.b.sleeve_syms or self.b.owner_intent(sym):
            raise Rejected(f"{sym} is already managed by llamaOS")
        self.adopt_position(pos, actor)
        return f"{sym} is now a manual position"

    def adopt_position(self, pos: dict, actor: str):
        sym, qty, avg = pos["symbol"], abs(float(pos["qty"])), float(pos["avg_entry_price"])
        with db.tx(self.conn):
            iid = self.new_intent(sym, pos["side"], int(qty), avg, None, None, None, {"manual": True, "adopted": True},
                                  "filled", actor, None, {"adopted": pos})
        now = iso(time.time())
        self.b.track_order({"id": f"adopt-{iid}", "symbol": sym, "side": "buy" if pos["side"] == "long" else "sell",
                            "qty": str(qty), "filled_qty": str(qty), "filled_avg_price": str(avg), "status": "filled",
                            "type": "market", "created_at": now, "filled_at": now}, iid, "entry")

    def cancel(self, p, cmd, actor) -> str:
        oid = str(p.get("order_id") or "")
        o = next((x for x in self.b.open_orders if x["id"] == oid), None)
        if not o:
            raise Rejected("that order is no longer open")
        self.api.cancel(oid)
        return f"cancel requested for {o['side']} {o['qty']} {o['symbol']}"

    def hold(self, p, cmd, actor) -> str:
        sym = self._symbol(p)
        on = bool(p.get("on"))
        ref = self.b.owner_intent(sym)
        if on and not (ref and ref["strategy_hash"] == M.MANUAL):
            raise Rejected("only manual positions can be held overnight; strategy positions follow their strategy")
        M.set_hold(self.conn, sym, on)
        return f"{sym} {'is held overnight' if on else 'closes at the flatten deadline'}"

    def accept(self, p, cmd, actor) -> str:
        sym = (p.get("symbol") or "").upper() or None
        n = self.reconcile(force=True, only=sym, actor=actor)
        return f"booked {n} reconciliation fix{'es' if n != 1 else ''}" if n else "nothing to fix"

    # ---------------------------------------------------------------- reconciliation
    def ready(self, key: str, force: bool) -> bool:
        first = self.seen.setdefault(key, time.time())
        return force or time.time() - first >= SETTLE

    def reconcile(self, force: bool = False, only: str | None = None, actor: str = "reconcile") -> int:
        """Compare our books with Alpaca, book what can be explained, and publish the rest for the dashboard."""
        if not force and time.time() - self.last_pass < RECONCILE_EVERY:
            return 0
        self.last_pass = time.time()
        self._closed = None
        auto = M.auto(self.conn) or force
        pos = {p["symbol"]: p for p in self.b.positions}
        open_syms = {o["symbol"] for o in self.b.open_orders}
        live_ids = {x["id"] for o in self.b.open_orders for x in [o, *(o.get("legs") or [])]}
        items, fixed, keys = [], 0, set()

        def want(key, sym):
            keys.add(key)
            return auto and (only is None or only == sym) and self.ready(key, force)

        # 1. Portfolio sleeves: shares our books hold that Alpaca no longer has.
        expected: dict[str, float] = {}
        for hold in sleeves.all_holdings(self.conn).values():
            for sym, x in hold.items():
                expected[sym] = expected.get(sym, 0) + x.qty
        busy = {r["symbol"] for r in db.rows(self.conn, "SELECT DISTINCT symbol FROM portfolio_orders WHERE status IN ('submitting','submitted')")}
        for sym, q in expected.items():
            have = float(pos[sym]["qty"]) if sym in pos else 0.0
            if sym in busy or sym in open_syms or abs(have - q) < 1e-6 or (not q and sym not in pos):
                continue
            item = {"symbol": sym, "kind": "sleeve", "llama": q, "alpaca": have}
            if have < q:
                item["fix"] = f"book the sale of {q - have:g} shares closed in Alpaca"
                if want(f"sleeve:{sym}:{q}:{have}", sym):
                    fixed += self.book_sleeve_sale(sym, q - have, actor)
                    continue
            else:
                item["note"] = "Alpaca holds more shares than the sleeves: extra shares were bought outside llamaOS"
            items.append(item)

        # 2. Intraday/manual positions our books hold that Alpaca closed.
        for ref in self.open_intents():
            sym = ref["symbol"]
            if sym in pos or sym in open_syms or sym in expected:
                continue
            if any(r["broker_id"] in live_ids for r in db.rows(self.conn, "SELECT broker_id FROM paper_orders WHERE intent_id=?", (ref["id"],))):
                continue  # one of our own orders is still working; its fill closes the books
            q = self.open_qty(ref["id"])
            item = {"symbol": sym, "kind": "closed_outside", "llama": q, "alpaca": 0.0, "sid": ref["sid"],
                    "fix": f"book the close of {q:g} shares made in Alpaca"}
            if want(f"closed:{ref['id']}", sym):
                self.book_intraday_close(ref, q)
                fixed += 1
                continue
            items.append(item)

        # 3. Positions nobody owns (opened in Alpaca).
        for sym, p in pos.items():
            if sym in expected or self.b.owner_intent(sym) or sym in open_syms:
                continue
            item = {"symbol": sym, "kind": "unowned_position", "llama": 0.0, "alpaca": float(p["qty"]),
                    "fix": "adopt it as a manual position"}
            if want(f"adopt:{sym}:{p['qty']}", sym):
                self.adopt_position(p, actor)
                fixed += 1
                continue
            items.append(item)

        # 4. Open orders placed in Alpaca: track them so their fills are booked.
        for o in self.b.open_orders:
            if o.get("client_order_id", "").startswith("llama-") or self.known(o["id"]):
                continue
            sym = o["symbol"]
            item = {"symbol": sym, "kind": "unknown_order", "order_id": o["id"], "side": o["side"], "qty": float(o["qty"]),
                    "fix": "track it in llamaOS"}
            ref = self.b.owner_intent(sym)
            closing = ref and o["side"] == ("sell" if ref["side"] == "long" else "buy")
            if sym in expected:
                if o["side"] != "sell":
                    item.update(fix=None, note="a buy on a sleeve symbol placed outside llamaOS; cancel it or let it fill")
                elif want(f"order:{o['id']}", sym):
                    self.book_sleeve(sym, float(o["qty"]), self.sleeve_holders(sym), o, "manual close (Alpaca)", actor, "submitted")
                    fixed += 1
                    continue
            elif closing or (not ref and sym not in pos):
                if want(f"order:{o['id']}", sym):
                    if closing:
                        self.b.track_order(o, ref["id"], "manual")
                    else:
                        side = "long" if o["side"] == "buy" else "short"
                        lim = float(o["limit_price"]) if o.get("limit_price") else None
                        with db.tx(self.conn):
                            iid = self.new_intent(sym, side, int(float(o["qty"])), lim or self.quote(sym) or 0, lim, None, None,
                                                  {"manual": True, "adopted_order": True}, "submitted", actor,
                                                  o.get("client_order_id") or f"llama-adopt-o{o['id']}", {"adopted_order": o["id"]})
                        self.conn.execute("UPDATE paper_intents SET broker_id=?, submitted_at=? WHERE intent_id=?", (o["id"], time.time(), iid))
                        self.b.track_order(o, iid, "entry")
                    fixed += 1
                    continue
            else:
                item.update(fix=None, note="an order that adds to a position llamaOS manages; cancel it or let it fill")
            items.append(item)

        self.seen = {k: v for k, v in self.seen.items() if k in keys}
        self.items = items
        if fixed:
            audit.log(self.conn, "reconcile.booked", actor, {"fixes": fixed, "auto": not force})
        return fixed

    def book_sleeve_sale(self, sym: str, qty: float, actor: str) -> int:
        holders = self.sleeve_holders(sym)
        last = db.row(self.conn, "SELECT MAX(ts) t FROM fills WHERE symbol=? AND kind='portfolio'", (sym,))
        since = float(last["t"] or 0) if last else 0.0
        left = qty
        for o in self.external(sym, "sell", since):
            if left <= 1e-9:
                break
            take = min(left, float(o["filled_qty"]))
            self.book_sleeve(sym, take, holders, {**o, "filled_qty": str(take)}, "manual close (Alpaca)", actor, "filled")
            holders = self.sleeve_holders(sym)
            left -= take
        if left > 1e-9:  # no matching Alpaca order (older than the lookback, or a corporate action)
            px = self.quote(sym) or sleeves.all_holdings(self.conn)[holders[0][0]][sym].cost
            now = iso(time.time())
            self.book_sleeve(sym, left, holders, {"id": f"recon-{sym}-{int(time.time())}", "status": "filled", "filled_qty": str(left),
                                                   "filled_avg_price": str(px), "created_at": now, "filled_at": now},
                             "reconciled (no Alpaca order found)", actor, "filled")
        return 1

    def book_intraday_close(self, ref: dict, qty: float):
        closing = "sell" if ref["side"] == "long" else "buy"
        left = qty
        for o in self.external(ref["symbol"], closing, float(ref["ts"] or 0)):
            if left <= 1e-9:
                break
            self.b.track_order(o, ref["id"], "manual")
            left -= float(o["filled_qty"])
        if left > 1e-9:
            px = self.quote(ref["symbol"]) or ref["entry"]
            now = iso(time.time())
            self.b.track_order({"id": f"recon-{ref['id']}-{int(time.time())}", "symbol": ref["symbol"], "side": closing,
                                "qty": str(left), "filled_qty": str(left), "filled_avg_price": str(px), "status": "filled",
                                "type": "market", "created_at": now, "filled_at": now}, ref["id"], "reconciled")
