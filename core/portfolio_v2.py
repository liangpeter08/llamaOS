"""DSL 0.2 portfolio engine: per-symbol daily rules plus portfolio overlays. Pure functions, no I/O.

Same contract as core.portfolio.plan(): the backtester and the paper desk pass identical inputs and get
identical decisions. 0.2 adds:

    market inputs   index or basket returns computed by the caller (None = unknown)
    macro overlay   a session decline trims every holding once per session, pauses buys, waits for a
                    later positive session, then rebuilds in steps toward max_holding
                    (phases: normal -> reduction_pending -> awaiting_positive_session -> rebuilding -> normal)
    rotation        a positive driver-basket return trims a holdings group proportionally to locked cash
    resolution      full exits win; partial reductions at one snapshot merge to the LARGEST fraction;
                    a reduction suppresses that symbol's buys; macro rebuild vs daily add takes the larger
    cash            buys are limited to the sleeve's unreserved cash: capital x max_holding + realized
                    - cost of shares held - locked rotation proceeds (+ owner releases)
    shadow mode     overlays are evaluated and logged, but orders and buy pauses come from the daily rules only

`observe()` handles intraday observations (macro overlays with on: every_fresh_observation); `plan_v2()` is
the daily decision. Both are idempotent per session: a repeated crossing never trims twice.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from common.config import RiskConfig
from dsl.portfolio import PortfolioStrategy

from .portfolio import (DayPlan, FULL, Holding, Order, Quote, Sleeve, _EVENT, _cond, _explain, _first_match, _none,
                        sleeve_value)

EVENT_TEXT = {**_EVENT, "macro_liquidation": "macro overlay: liquidate the sleeve"}
OPS = {"lte": lambda a, b: a <= b, "lt": lambda a, b: a < b, "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b}
OP_TEXT = {"lte": "<=", "lt": "<", "gt": ">", "gte": ">="}


@dataclass
class Ctx:
    """What the overlays decided at one snapshot, applied per symbol."""
    reductions: dict[str, list[tuple[float, str, str]]] = field(default_factory=dict)  # sym -> [(fraction, reason, overlay)]
    targets: dict[str, tuple[int, str]] = field(default_factory=dict)  # sym -> (share target, overlay): retained macro reduction
    block_all: list[str] = field(default_factory=list)  # reasons no symbol may buy
    block_syms: dict[str, list[str]] = field(default_factory=dict)
    rebuild: tuple[str, float, set[str]] | None = None  # (overlay, step, eligible symbols)
    liquidate: str = ""
    events: list[dict] = field(default_factory=list)

    def reduce(self, sym, fraction, reason, overlay):
        self.reductions.setdefault(sym, []).append((fraction, reason, overlay))

    def blocked(self, sym) -> list[str]:
        return self.block_all + self.block_syms.get(sym, [])


def _state(sl: Sleeve, overlay_id: str) -> dict:
    return sl.overlays.setdefault(overlay_id, {"phase": "normal"})


def locked_cash(sl: Sleeve, locked_usd: float) -> float:
    return max(0.0, locked_usd - sl.released_usd)


def spendable(s: PortfolioStrategy, sl: Sleeve, held: dict[str, Holding], locked_usd: float) -> float:
    """Unreserved sleeve cash: the sleeve's peak budget plus realized P&L, minus the cost of what it holds and locked cash."""
    cost = sum(h.qty * h.cost for h in held.values())
    return sl.capital_usd * s.max_holding + sl.realized - cost - locked_cash(sl, locked_usd)


# ---------------------------------------------------------------- overlays
def _macro(s, o, sl, held, x, day, working, open_reductions, enforce, decision, ctx: Ctx):
    st = _state(sl, o.id)
    thr = s.params[o.threshold_param]
    inp = s.v2.inputs[o.metric]
    if x is None:
        if inp.required:
            ctx.block_all.append(f"{o.metric} unavailable (required input)")
            ctx.events.append({"kind": "input_missing", "overlay": o.id, "input": o.metric})
    hit = x is not None and OPS[o.operator](x, thr)
    if hit and st.get("last_trigger_day") != day:
        st.update(last_trigger_day=day, trigger_day=day, trigger_value=round(x, 6))
        ev = {"kind": "macro_trigger", "overlay": o.id, "input": o.metric, "value": round(x, 6), "threshold": thr,
              "snapshot": "daily_decision" if decision else "observation"}
        if o.action == "liquidate_sleeve":
            ctx.liquidate = o.id
        elif not sl.funded:
            if o.before_initial_buy:
                st.update(phase="awaiting_positive_session", eligible=[], targets={}, initial_blocked=True)
                ev["effect"] = "initial funding blocked until a later positive session"
            else:
                ev["effect"] = "ignored before the initial buy"
        else:
            f = s.params[o.trim_param]
            targets = {sym: h.qty - math.floor(h.qty * f + 1e-9) for sym, h in held.items() if h.qty > 0}
            st.update(phase="reduction_pending", eligible=sorted(targets), targets=targets, initial_blocked=False)
            for sym in targets:
                ctx.reduce(sym, f, "macro_trim", o.id)
            ev["effect"] = f"trim {f:.0%} of current shares, pause buys"
        ctx.events.append(ev)
    elif hit:
        ctx.events.append({"kind": "macro_repeat", "overlay": o.id, "value": round(x, 6), "effect": "no second trim this session"})
    if st["phase"] == "reduction_pending":
        if not enforce:
            st["phase"] = "awaiting_positive_session"  # shadow: nothing was sold, so nothing to reconcile
        else:
            left = {sym: t for sym, t in st.get("targets", {}).items()
                    if held.get(sym, Holding()).qty - working.get(sym, 0) > t}
            for sym, t in left.items():
                ctx.targets[sym] = (t, o.id)
            if not left and not open_reductions:
                st["phase"] = "awaiting_positive_session"
                ctx.events.append({"kind": "macro_reduced", "overlay": o.id})
    if st["phase"] in ("reduction_pending", "awaiting_positive_session"):
        later = st.get("trigger_day") is not None and day > st["trigger_day"]
        if (decision and st["phase"] == "awaiting_positive_session" and later and x is not None and x > 0
                and not open_reductions):
            if st.get("initial_blocked"):
                st.update(phase="normal", initial_blocked=False)
                ctx.events.append({"kind": "macro_recovery", "overlay": o.id, "value": round(x, 6),
                                   "effect": "initial funding resumes at 100% of the allocation"})
            elif st.get("eligible"):
                st.update(phase="rebuilding", rebuild_from=day)
                ctx.events.append({"kind": "macro_recovery", "overlay": o.id, "value": round(x, 6),
                                   "effect": f"rebuild {s.params[o.rebuild_step_param]:.0%} of the original allocation per session"})
            else:
                st["phase"] = "normal"
        else:
            ctx.block_all.append(f"{o.id}: " + ("reduction pending" if st["phase"] == "reduction_pending"
                                                else "waiting for a later positive session"))
    if st["phase"] == "rebuilding" and decision and o.rebuild_step_param:
        ctx.rebuild = (o.id, s.params[o.rebuild_step_param], set(st.get("eligible", [])))


def _rotation(s, o, sl, held, x, day, ctx: Ctx):
    st = _state(sl, o.id)
    if st.get("last_day") == day:
        return
    st["last_day"] = day
    if o.skip_initial_funding_session and not sl.funded:
        ctx.events.append({"kind": "rotation_skipped", "overlay": o.id, "why": "initial funding session"})
        return
    if x is None:
        for sym in o.symbols:
            ctx.block_syms.setdefault(sym, []).append(f"{o.metric} unavailable (required at the daily decision)")
        ctx.events.append({"kind": "input_missing", "overlay": o.id, "input": o.metric})
        return
    thr = o.threshold if o.threshold is not None else 0.0
    if not OPS[o.operator](x, thr):
        return
    f = min(max(x, 0.0) / s.params[o.anchor_return_param] * s.params[o.anchor_trim_param], o.max_fraction)
    if f <= 0:
        return
    for sym in o.symbols:
        ctx.reduce(sym, f, "rotation_trim", o.id)
        ctx.block_syms.setdefault(sym, []).append(f"{o.id}: reduction this session")
    st.update(last_fraction=round(f, 6), last_value=round(x, 6))
    ctx.events.append({"kind": "rotation", "overlay": o.id, "input": o.metric, "value": round(x, 6), "fraction": round(f, 6),
                       "group": o.target_group})


def _overlays(s, sl, held, inputs, day, working, open_reductions, enforce, decision) -> Ctx:
    ctx = Ctx()
    for o in s.overlays:
        x = inputs.get(o.metric)
        x = None if x is None else round(x, 10)  # exact boundaries: -2% is -2%, not -2.0000000000000018%
        if o.kind == "macro":
            if decision or o.on == "every_fresh_observation":
                _macro(s, o, sl, held, x, day, working, open_reductions, enforce, decision, ctx)
        elif decision:
            _rotation(s, o, sl, held, x, day, ctx)
    return ctx


# ---------------------------------------------------------------- intraday observation
def observe(s: PortfolioStrategy, sl: Sleeve, inputs: dict[str, float | None], holdings: dict[str, Holding],
            prices: dict[str, float], day: str, working: dict[str, int] | None = None, enforce: bool = True) -> DayPlan:
    """One fresh intraday observation: only macro overlays with on: every_fresh_observation react."""
    if sl.status != "active" or s.v2 is None or not any(o.kind == "macro" and o.on == "every_fresh_observation" for o in s.overlays):
        return DayPlan([], [])
    working = working or {}
    held = {sym: holdings.get(sym, Holding()) for sym in s.symbols}
    before = copy.deepcopy(sl.overlays)
    ctx = Ctx()
    for o in s.overlays:
        if o.kind == "macro" and o.on == "every_fresh_observation":
            x = inputs.get(o.metric)
            x = None if x is None else round(x, 10)
            st = _state(sl, o.id)
            # Only a new crossing acts here; recovery and rebuilding wait for the daily decision.
            if x is not None and OPS[o.operator](x, s.params[o.threshold_param]) and st.get("last_trigger_day") != day:
                _macro(s, o, sl, held, x, day, working, True, enforce, False, ctx)
    if not ctx.events:
        sl.overlays = before
        return DayPlan([], [])
    orders, shadow = [], []
    if ctx.liquidate:
        orders = [Order(sym, "sell", h.qty - working.get(sym, 0), prices.get(sym, h.cost), "macro_liquidation",
                        [{"cond": EVENT_TEXT["macro_liquidation"], "ok": True, "value": True}], overlay=ctx.liquidate)
                  for sym, h in held.items() if h.qty - working.get(sym, 0) > 0]
        if enforce:
            sl.status, sl.reason = "finished", "macro_liquidation"
    else:
        for sym, reds in ctx.reductions.items():
            h = held[sym]
            f, reason, oid = max(reds)
            qty = min(math.floor(h.qty * f + 1e-9), h.qty - working.get(sym, 0))
            if qty > 0 and sym in prices:
                orders.append(Order(sym, "sell", qty, prices[sym], reason,
                                    [{"cond": f"{oid}: {ctx.events[0].get('input')} = {ctx.events[0].get('value'):+.2%}, "
                                              f"sell {f:.0%} of the {h.qty} shares held", "ok": True, "value": True}], overlay=oid))
    if not enforce:
        shadow = [{"symbol": o.symbol, "would": f"sell {o.qty}", "reason": o.reason} for o in orders]
        orders = []
    return DayPlan(orders, [], "macro_liquidation" if ctx.liquidate and enforce else "", ctx.events, shadow)


# ---------------------------------------------------------------- daily decision
def plan_v2(s: PortfolioStrategy, sl: Sleeve, quotes: dict[str, Quote], holdings: dict[str, Holding], day: str,
            risk: RiskConfig, retire: bool = False, inputs: dict[str, float | None] | None = None,
            working: dict[str, int] | None = None, open_reductions: bool = False, enforce: bool = True,
            locked_usd: float = 0.0, blocks: list[str] | tuple = ()) -> DayPlan:
    """The 0.2 daily decision. `working` = sell shares already working at the broker per symbol; `open_reductions` =
    a macro reduction order is still unreconciled; `blocks` = buy blocks from monitored assumptions (enforce only)."""
    inputs, working = inputs or {}, working or {}
    k = sl.sessions
    prices = {sym: q.price for sym, q in quotes.items()}
    held = {sym: holdings.get(sym, Holding()) for sym in s.symbols}

    ctx = _overlays(s, sl, held, inputs, day, working, open_reductions, enforce, True)
    event = ""
    if retire:
        event = "retired"
    elif all(sym in prices for sym, h in held.items() if h.qty):
        value = sleeve_value(sl, held, prices)
        sl.peak = max(sl.peak, value)
        dd = s.max_drawdown(risk)
        if sl.peak > 0 and value / sl.peak - 1 <= -dd:
            event = "max_drawdown"
    if not event and ((sl.funded and k >= s.hold_sessions - 1) or (s.end_date and day >= s.end_date.isoformat())):
        event = "horizon"
    if not event and ctx.liquidate and enforce:
        event = "macro_liquidation"

    orders: list[Order] = []
    records: list[dict] = []
    shadow: list[dict] = []
    if event:
        for sym, h in held.items():
            qty = h.qty - working.get(sym, 0)
            if qty > 0:
                orders.append(Order(sym, "sell", qty, prices.get(sym, h.cost), event,
                                    [{"cond": EVENT_TEXT[event], "ok": True, "value": True}]))
            records.append({"symbol": sym, "mode": sl.symbols[sym].mode, "action": event})
        sl.status = "stopped" if event == "max_drawdown" else "finished"
        sl.reason = event
    else:
        cash = spendable(s, sl, held, locked_usd)
        real = Ctx(block_all=list(blocks)) if not enforce else ctx
        if enforce:
            ctx.block_all += list(blocks)
        if not enforce:  # shadow: what the overlays would do, on a copy of the symbol states
            hypo = copy.deepcopy(sl.symbols)
            would, _ = _symbols(s, sl, hypo, held, quotes, k, ctx, working, cash)
            actual, records = _symbols(s, sl, sl.symbols, held, quotes, k, real, working, cash)
            a = {o.symbol: (o.side, o.qty, o.reason) for o in actual}
            for o in would:
                if a.get(o.symbol) != (o.side, o.qty, o.reason):
                    shadow.append({"symbol": o.symbol, "would": f"{o.side} {o.qty}", "reason": o.reason,
                                   "actual": " ".join(map(str, a[o.symbol][:2])) if o.symbol in a else "none"})
            wsyms = {o.symbol for o in would}
            shadow += [{"symbol": sym, "would": "none", "reason": "overlay suppresses it", "actual": f"{x[0]} {x[1]}"}
                       for sym, x in a.items() if sym not in wsyms]
            orders = actual
        else:
            orders, records = _symbols(s, sl, sl.symbols, held, quotes, k, ctx, working, cash)
        _rebuild_progress(s, sl, held, quotes, orders, ctx)
        if not sl.funded and any(o.side == "buy" and o.reason == "initial" for o in orders):
            sl.funded, sl.funded_day = True, day
    for o in s.overlays:  # full exits remove a symbol from macro eligibility for good
        st = sl.overlays.get(o.id)
        if st and st.get("eligible"):
            st["eligible"] = [sym for sym in st["eligible"] if sl.symbols[sym].mode not in ("closed",)]
    if sl.funded or event:
        sl.sessions = k + 1
    sl.last_day = day
    orders.sort(key=lambda o: o.side != "sell")
    for r in records:
        r["blocked"] = ctx.blocked(r["symbol"]) or None
    return DayPlan(orders, records, event, ctx.events, shadow)


def _symbols(s, sl, states, held, quotes, k, ctx: Ctx, working, cash):
    orders, records = [], []
    budget = [cash]
    for sym in s.symbols:
        st, h, q = states[sym], held[sym], quotes.get(sym)
        rec = {"symbol": sym, "mode": st.mode}
        if q is None or not q.prior_close or q.price <= 0:
            rec.update(action="skip", note="no fresh price (fails closed)")
            records.append(rec)
            continue
        vals = {
            "day_change": round(q.price / q.prior_close - 1, 10),  # 105/100-1 must equal +5% exactly
            "holding": h.qty * q.price / st.base_usd if st.base_usd else 0.0,
            "sessions_held": float(k),
            "gain": q.price / h.cost - 1 if h.qty and h.cost else None,
            "_sym": sym, "_quotes": quotes,  # context for daily functions and peer()
        }
        rec.update(day_change=round(vals["day_change"], 6), holding=round(vals["holding"], 4))
        o = _decide(s, st, h, q, vals, rec, ctx, working.get(sym, 0), budget)
        if o:
            orders.append(o)
        rec["mode_after"] = st.mode
        records.append(rec)
    return orders, records


def _decide(s: PortfolioStrategy, st, h: Holding, q: Quote, vals: dict, rec: dict, ctx: Ctx, working: int,
            budget: list[float]) -> Order | None:
    px, sym = q.price, rec["symbol"]
    avail = max(0, h.qty - working)
    match = _first_match(s, vals)
    rec["rule"] = match[0].when.src if match else None
    decision = match[1] if match else []

    def sell(qty: int, why: str, dec=None, reserve: int = 0, overlay: str = "") -> Order | None:
        qty = min(qty, avail)
        rec["action"] = f"sell {qty}" if qty else "none"
        return Order(sym, "sell", qty, px, why, dec if dec is not None else decision, min(reserve, qty), overlay) if qty else None

    def buy_usd(usd: float, why: str, dec=None) -> Order | None:
        usd = max(min(usd, budget[0]), 0.0)
        qty = math.floor(usd / px + 1e-9)
        if not qty:
            rec["action"] = "none"
            if budget[0] < px:
                rec["note"] = "deferred: no unreserved sleeve cash"
            return None
        budget[0] -= qty * px
        rec["action"] = f"buy {qty}"
        return Order(sym, "buy", qty, px, why, dec if dec is not None else decision)

    def blocked() -> bool:
        why = ctx.blocked(sym)
        if why:
            rec.update(action="none", note="buy blocked: " + "; ".join(why))
        return bool(why)

    if st.mode == "closed":
        return sell(avail, "exit") if avail else _none(rec)
    if st.mode == "waiting":
        if avail:
            return sell(avail, "exit_and_wait")
        if s.reentry_when is not None and _cond(s, s.reentry_when, vals):
            if blocked():
                rec["note"] += " (re-entry waits for the next qualifying session)"
                return None
            st.mode, st.step = "building", s.params[s.reentry_step]
            decision = [_explain(s, s.reentry_when, vals)]
        else:
            return _none(rec)
    # full exits win over every partial reduction and keep the right exit state
    if match and match[0].action in ("exit", "exit_and_wait"):
        st.mode = "closed" if match[0].action == "exit" else "waiting"
        return sell(avail, match[0].action) if avail else _none(rec)

    # partial reductions at this snapshot: the largest single request, never a sum
    cands: list[tuple[int, str, str, str]] = []  # (qty, reason, overlay, text)
    for f, reason, oid in ctx.reductions.get(sym, []):
        cands.append((math.floor(h.qty * f + 1e-9), reason, oid, f"{oid}: sell {f:.1%} of the {h.qty} shares held"))
    if st.mode == "holding" and match and match[0].action == "trim":
        f = s.params[match[0].size]
        cands.append((math.floor(h.qty * f + 1e-9), "trim", "", f"daily rule: sell {f:.0%} of the {h.qty} shares held"))
    if sym in ctx.targets:
        t, oid = ctx.targets[sym]
        cands.append((avail - t, "macro_trim", oid, f"{oid}: retained reduction to {t} shares"))
    if cands:
        qty, reason, oid, _ = max(cands, key=lambda c: (c[0], c[1] != "trim"))
        rot = max((math.floor(h.qty * f + 1e-9) for f, r, _ in ctx.reductions.get(sym, []) if r == "rotation_trim"), default=0)
        dec = decision + [{"cond": text, "ok": True, "value": n} for n, _, _, text in cands]
        if len(cands) > 1:
            dec.append({"cond": f"largest reduction wins: {qty} shares ({reason})", "ok": True, "value": qty})
        o = sell(qty, reason, dec, reserve=rot, overlay=oid)
        if o is None:
            rec["note"] = "reduction below one share (sub-share remainder logged, nothing sold)"
        rec["action"] = rec.get("action", "none")
        if st.mode == "building":
            rec["note"] = (rec.get("note", "") + " rebuild deferred: a reduction applies").strip()
        return o  # a reduction suppresses this symbol's buys this session

    if st.mode == "building":
        if blocked():
            return None
        missing = 1.0 - vals["holding"]
        if missing <= 1.0 - FULL:
            st.mode = "holding"
            return _none(rec)
        o = buy_usd(min(st.step, missing) * st.base_usd, "initial" if st.step >= 1.0 else "reentry")
        if o is None:
            if "note" not in rec:
                st.mode = "holding"  # less than one share short: treat as built
        elif vals["holding"] + o.qty * px / st.base_usd >= FULL:
            st.mode = "holding"
        if o is not None and not o.decision:
            o.decision = [{"cond": f"rebuild {min(st.step, missing):.0%} of the allocation", "ok": True,
                           "value": round(vals["holding"], 4)}]
        return o

    # holding: daily add vs macro rebuild, the larger increment (never both)
    add = s.params[match[0].size] if match and match[0].action == "add" else 0.0
    reb = ctx.rebuild[1] if ctx.rebuild and sym in ctx.rebuild[2] else 0.0
    if not add and not reb:
        return _none(rec, "hold" if match else "none")
    if blocked():
        return None
    room = s.max_holding - vals["holding"]
    if room * st.base_usd < px:
        rec["note"] = f"at the {s.max_holding:.0%} cap"
        return _none(rec)
    if reb > add:
        dec = decision + [{"cond": f"{ctx.rebuild[0]}: rebuild {reb:.0%} of the original allocation toward {s.max_holding:.0%}",
                           "ok": True, "value": round(vals["holding"], 4)}]
        return buy_usd(min(reb, room) * st.base_usd, "macro_rebuild", dec)
    return buy_usd(min(add, room) * st.base_usd, "add")


def _rebuild_progress(s, sl, held, quotes, orders, ctx: Ctx):
    """Finish the macro rebuild once every eligible symbol is at its cap (after today's buys)."""
    if not ctx.rebuild:
        return
    oid, _, eligible = ctx.rebuild
    bought = {o.symbol: o.qty for o in orders if o.side == "buy"}
    done = True
    for sym in eligible:
        st, q = sl.symbols[sym], quotes.get(sym)
        if st.mode == "closed":
            continue
        if st.mode != "holding" or q is None:
            done = False
            continue
        value = (held[sym].qty + bought.get(sym, 0)) * q.price
        if st.base_usd * s.max_holding - value >= q.price:  # at least one more share of room
            done = False
    if done:
        sl.overlays[oid]["phase"] = "normal"
        ctx.events.append({"kind": "macro_rebuilt", "overlay": oid})
