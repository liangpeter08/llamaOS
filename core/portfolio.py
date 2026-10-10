"""Portfolio engine: one daily decision per sleeve. Pure functions, no I/O, no models.

The backtester and the paper broker both call `plan()` with the same inputs (sleeve state,
quotes at the decision time, shares held), so a portfolio strategy decides identically in
replay and in paper. Fills come back through `apply_fill()`.

Per-symbol modes:
    building  buying `step` of the original allocation per session until it is fully held
              (the first session uses step 100%; re-entry uses reentry.step)
    holding   rules apply, first match wins
    waiting   sold by exit_and_wait; re-entry starts on the first session where reentry.when is true
    closed    sold by exit; never re-entered
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from common.config import RiskConfig
from dsl import expr as E
from dsl.portfolio import PortfolioStrategy, Rule

from .risk_guard import Check

FULL = 0.995  # holding at or above this fraction of the allocation counts as fully built


@dataclass
class Quote:
    price: float
    prior_close: float


@dataclass
class Holding:
    qty: int = 0
    cost: float = 0.0  # average cost per share


@dataclass
class SymState:
    base_usd: float
    mode: str = "building"
    step: float = 1.0


@dataclass
class Sleeve:
    capital_usd: float
    symbols: dict[str, SymState]
    started: str | None = None  # first session (ISO date)
    sessions: int = 0  # sessions decided so far
    last_day: str | None = None
    peak: float = 0.0
    realized: float = 0.0
    status: str = "active"  # active | finished | stopped | halted
    reason: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Sleeve":
        d = dict(d)
        d["symbols"] = {k: SymState(**v) for k, v in d["symbols"].items()}
        return cls(**d)


@dataclass
class Order:
    symbol: str
    side: str  # buy | sell
    qty: int
    price: float  # reference price at the decision
    reason: str  # rule action or sleeve event
    decision: list[dict] = field(default_factory=list)


@dataclass
class DayPlan:
    orders: list[Order]
    records: list[dict]  # one per symbol: what was seen and decided
    event: str = ""  # sleeve-level event: horizon | max_drawdown | retired


def start(s: PortfolioStrategy, equity: float, day: str) -> Sleeve:
    cap = s.capital * equity
    return Sleeve(capital_usd=cap, symbols={sym: SymState(base_usd=cap * w) for sym, w in s.weights.items()},
                  started=day, peak=cap)


def sleeve_value(sl: Sleeve, holdings: dict[str, Holding], prices: dict[str, float]) -> float:
    """Capital plus realized plus unrealized P&L: what the sleeve is worth now."""
    unreal = sum(h.qty * (prices[sym] - h.cost) for sym, h in holdings.items() if h.qty and sym in prices)
    return sl.capital_usd + sl.realized + unreal


def plan(s: PortfolioStrategy, sl: Sleeve, quotes: dict[str, Quote], holdings: dict[str, Holding], day: str,
         risk: RiskConfig, retire: bool = False) -> DayPlan:
    """Decide today's orders and advance the sleeve state. Missing quotes fail closed for that symbol."""
    if sl.status != "active" or sl.last_day == day:
        return DayPlan([], [])
    k = sl.sessions
    prices = {sym: q.price for sym, q in quotes.items()}
    held = {sym: holdings.get(sym, Holding()) for sym in s.symbols}

    event = ""
    if retire:
        event = "retired"
    elif all(sym in prices for sym, h in held.items() if h.qty):
        value = sleeve_value(sl, held, prices)
        sl.peak = max(sl.peak, value)
        dd = s.max_drawdown(risk)
        if sl.peak > 0 and value / sl.peak - 1 <= -dd:
            event = "max_drawdown"
    if not event and (k >= s.hold_sessions - 1 or (s.end_date and day >= s.end_date.isoformat())):
        event = "horizon"

    orders: list[Order] = []
    records: list[dict] = []
    if event:
        for sym, h in held.items():
            if h.qty:
                px = prices.get(sym, h.cost)
                orders.append(Order(sym, "sell", h.qty, px, event,
                                    [{"cond": _EVENT[event], "ok": True, "value": True}]))
            records.append({"symbol": sym, "mode": sl.symbols[sym].mode, "action": event})
        sl.status = "stopped" if event == "max_drawdown" else "finished"
        sl.reason = event
    else:
        for sym in s.symbols:
            st, h, q = sl.symbols[sym], held[sym], quotes.get(sym)
            rec = {"symbol": sym, "mode": st.mode}
            if q is None or not q.prior_close or q.price <= 0:
                rec.update(action="skip", note="no fresh price (fails closed)")
                records.append(rec)
                continue
            vals = {
                "day_change": q.price / q.prior_close - 1,
                "holding": h.qty * q.price / st.base_usd if st.base_usd else 0.0,
                "sessions_held": float(k),
                "gain": q.price / h.cost - 1 if h.qty and h.cost else None,
            }
            rec.update(day_change=round(vals["day_change"], 6), holding=round(vals["holding"], 4))
            o = _decide(s, st, h, q, vals, rec)
            if o:
                orders.append(o)
            rec["mode_after"] = st.mode
            records.append(rec)
    sl.sessions = k + 1
    sl.last_day = day
    orders.sort(key=lambda o: o.side != "sell")  # sells first: they free buying power
    return DayPlan(orders, records, event)


_EVENT = {"horizon": "holding period over: sell everything", "max_drawdown": "sleeve drawdown limit reached: sell everything",
          "retired": "strategy retired: sell everything"}


def _decide(s: PortfolioStrategy, st: SymState, h: Holding, q: Quote, vals: dict, rec: dict) -> Order | None:
    px = q.price
    match = _first_match(s, vals)
    rec["rule"] = match[0].when.src if match else None
    decision = match[1] if match else []

    def buy_usd(usd: float, why: str) -> Order | None:
        qty = math.floor(max(usd, 0.0) / px)
        rec["action"] = f"buy {qty}" if qty else "none"
        return Order(rec["symbol"], "buy", qty, px, why, decision) if qty else None

    def sell(qty: int, why: str) -> Order | None:
        qty = min(qty, h.qty)
        rec["action"] = f"sell {qty}" if qty else "none"
        return Order(rec["symbol"], "sell", qty, px, why, decision) if qty else None

    if st.mode == "closed":
        return sell(h.qty, "exit") if h.qty else _none(rec)
    if st.mode == "waiting":
        if h.qty:  # an earlier exit didn't fill: try again
            return sell(h.qty, "exit_and_wait")
        if s.reentry_when is not None and _cond(s, s.reentry_when, vals):
            st.mode, st.step = "building", s.params[s.reentry_step]
            decision = [_explain(s, s.reentry_when, vals)]
        else:
            return _none(rec)
    if match and match[0].action in ("exit", "exit_and_wait"):
        st.mode = "closed" if match[0].action == "exit" else "waiting"
        return sell(h.qty, match[0].action) if h.qty else _none(rec)
    if st.mode == "building":
        missing = 1.0 - vals["holding"]
        if missing <= 1.0 - FULL:
            st.mode = "holding"
            return _none(rec)
        o = buy_usd(min(st.step, missing) * st.base_usd, "initial" if st.step >= 1.0 else "reentry")
        if o is None:
            st.mode = "holding"  # less than one share short: treat as built
        elif vals["holding"] + o.qty * px / st.base_usd >= FULL:
            st.mode = "holding"
        if o is not None and not o.decision:
            o.decision = [{"cond": f"rebuild {min(st.step, missing):.0%} of the allocation", "ok": True,
                           "value": round(vals["holding"], 4)}]
        return o
    # holding
    if not match or match[0].action == "hold":
        return _none(rec, "hold" if match else "none")
    r = match[0]
    size = s.params[r.size]
    if r.action == "trim":
        return sell(math.floor(h.qty * size), "trim")
    room = s.max_holding - vals["holding"]
    return buy_usd(min(size, room) * st.base_usd, "add")


def _none(rec: dict, action: str = "none") -> None:
    rec["action"] = action
    return None


def _first_match(s: PortfolioStrategy, vals: dict) -> tuple[Rule, list[dict]] | None:
    for r in s.rules:
        if _cond(s, r.when, vals):
            return r, [_explain(s, r.when, vals)]
    return None


def _cond(s: PortfolioStrategy, node: E.Node, vals: dict) -> bool:
    return _eval(s, node, vals) is True


def _explain(s: PortfolioStrategy, node: E.Node, vals: dict) -> dict:
    rec = {"cond": node.src, "ok": _eval(s, node, vals) is True, "value": True}
    if isinstance(node, E.Cmp):
        rec["detail"] = f"{node.l.src} = {_fmt(_eval(s, node.l, vals))} {node.op} {node.r.src} = {_fmt(_eval(s, node.r, vals))}"
    return rec


def _fmt(v):
    return None if v is None else round(v, 6)


def _eval(s: PortfolioStrategy, node: E.Node, vals: dict):
    """Fail-closed evaluator for the small portfolio condition language (validated beforehand)."""
    if isinstance(node, (E.Num, E.BoolLit)):
        return node.value
    if isinstance(node, E.Name):
        return s.params[node.id] if node.id in s.params else vals.get(node.id)
    if isinstance(node, E.Neg):
        x = _eval(s, node.x, vals)
        return None if x is None else -x
    if isinstance(node, E.BinOp):
        a, b = _eval(s, node.l, vals), _eval(s, node.r, vals)
        if a is None or b is None:
            return None
        if node.op == "/":
            return None if b == 0 else a / b
        return {"+": a + b, "-": a - b, "*": a * b}[node.op]
    if isinstance(node, E.Cmp):
        a, b = _eval(s, node.l, vals), _eval(s, node.r, vals)
        if a is None or b is None:
            return None
        return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "!=": a != b}[node.op]
    if isinstance(node, E.Not):
        x = _eval(s, node.x, vals)
        return None if x is None else not x
    if isinstance(node, E.BoolOp):
        xs = [_eval(s, x, vals) for x in node.items]
        if node.op == "and":
            return False if any(x is False for x in xs) else (None if any(x is None for x in xs) else True)
        return True if any(x is True for x in xs) else (None if any(x is None for x in xs) else False)
    raise TypeError(f"not allowed in portfolio rules: {node.src!r}")


# ---------------------------------------------------------------- fills
def apply_fill(sl: Sleeve | None, holdings: dict[str, Holding], symbol: str, side: str, qty: float, price: float) -> float:
    """Update average-cost holdings; returns realized P&L for sells (added to the sleeve)."""
    h = holdings.setdefault(symbol, Holding())
    pnl = 0.0
    if side == "buy":
        total = h.qty + qty
        h.cost = (h.cost * h.qty + price * qty) / total if total else 0.0
        h.qty = int(round(total))
    else:
        qty = min(qty, h.qty)
        pnl = (price - h.cost) * qty
        h.qty = int(round(h.qty - qty))
        if h.qty == 0:
            h.cost = 0.0
        if sl is not None:
            sl.realized += pnl
    return pnl


# ---------------------------------------------------------------- risk checks
@dataclass
class Book:
    """Account view for checking one sleeve order."""
    equity: float
    gross_exposure: float  # $ of every open position in the account
    sleeve_exposure: float  # $ of every sleeve position (all portfolio strategies)
    symbol_value: float  # $ this sleeve holds in the order's symbol
    held_qty: int
    orders_today: int
    kill_switch: bool = False
    intraday_symbols: set[str] = field(default_factory=set)


def check_order(o: Order, b: Book, risk: RiskConfig, adv: float | None) -> list[Check]:
    pr = risk.portfolio
    if pr is None:
        return [Check("portfolio_enabled", False, "portfolio strategies are disabled (Risk page)")]
    eq = max(b.equity, 1e-9)
    notional = o.qty * o.price
    checks = [
        Check("kill_switch_off", not b.kill_switch, "kill switch is active" if b.kill_switch else "off"),
        Check("qty_positive", o.qty >= 1, f"qty={o.qty}"),
        Check("max_orders_per_day", b.orders_today < pr.max_orders_per_day, f"{b.orders_today} today / max {pr.max_orders_per_day}"),
    ]
    if o.side == "sell":
        checks.append(Check("sell_within_holding", o.qty <= b.held_qty, f"sell {o.qty} / held {b.held_qty} (no shorting)"))
        return checks
    checks += [
        Check("not_intraday_symbol", o.symbol not in b.intraday_symbols,
              f"{o.symbol} is held by an intraday strategy" if o.symbol in b.intraday_symbols else "free"),
        Check("max_position", b.symbol_value + notional <= pr.max_position * eq + 1e-6,
              f"${b.symbol_value + notional:,.0f} = {(b.symbol_value + notional) / eq:.1%} after / max {pr.max_position:.0%}"),
        Check("max_allocation", b.sleeve_exposure + notional <= pr.max_allocation * eq + 1e-6,
              f"${b.sleeve_exposure + notional:,.0f} = {(b.sleeve_exposure + notional) / eq:.1%} in sleeves after / max {pr.max_allocation:.0%}"),
        Check("gross_exposure", b.gross_exposure + notional <= risk.max_gross_exposure * eq + 1e-6,
              f"${b.gross_exposure + notional:,.0f} after / max {risk.max_gross_exposure:.2f}x equity"),
        Check("min_price", o.price >= pr.min_price, f"{o.price:.2f} / min {pr.min_price:.2f}"),
        Check("min_adv", adv is not None and adv >= pr.min_adv,
              f"ADV(20) ${adv:,.0f} / min ${pr.min_adv:,.0f}" if adv is not None else "ADV unknown (fails closed)"),
    ]
    return checks


def replay_fills(rows: list[dict]) -> tuple[dict[str, Holding], float, list[dict]]:
    """Holdings, realized P&L and closed lots from filled sleeve orders (oldest first)."""
    holdings: dict[str, Holding] = {}
    realized, sells = 0.0, []
    for r in rows:
        qty, px = float(r.get("filled_qty") or 0), r.get("avg_price")
        if qty <= 0 or px is None:
            continue
        cost = holdings.get(r["symbol"], Holding()).cost
        pnl = apply_fill(None, holdings, r["symbol"], r["side"], qty, float(px))
        if r["side"] == "sell":
            realized += pnl
            sells.append({**r, "entry_price": cost, "pnl": pnl})
    return holdings, realized, sells
