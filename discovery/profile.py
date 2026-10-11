"""Focus profile: what one ticker's own history says, computed by code on the discovery window only.

Every row carries its sample size and t-statistic so a generator can tell evidence from anecdote.
Nothing here reads the holdout: callers pass the last discovery session as `end`.
"""
from __future__ import annotations

import statistics
from datetime import date

from common import db
from common import timeutil as T
from dsl import features as F

from .stats import t_stat


def daily_rows(conn, sym: str, end: date | None = None) -> list[dict]:
    q = "SELECT day, o, h, l, c, v FROM daily WHERE symbol=?" + (" AND day<=?" if end else "") + " ORDER BY day"
    return db.rows(conn, q, (sym, end.isoformat()) if end else (sym,))


def _row(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "mean": statistics.mean(xs) if xs else None,
            "hit": sum(x > 0 for x in xs) / len(xs) if xs else None, "t": t_stat(xs)}


def _split(label_ret: list[tuple[str, float]]) -> dict:
    out: dict[str, list[float]] = {}
    for label, r in label_ret:
        out.setdefault(label, []).append(r)
    return {k: _row(v) for k, v in sorted(out.items())}


def daily_profile(conn, ticker: str, peers: list[str], start: date, end: date) -> dict | None:
    rows = daily_rows(conn, ticker, end)
    series = [(r["day"], r["c"]) for r in rows]
    idx = [k for k, r in enumerate(rows) if r["day"] >= start.isoformat()]
    if len(idx) < 30:
        return None
    rets = {rows[k]["day"]: rows[k]["c"] / rows[k - 1]["c"] - 1 for k in range(1, len(rows))}
    win = [rets[rows[k]["day"]] for k in idx if rows[k]["day"] in rets]
    sd = statistics.stdev(win)
    nxt = lambda k: rets.get(rows[k + 1]["day"]) if k + 1 < len(rows) else None  # noqa: E731
    fwd5 = lambda k: rows[k + 5]["c"] / rows[k]["c"] - 1 if k + 5 < len(rows) else None  # noqa: E731
    out = {
        "sessions": len(win), "from": rows[idx[0]]["day"], "to": rows[idx[-1]]["day"],
        "mean_daily_return": statistics.mean(win), "daily_vol": sd,
        "autocorr_1d": _autocorr(win),
        "next_day_after_big_move": _split([("up_>1sd" if rets.get(rows[k]["day"], 0) > sd else "down_<-1sd" if rets.get(rows[k]["day"], 0) < -sd
                                             else "normal", nxt(k)) for k in idx if nxt(k) is not None]),
        "next_5d_by_20d_trend": _split([("trend_up" if (v := F.ret_d(series[:k + 1], 20)) is not None and v > 0 else "trend_down", fwd5(k))
                                        for k in idx if fwd5(k) is not None and F.ret_d(series[:k + 1], 20) is not None]),
        "next_day_by_vol_regime": _split([(_tercile(F.vol_rank(series[:k + 1], 10, 60)), nxt(k)) for k in idx
                                          if nxt(k) is not None and F.vol_rank(series[:k + 1], 10, 60) is not None]),
        "next_5d_by_zscore_20": _split([("stretched_up_z>1" if z > 1 else "stretched_down_z<-1" if z < -1 else "middle", fwd5(k))
                                        for k in idx if fwd5(k) is not None and (z := F.zscore_d(series[:k + 1], 20)) is not None]),
        "peers": {},
    }
    for p in peers:
        prows = {r["day"]: r["c"] for r in daily_rows(conn, p, end)}
        pdays = sorted(prows)
        pret = {pdays[k]: prows[pdays[k]] / prows[pdays[k - 1]] - 1 for k in range(1, len(pdays))}
        pairs = [(pret[d], rets[d]) for d in (rows[k]["day"] for k in idx) if d in pret and d in rets]
        lagged = [(pret[rows[k]["day"]], nxt(k)) for k in idx if rows[k]["day"] in pret and nxt(k) is not None]
        if len(pairs) < 20:
            continue
        psd = statistics.stdev([a for a, _ in pairs])
        out["peers"][p] = {
            "same_day_corr": _corr(pairs), "beta": _beta(pairs), "lead_1d_corr": _corr(lagged),
            "next_day_after_peer_move": _split([("peer_up_>1sd" if a > psd else "peer_down_<-1sd" if a < -psd else "normal", b)
                                                for a, b in lagged]),
        }
    return out


def intraday_profile(conn, ticker: str, days: list[date]) -> dict | None:
    """Opening behaviour per session: gap, first 30 minutes, rest of day, VWAP and gap-fill."""
    rows = []
    for d in days:
        bars = db.rows(conn, "SELECT ts, o, h, l, c, v FROM bars WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                       (ticker, T.open_ts(d), T.close_ts(d)))
        prior = db.row(conn, "SELECT c FROM daily WHERE symbol=? AND day<? ORDER BY day DESC LIMIT 1", (ticker, d.isoformat()))
        if len(bars) < 60 or not prior:
            continue
        cut = next((k for k, b in enumerate(bars) if b["ts"] >= T.open_ts(d) + 30 * 60), None)
        if cut is None or cut == 0:
            continue
        o, first, last = bars[0]["o"], bars[cut - 1]["c"], bars[-1]["c"]
        rows.append({"gap": o / prior["c"] - 1, "first30": first / o - 1, "rest": last / first - 1,
                     "filled": min(b["l"] for b in bars) <= prior["c"] <= max(b["h"] for b in bars),
                     "range": (max(b["h"] for b in bars) - min(b["l"] for b in bars)) / o})
    if len(rows) < 10:
        return None
    gaps = sorted(abs(r["gap"]) for r in rows)
    big = gaps[int(len(gaps) * 0.66)]
    return {
        "sessions": len(rows), "median_abs_gap": statistics.median(gaps), "big_gap_threshold": big,
        "median_range": statistics.median(r["range"] for r in rows),
        "rest_of_day_after_first_30": _split([("first30_up" if r["first30"] > 0 else "first30_down", r["rest"]) for r in rows]),
        "rest_of_day_by_gap": _split([("gap_up_big" if r["gap"] > big else "gap_down_big" if r["gap"] < -big else "small_gap",
                                       r["rest"]) for r in rows]),
        "gap_fill_rate_big_gaps": (sum(r["filled"] for r in rows if abs(r["gap"]) > big)
                                   / max(1, sum(abs(r["gap"]) > big for r in rows))),
    }


def _tercile(v):
    return "vol_low" if v < 1 / 3 else "vol_high" if v > 2 / 3 else "vol_mid"


def _autocorr(xs: list[float]) -> float | None:
    return _corr(list(zip(xs[:-1], xs[1:]))) if len(xs) > 10 else None


def _corr(pairs) -> float | None:
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(pairs) < 10:
        return None
    a, b = zip(*pairs)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    return sum((x - ma) * (y - mb) for x, y in pairs) / (len(pairs) * sa * sb)


def _beta(pairs) -> float | None:
    a, b = zip(*pairs)
    vb = statistics.pvariance(a)
    if vb == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    return sum((x - ma) * (y - mb) for x, y in pairs) / (len(pairs) * vb)
