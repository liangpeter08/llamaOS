"""Replay a portfolio strategy over stored daily bars with the same engine the paper broker uses.

Daily bars carry one price per session, so each session's rules are decided on the
close-to-close move and filled at that close plus slippage. Live, the broker decides at
`decide_at` (15:45 ET) on the move so far and fills before the close; the report says so.
"""
from __future__ import annotations

from common import db
from common.config import RiskConfig
from core import portfolio as P
from dsl.portfolio import PortfolioStrategy

from .engine import metrics

ADV_DAYS = 20


def run(conn, s: PortfolioStrategy, risk: RiskConfig, starting_equity: float = 100_000.0,
        slippage: float = 0.0002, **_) -> dict:
    daily = {sym: {r["day"]: r for r in db.rows(conn, "SELECT day, c, v FROM daily WHERE symbol=? ORDER BY day", (sym,))}
             for sym in s.symbols}
    missing = [sym for sym, rows in daily.items() if not rows]
    if missing:
        raise ValueError(f"No daily bars stored for {', '.join(missing)}. The paper broker downloads daily history for "
                         "portfolio symbols when it starts or reloads strategies; retry in a minute.")
    days = sorted(set.intersection(*(set(rows) for rows in daily.values())))
    if len(days) < ADV_DAYS + 2:
        raise ValueError(f"Only {len(days)} sessions have daily bars for every symbol; the backtest needs at least {ADV_DAYS + 2}.")
    first = max(ADV_DAYS, len(days) - s.hold_sessions)
    window = days[first:]

    cash = starting_equity
    holdings: dict[str, P.Holding] = {}
    sleeve: P.Sleeve | None = None
    curve, per_day, trades, orders, records, rejected = [], [], [], [], [], []
    prev_equity = starting_equity
    last_close: dict[str, float] = {}
    for n, day in enumerate(window):
        i = first + n
        close = {sym: daily[sym][day]["c"] for sym in s.symbols}
        quotes = {sym: P.Quote(close[sym], daily[sym][days[i - 1]]["c"]) for sym in s.symbols}
        adv = {sym: sum(daily[sym][d]["c"] * daily[sym][d]["v"] for d in days[i - ADV_DAYS:i]) / ADV_DAYS for sym in s.symbols}
        equity = cash + sum(h.qty * close[sym] for sym, h in holdings.items())
        if sleeve is None:
            sleeve = P.start(s, equity, day)
        dp = P.plan(s, sleeve, quotes, holdings, day, risk)
        if n == len(window) - 1 and sleeve.status == "active":
            dp.orders += [P.Order(sym, "sell", h.qty, close[sym], "backtest_end",
                                  [{"cond": "end of stored history", "ok": True, "value": True}])
                          for sym, h in holdings.items() if h.qty and not any(o.symbol == sym and o.side == "sell" for o in dp.orders)]
        day_orders = 0
        for o in dp.orders:
            mv = {sym: h.qty * close[sym] for sym, h in holdings.items()}
            book = P.Book(equity=cash + sum(mv.values()), gross_exposure=sum(mv.values()), sleeve_exposure=sum(mv.values()),
                          symbol_value=mv.get(o.symbol, 0.0), held_qty=holdings.get(o.symbol, P.Holding()).qty,
                          orders_today=day_orders)
            checks = P.check_order(o, book, risk, adv[o.symbol])
            if not all(c.ok for c in checks):
                rejected.append({"day": day, "symbol": o.symbol, "side": o.side, "qty": o.qty, "reason": o.reason,
                                 "failed": [c.as_dict() for c in checks if not c.ok]})
                continue
            day_orders += 1
            px = o.price * (1 + slippage if o.side == "buy" else 1 - slippage)
            cost = holdings.get(o.symbol, P.Holding()).cost
            pnl = P.apply_fill(sleeve, holdings, o.symbol, o.side, o.qty, px)
            cash += -o.qty * px if o.side == "buy" else o.qty * px
            orders.append({"day": day, "symbol": o.symbol, "side": o.side, "qty": o.qty, "price": round(px, 4),
                           "reason": o.reason, "decision": o.decision})
            if o.side == "sell":
                trades.append({"day": day, "symbol": o.symbol, "side": "long", "qty": o.qty, "entry_price": round(cost, 4),
                               "exit_price": round(px, 4), "exit_kind": o.reason, "pnl": round(pnl, 2), "r_multiple": None,
                               "sid": s.id})
        records += [{"day": day, **r} for r in dp.records]
        equity = cash + sum(h.qty * close[sym] for sym, h in holdings.items())
        curve.append({"day": day, "equity": round(equity, 2)})
        per_day.append({"day": day, "trades": day_orders, "pnl": round(equity - prev_equity, 2)})
        prev_equity, last_close = equity, close
        if sleeve.status != "active":
            break

    used = [c["day"] for c in curve]
    start_px = {sym: daily[sym][used[0]]["c"] for sym in s.symbols}
    bench = sum((s.capital * starting_equity * w) / (start_px[sym] * (1 + slippage)) * last_close[sym] * (1 - slippage)
                for sym, w in s.weights.items()) - s.capital * starting_equity
    m = metrics(trades, starting_equity, curve)
    m["benchmark_return_pct"] = bench / starting_equity
    m["orders"] = len(orders)
    m["peak_exposure_pct"] = _peak_exposure(orders, daily, used, starting_equity, curve)
    truncated = len(used) < s.hold_sessions and (sleeve is None or sleeve.reason != "horizon")
    note = (f"daily bars: each session decided on its close-to-close move and filled at that close (live decides at "
            f"{s.decide_at:%H:%M} ET on the move so far). {len(used)} sessions from {used[0]} to {used[-1]}"
            + (f"; history covers fewer than the {s.hold_sessions} sessions held, so the rest was sold at the end" if truncated else "")
            + ".")
    return {
        "strategy": s.id, "version": s.version, "hash": s.hash, "kind": "portfolio", "uses_signals": False,
        "signal_note": note, "days": used, "symbols": s.symbols, "starting_equity": starting_equity,
        "metrics": m, "per_day": per_day, "equity_curve": curve, "trades": trades, "orders": orders,
        "records": records, "rejected": rejected, "rejected_by_guard": len(rejected),
        "decision_records": [{"symbol": o["symbol"], "ts": o["day"], "decision": o["decision"], "pnl": None} for o in orders[:25]],
        "holdings_end": {sym: {"qty": h.qty, "cost": round(h.cost, 4)} for sym, h in holdings.items() if h.qty},
        "sleeve_end": sleeve.to_json() if sleeve else None,
    }


def _peak_exposure(orders, daily, days, start, curve) -> float:
    held: dict[str, int] = {}
    by_day: dict[str, list] = {}
    for o in orders:
        by_day.setdefault(o["day"], []).append(o)
    peak = 0.0
    for day, pt in zip(days, curve):
        for o in by_day.get(day, []):
            held[o["symbol"]] = held.get(o["symbol"], 0) + (o["qty"] if o["side"] == "buy" else -o["qty"])
        mv = sum(q * daily[sym][day]["c"] for sym, q in held.items())
        peak = max(peak, mv / pt["equity"] if pt["equity"] else 0.0)
    return peak
