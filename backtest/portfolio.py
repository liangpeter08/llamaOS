"""Replay a portfolio strategy over stored daily bars with the same engine the paper broker uses.

Daily bars carry one price per session, so each session's rules are decided on the
close-to-close move and filled at that close plus slippage. Live, the broker decides at
`decide_at` (15:45 ET) on the move so far and fills before the close; the report says so.

DSL 0.2 files also replay their market inputs from daily closes (index history in `index_daily`,
basket members in `daily`) and simulate the overlays as enforced, whatever the file's monitoring mode.
An intraday macro trigger can't be replayed from daily bars: it is merged into the daily decision.
"""
from __future__ import annotations

import bisect

from common import db
from common.config import RiskConfig
from core import portfolio as P
from dsl.features import HISTORY_SESSIONS
from dsl.portfolio import PortfolioStrategy

from .engine import metrics

ADV_DAYS = 20


def run(conn, s: PortfolioStrategy, risk: RiskConfig, starting_equity: float = 100_000.0,
        slippage: float = 0.0002, days=None, commission_per_share=0.0, commission_per_order=0.0,
        risk_free_rate=0.0, progress=None, check_cancel=None, missing_data='skip_symbol', repeat_cycles=False, assumption_blocks=None, **_) -> dict:
    """repeat_cycles: when the holding period ends before the window does, start a new sleeve (with the equity
    then) on the next session, so a long window replays consecutive holding periods instead of only the first."""
    v2 = getattr(s, "v2", None)
    daily = {sym: {r["day"]: r for r in db.rows(conn, "SELECT day, c, v FROM daily WHERE symbol=? ORDER BY day", (sym,))}
             for sym in [*s.symbols, *s.input_symbols]}
    missing = [sym for sym, rows in daily.items() if not rows]
    if missing:
        raise ValueError(f"No daily bars stored for {', '.join(missing)}. The paper broker downloads daily history for "
                         "portfolio symbols when it starts or reloads strategies; retry in a minute.")
    selected = {d.isoformat() if hasattr(d, 'isoformat') else str(d) for d in days} if days is not None else None
    index = {}
    for inst in (v2.index_instruments if v2 else []):
        index[inst] = {r["day"]: r["c"] for r in db.rows(conn, "SELECT day, c FROM index_daily WHERE instrument=? ORDER BY day", (inst,))}
        if not index[inst]:
            raise ValueError(f"No daily history stored for the index {inst}. Configure it in config/market_inputs.yaml; the paper "
                             "broker downloads index history for portfolio strategies when it starts or reloads strategies.")
    days = sorted(set.intersection(*(set(rows) for rows in [*daily.values(), *index.values()])))
    # Each symbol's own completed sessions, for daily-history features (not limited to the common days).
    own_days = {sym: sorted(rows) for sym, rows in daily.items()}
    readable = [*s.symbols, *[r for r in s.references if r not in s.symbols]]

    def closes(sym: str, day: str) -> list:
        k = bisect.bisect_left(own_days[sym], day)
        return [(d, daily[sym][d]["c"]) for d in own_days[sym][max(0, k - HISTORY_SESSIONS):k]]
    omitted = sorted(selected - set(days)) if selected is not None else []
    if omitted and missing_data == 'strict':
        raise ValueError(f'Portfolio basket or market inputs missing on requested sessions: {", ".join(omitted)}')
    if len(days) < ADV_DAYS + 2:
        raise ValueError(f"Only {len(days)} sessions have daily bars for every symbol; the backtest needs at least {ADV_DAYS + 2}.")
    first = max(ADV_DAYS, len(days) - s.hold_sessions) if selected is None else ADV_DAYS
    window = [d for d in days[first:] if selected is None or d in selected]
    if not window:
        raise ValueError('No portfolio sessions in the requested window with 20 prior sessions for every symbol.')

    cash = starting_equity
    holdings: dict[str, P.Holding] = {}
    sleeve: P.Sleeve | None = None
    curve, per_day, trades, orders, records, rejected = [], [], [], [], [], []
    prev_equity = starting_equity
    last_close: dict[str, float] = {}
    total_fees = 0.0
    entry_fees: dict[str, float] = {}
    locked = 0.0
    events: list[dict] = []
    cycles: list[dict] = []
    cycle_start = (window[0], starting_equity)
    for n, day in enumerate(window):
        if check_cancel:
            check_cancel()
        i = days.index(day)
        close = {sym: daily[sym][day]["c"] for sym in s.symbols}
        quotes = {sym: P.Quote(daily[sym][day]["c"], daily[sym][days[i - 1]]["c"],
                               closes(sym, day) if s.daily_lookback else []) for sym in readable}
        adv = {sym: sum(daily[sym][d]["c"] * daily[sym][d]["v"] for d in days[i - ADV_DAYS:i]) / ADV_DAYS for sym in s.symbols}
        equity = cash + sum(h.qty * close[sym] for sym, h in holdings.items())
        if sleeve is None:
            sleeve = P.start(s, equity, day)
            cycle_start = (day, equity)
        if v2:
            dp = P.plan(s, sleeve, quotes, holdings, day, risk, inputs=_inputs(v2, daily, index, days[i - 1], day),
                        locked_usd=locked, enforce=True, blocks=(assumption_blocks or {}).get(day, []))
            events += [{"day": day, **e} for e in dp.events]
        else:
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
            fee = o.qty * commission_per_share + commission_per_order
            total_fees += fee
            allocated_fee = 0.0
            if o.side == 'sell':
                held_qty = holdings[o.symbol].qty
                allocated_fee = entry_fees.get(o.symbol, 0.0) * o.qty / held_qty
                entry_fees[o.symbol] = entry_fees.get(o.symbol, 0.0) - allocated_fee
            else:
                entry_fees[o.symbol] = entry_fees.get(o.symbol, 0.0) + fee
            pnl = P.apply_fill(sleeve, holdings, o.symbol, o.side, o.qty, px)
            sleeve.realized -= fee  # sleeve drawdown checks must observe transaction costs too
            cash += -o.qty * px if o.side == "buy" else o.qty * px
            cash -= fee
            if o.side == "sell" and o.reserve_qty:
                locked += min(o.reserve_qty, o.qty) * px  # rotation proceeds stay as reserved strategy cash
            invested = sum(h.qty * close[sym] for sym, h in holdings.items())
            cap = sleeve.capital_usd
            orders.append({"day": day, "symbol": o.symbol, "side": o.side, "qty": o.qty, "price": round(px, 4),
                           "reason": o.reason, "decision": o.decision, "fee": fee, "reserve_qty": o.reserve_qty,
                           # Allocation after this fill, as a fraction of the sleeve's capital, plus the account's cash.
                           "notional": round(o.qty * px, 2), "deployed_pct": o.qty * px / cap if cap else None,
                           "allocated_pct": invested / cap if cap else None,
                           "cash_after": round(cash, 2), "invested_after": round(invested, 2)})
            if o.side == "sell":
                trades.append({"day": day, "symbol": o.symbol, "side": "long", "qty": o.qty, "entry_price": round(cost, 4),
                               "exit_price": round(px, 4), "exit_kind": o.reason, "pnl": round(pnl - fee - allocated_fee, 4),
                               "fees": fee + allocated_fee, "gross_pnl": pnl, "r_multiple": None,
                               "sid": s.id})
        records += [{"day": day, **r} for r in dp.records]
        invested = sum(h.qty * close[sym] for sym, h in holdings.items())
        equity = cash + invested
        curve.append({"day": day, "equity": round(equity, 4), "cash": round(cash, 2), "stock": round(invested, 2)})
        per_day.append({"day": day, "trades": day_orders, "pnl": round(equity - prev_equity, 2)})
        prev_equity, last_close = equity, close
        if progress:
            progress(n + 1, len(window))
        if sleeve.status != "active":
            cycles.append({"start": cycle_start[0], "end": day, "reason": sleeve.reason, "sessions": sleeve.sessions,
                           "return_pct": equity / cycle_start[1] - 1 if cycle_start[1] else 0.0})
            if repeat_cycles and sleeve.reason == "horizon" and n < len(window) - 1:
                sleeve, locked = None, 0.0  # everything was sold: the next session starts a fresh holding period
                continue
            break
    if sleeve is not None and sleeve.status == "active" and curve:
        cycles.append({"start": cycle_start[0], "end": curve[-1]["day"], "reason": "end_of_window", "sessions": sleeve.sessions,
                       "return_pct": curve[-1]["equity"] / cycle_start[1] - 1 if cycle_start[1] else 0.0})

    used = [c["day"] for c in curve]
    start_px = {sym: daily[sym][used[0]]["c"] for sym in s.symbols}
    bench = sum((s.capital * starting_equity * w) / (start_px[sym] * (1 + slippage)) * last_close[sym] * (1 - slippage)
                for sym, w in s.weights.items()) - s.capital * starting_equity
    m = metrics(trades, starting_equity, curve, risk_free_rate)
    m['total_fees'] = total_fees
    m['gross_pnl'] = m['total_pnl'] + total_fees
    m['turnover'] = sum(o['qty'] * o['price'] for o in orders) / starting_equity
    m["benchmark_return_pct"] = bench / starting_equity
    m['basket_buy_hold_return_pct'] = bench / starting_equity
    m["orders"] = len(orders)
    m["peak_exposure_pct"] = _peak_exposure(orders, daily, used, starting_equity, curve)
    truncated = len(used) < s.hold_sessions and (sleeve is None or sleeve.reason != "horizon")
    note = (f"daily bars: each session decided on its close-to-close move and filled at that close (live decides at "
            f"{s.decide_at:%H:%M} ET on the move so far). {len(used)} sessions from {used[0]} to {used[-1]}"
            + (f"; history covers fewer than the {s.hold_sessions} sessions held, so the rest was sold at the end" if truncated else "")
            + (f"; {len(cycles)} consecutive holding periods of up to {s.hold_sessions} sessions, each starting fresh with the equity "
               "at that point" if len(cycles) > 1 else "")
            + (f"; the strategy finished its {s.hold_sessions}-session holding period on {used[-1]}, before the end of the requested "
               "window (turn on repeat holding periods to keep going)" if not repeat_cycles and sleeve is not None
               and sleeve.reason == "horizon" and selected is not None and used[-1] < max(selected) else "")
            + "."
            + (f" Overlays simulated as enforced (the file runs them in {v2.mode} mode on paper); market inputs come from daily "
               "closes, so an intraday macro trigger is merged into that session's decision." if v2 and v2.overlays else ""))
    return {
        "strategy": s.id, "version": s.version, "hash": s.hash, "kind": "portfolio", "uses_signals": False,
        "signal_note": note, "days": used, "symbols": s.symbols, "starting_equity": starting_equity,
        "metrics": m, "per_day": per_day, "equity_curve": curve, "trades": trades, "orders": orders,
        "records": records, "rejected": rejected, "rejected_by_guard": len(rejected),
        "decision_records": [{"symbol": o["symbol"], "ts": o["day"], "decision": o["decision"], "pnl": None} for o in orders[:25]],
        "holdings_end": {sym: {"qty": h.qty, "cost": round(h.cost, 4)} for sym, h in holdings.items() if h.qty},
        "sleeve_end": sleeve.to_json() if sleeve else None,
        "overlay_events": events, "locked_cash": round(locked, 2), "input_symbols": list(s.input_symbols), "cycles": cycles,
        "warnings": ['Daily portfolio decisions fill at the same close: an approximation of intraday execution.',
                     'Corporate-action cashflows and historical universe membership are not modeled.'],
        "diagnostics": {"funnel": {"orders": len(orders), "rejected": len(rejected)},
                        "failed_conditions": {},
                        "risk_rejections": {name: sum(c['name'] == name for r in rejected for c in r['failed'])
                                            for name in {c['name'] for r in rejected for c in r['failed']}},
                        "processed_sessions": len(used),
                        "skipped_sessions": len(selected - set(used)) if selected is not None else 0},
    }


def _inputs(v2, daily, index, prev: str, day: str) -> dict[str, float | None]:
    """Market input values at one session's close: an index return, or the equal-weight mean of member returns."""
    out: dict[str, float | None] = {}
    for name, inp in v2.inputs.items():
        if inp.kind == "index":
            a, b = index[inp.instrument].get(prev), index[inp.instrument].get(day)
            out[name] = b / a - 1 if a and b else None
        else:
            rets = [daily[sym][day]["c"] / daily[sym][prev]["c"] - 1 for sym in inp.symbols
                    if daily[sym].get(day) and daily[sym].get(prev)]
            out[name] = sum(rets) / len(rets) if len(rets) == len(inp.symbols) else None  # a missing member: unknown, never reweighted
    return out


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
