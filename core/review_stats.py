"""Evidence for the strategy review panel, computed from stored bars and records. Pure stats, no models.

Every figure the panel quotes comes from here or from the backtest, so the agents argue from the
same numbers the system trades on.
"""
from __future__ import annotations

import math
import statistics


def symbol_stats(closes: list[float], days: list[str]) -> dict:
    """Trend, volatility and how often daily-move thresholds were hit, from a daily close series."""
    if len(closes) < 3:
        return {"sessions": len(closes)}
    rets = [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]

    def ret(n):
        return round(closes[-1] / closes[-1 - n] - 1, 4) if len(closes) > n else None

    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    vol = statistics.pstdev(rets[-20:]) * math.sqrt(252) if len(rets) >= 5 else None
    return {
        "sessions": len(closes), "from": days[0], "to": days[-1], "last_close": round(closes[-1], 2),
        "return_5d": ret(5), "return_20d": ret(20), "return_60d": ret(60), "return_all": round(closes[-1] / closes[0] - 1, 4),
        "vol_20d_annualized": round(vol, 4) if vol is not None else None,
        "max_drawdown": round(mdd, 4),
        "best_day": round(max(rets), 4), "worst_day": round(min(rets), 4),
        "days_up_over_3pct": sum(r > 0.03 for r in rets), "days_up_over_5pct": sum(r > 0.05 for r in rets),
        "days_down_3pct_or_more": sum(r <= -0.03 for r in rets), "days_down_over_5pct": sum(r < -0.05 for r in rets),
        "pct_up_days": round(sum(r > 0 for r in rets) / len(rets), 3),
    }


def correlations(series: dict[str, list[float]]) -> dict:
    """Average and extreme pairwise correlation of daily returns over the common tail."""
    names = [s for s, c in series.items() if len(c) >= 21]
    n = min((len(series[s]) for s in names), default=0)
    if len(names) < 2 or n < 21:
        return {"symbols": names, "note": "not enough common history"}
    rets = {s: [series[s][-n:][k] / series[s][-n:][k - 1] - 1 for k in range(1, n)] for s in names}
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                pairs.append((statistics.correlation(rets[a], rets[b]), a, b))
            except statistics.StatisticsError:
                continue
    if not pairs:
        return {"symbols": names, "note": "flat series"}
    pairs.sort()
    return {"sessions": n - 1, "average": round(sum(p[0] for p in pairs) / len(pairs), 3),
            "lowest": {"pair": f"{pairs[0][1]}/{pairs[0][2]}", "corr": round(pairs[0][0], 3)},
            "highest": {"pair": f"{pairs[-1][1]}/{pairs[-1][2]}", "corr": round(pairs[-1][0], 3)}}


def basket_worst_day(series: dict[str, list[float]], weights: dict[str, float]) -> dict:
    """The worst and best daily move of the weighted basket over the common history."""
    names = [s for s in weights if len(series.get(s, [])) >= 2]
    n = min((len(series[s]) for s in names), default=0)
    if n < 2:
        return {}
    total = sum(weights[s] for s in names) or 1
    moves = [sum(weights[s] / total * (series[s][-n:][k] / series[s][-n:][k - 1] - 1) for s in names) for k in range(1, n)]
    return {"sessions": n - 1, "worst_day": round(min(moves), 4), "best_day": round(max(moves), 4),
            "vol_annualized": round(statistics.pstdev(moves) * math.sqrt(252), 4) if len(moves) > 2 else None}
