"""Replay a strategy over stored sessions through the same TradingSession the broker uses.

Price-only logic uses full history. News-gated logic reads recorded signals only (the
point-in-time store), so model look-ahead can't leak into results.
"""
from __future__ import annotations

import math
import statistics
from datetime import date

from common import marketdata as MD
from common import timeutil as T
from common.config import RiskConfig, universe
from core.session import TradingSession
from dsl.evaluator import DayData
from dsl.validator import Strategy


def simulate(conn, strategies: list[Strategy], risk: RiskConfig, days: list[date] | None = None,
             starting_equity: float = 100_000.0, slippage: float = 0.0002, keep_records: int = 25,
             on_bar=None) -> dict:
    """Replay strategies together over stored sessions. on_bar(session, day, i) runs after each bar.

    Intraday strategies only: portfolio strategies hold overnight and replay through backtest.portfolio."""
    strategies = [s for s in strategies if getattr(s, "kind", "intraday") == "intraday"]
    days = days if days is not None else MD.bar_days(conn)
    syms = [s for s in universe() if s in set(MD.symbols(conn))]
    equity = starting_equity
    curve, trades, rejected, records = [], [], 0, []
    per_day = []
    for d in days:
        data = {s: MD.load_day(conn, s, d) for s in syms}
        n = min((len(x) for x in data.values()), default=0)
        if n == 0:
            continue
        # replay bar by bar: the evaluator only ever sees bars up to the one being decided
        replay = {s: DayData(s, d, x.history) for s, x in data.items()}
        book = MD.signal_book(conn, T.prior_close_ts(d), T.close_ts(d))
        sess = TradingSession(risk, strategies, replay, book, cash=equity, slippage=slippage)
        day_trades = []

        def collect(events):
            nonlocal rejected
            for ev in events:
                if ev.kind == "trade":
                    t = {k: v for k, v in ev.data.items() if k != "intent_ref"}
                    ref = ev.data.get("intent_ref")
                    if ref is not None:
                        t["sid"] = ref.sid
                        if len(records) < keep_records:
                            records.append({"symbol": ref.symbol, "ts": ref.ts, "decision": ref.decision,
                                            "signals": ref.signals, "pnl": t["pnl"]})
                    t["day"] = d.isoformat()
                    day_trades.append(t)
                elif ev.kind == "rejected":
                    rejected += 1

        for i in range(n):
            for s in syms:
                x = data[s]
                replay[s].append(x.ts[i], x.o[i], x.h[i], x.l[i], x.c[i], x.v[i])
            collect(sess.step(i))
            if i == n - 1:
                collect(sess.end_of_day())
            if on_bar is not None:
                on_bar(sess, d, i)
        equity = sess.exchange.equity()
        curve.append({"day": d.isoformat(), "equity": round(equity, 2)})
        per_day.append({"day": d.isoformat(), "trades": len(day_trades),
                        "pnl": round(sum(t["pnl"] for t in day_trades), 2)})
        trades += day_trades
    return {
        "days": [d.isoformat() for d in days], "symbols": syms, "starting_equity": starting_equity,
        "metrics": metrics(trades, starting_equity, curve),
        "per_day": per_day, "equity_curve": curve, "trades": trades,
        "rejected_by_guard": rejected, "decision_records": records,
    }


def run(conn, strat: Strategy, risk: RiskConfig, days: list[date] | None = None,
        starting_equity: float = 100_000.0, slippage: float = 0.0002, keep_records: int = 25) -> dict:
    if getattr(strat, "kind", "intraday") == "portfolio":
        from .portfolio import run as run_portfolio
        return run_portfolio(conn, strat, risk, starting_equity=starting_equity, slippage=slippage)
    rep = simulate(conn, [strat], risk, days, starting_equity, slippage, keep_records)
    return {
        "strategy": strat.id, "version": strat.version, "hash": strat.hash,
        "uses_signals": strat.uses_signals,
        "signal_note": "news-gated conditions read recorded signals only" if strat.uses_signals else "price-only",
        **rep,
    }


def metrics(trades: list[dict], start: float, curve: list[dict]) -> dict:
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    peak, mdd = start, 0.0
    for p in curve:
        peak = max(peak, p["equity"])
        mdd = min(mdd, p["equity"] / peak - 1)
    daily = [c["equity"] for c in curve]
    rets = [daily[k] / (daily[k - 1] if k else start) - 1 for k in range(len(daily))]
    sharpe = None
    if len(rets) > 2 and statistics.pstdev(rets) > 0:
        sharpe = statistics.mean(rets) / statistics.pstdev(rets) * math.sqrt(252)
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else None,
        "avg_r": statistics.mean(rs) if rs else None,
        "total_pnl": round(pnl, 2),
        "return_pct": pnl / start,
        "max_drawdown": mdd,
        "sharpe_daily": sharpe,
        "exits": {k: sum(1 for t in trades if t["exit_kind"] == k) for k in {t["exit_kind"] for t in trades}},
    }
