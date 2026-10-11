"""Daily-history features shared by intraday and portfolio rules (builtins@v2).

Every function reads *completed* sessions only: a list of (day, close) pairs, oldest first, that ends
with the last session before the one being decided. Nothing reads today's bars, so the same value is
seen at 09:31 and at 15:45, in replay and live. Too little history returns None, which fails closed.

Lookbacks are in sessions. `lookback(fn, args)` is the number of prior sessions a call needs; the
validator uses it to cap lookbacks and to tell the backtester and broker how much history to load.
"""
from __future__ import annotations

import math
import statistics

MAX_LOOKBACK = 250  # sessions; about one year of daily bars
HISTORY_SESSIONS = MAX_LOOKBACK + 10  # loaded per symbol (prior closes, ADV and ATR need a few extra)

# name -> (number of whole-number arguments, takes a symbol first, doc)
DAILY_FUNCTIONS: dict[str, tuple[int, bool, str]] = {
    "ret_d": (1, False, "Return over the last N completed sessions (prior close vs the close N sessions before it)"),
    "vol_d": (1, False, "Standard deviation of daily returns over the last N completed sessions (daily, not annualized)"),
    "vol_rank": (2, False, "Fraction (0-1) of the previous M sessions whose vol_d(N) was at or below today's vol_d(N)"),
    "sma_d": (1, False, "Average daily close over the last N completed sessions"),
    "zscore_d": (1, False, "(prior close - sma_d(N)) / standard deviation of the last N closes"),
    "highest_d": (1, False, "Highest daily close over the last N completed sessions"),
    "lowest_d": (1, False, "Lowest daily close over the last N completed sessions"),
    "up_days": (1, False, "Fraction (0-1) of the last N completed sessions that closed up"),
    "corr_d": (1, True, "Correlation of daily returns with SYM over the last N common sessions"),
    "beta_d": (1, True, "Beta of daily returns to SYM over the last N common sessions"),
}


def lookback(fn: str, args: list[int]) -> int:
    """Prior sessions a call needs (closes, so returns need one more)."""
    if fn == "vol_rank":
        return args[0] + args[1] + 1
    if fn in ("sma_d", "zscore_d", "highest_d", "lowest_d"):
        return args[0]
    return args[0] + 1


def min_n(fn: str) -> int:
    return 2 if fn in ("vol_d", "zscore_d", "corr_d", "beta_d", "vol_rank") else 1


Series = list[tuple[str, float]]  # (day, close), oldest first


def _closes(s: Series, n: int) -> list[float] | None:
    if n < 1 or len(s) < n:
        return None
    out = [c for _, c in s[-n:]]
    return None if any(c is None or c <= 0 for c in out) else out


def _rets(closes: list[float]) -> list[float]:
    return [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]


def _vol_at(s: Series, n: int, end: int) -> float | None:
    """vol_d(n) as of the session at index end-1 (exclusive end)."""
    if end - (n + 1) < 0:
        return None
    w = [c for _, c in s[end - n - 1:end]]
    if any(c is None or c <= 0 for c in w):
        return None
    return statistics.stdev(_rets(w))


def ret_d(s: Series, n: int):
    w = _closes(s, n + 1)
    return w[-1] / w[0] - 1 if w else None


def vol_d(s: Series, n: int):
    return _vol_at(s, n, len(s)) if n >= 2 else None


def vol_rank(s: Series, n: int, m: int):
    if n < 2 or m < 2 or len(s) < n + m + 1:
        return None
    now = _vol_at(s, n, len(s))
    past = [_vol_at(s, n, len(s) - k) for k in range(1, m + 1)]
    if now is None or any(v is None for v in past):
        return None
    return sum(v <= now for v in past) / len(past)


def sma_d(s: Series, n: int):
    w = _closes(s, n)
    return sum(w) / n if w else None


def zscore_d(s: Series, n: int):
    w = _closes(s, n) if n >= 2 else None
    if not w:
        return None
    sd = statistics.stdev(w)
    return (w[-1] - statistics.mean(w)) / sd if sd > 0 else None


def highest_d(s: Series, n: int):
    w = _closes(s, n)
    return max(w) if w else None


def lowest_d(s: Series, n: int):
    w = _closes(s, n)
    return min(w) if w else None


def up_days(s: Series, n: int):
    w = _closes(s, n + 1)
    return sum(r > 0 for r in _rets(w)) / n if w else None


def _paired(a: Series, b: Series, n: int) -> tuple[list[float], list[float]] | None:
    """Returns of a and b on the last n sessions both have, using each one's previous common session."""
    bd = dict(b)
    common = [(d, c, bd[d]) for d, c in a if d in bd]
    if len(common) < n + 1:
        return None
    w = common[-(n + 1):]
    if any(x is None or x <= 0 or y is None or y <= 0 for _, x, y in w):
        return None
    ra = [w[k][1] / w[k - 1][1] - 1 for k in range(1, len(w))]
    rb = [w[k][2] / w[k - 1][2] - 1 for k in range(1, len(w))]
    return ra, rb


def corr_d(a: Series, b: Series, n: int):
    p = _paired(a, b, n) if n >= 2 else None
    if not p:
        return None
    ra, rb = p
    sa, sb = statistics.pstdev(ra), statistics.pstdev(rb)
    if sa == 0 or sb == 0:
        return None
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (len(ra) * sa * sb)


def beta_d(a: Series, b: Series, n: int):
    p = _paired(a, b, n) if n >= 2 else None
    if not p:
        return None
    ra, rb = p
    vb = statistics.pvariance(rb)
    if vb == 0:
        return None
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (len(ra) * vb)


def compute(fn: str, own: Series, args: list[int], other: Series | None = None):
    """Evaluate a daily function. Arguments are already whole numbers; `other` is the symbol argument's series."""
    if any(a is None or a < 1 or a > MAX_LOOKBACK for a in args) or any(a < min_n(fn) for a in args[:1]):
        return None
    if DAILY_FUNCTIONS[fn][1]:
        return None if other is None else globals()[fn](own, other, *args)
    v = globals()[fn](own, *args)
    return v if v is None or math.isfinite(v) else None
