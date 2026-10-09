"""Exchange-time helpers. All market times are US/Eastern; storage is epoch seconds."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
BARS_PER_DAY = 390


def et(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ET)


def ts_at(d: date, t: time) -> int:
    return int(datetime.combine(d, t, ET).timestamp())


def open_ts(d: date) -> int:
    return ts_at(d, OPEN)


def close_ts(d: date) -> int:
    return ts_at(d, CLOSE)


def prior_close_ts(d: date) -> int:
    return close_ts(prev_weekday(d))


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def weekdays_before(d: date, n: int) -> list[date]:
    out, cur = [], d
    for _ in range(n):
        cur = prev_weekday(cur)
        out.append(cur)
    return list(reversed(out))


def session_date(today: date | None = None) -> date:
    """The simulated trading day: today if it's a weekday, otherwise the next weekday."""
    d = today or datetime.now(ET).date()
    return d if d.weekday() < 5 else next_weekday(d)


def fmt(ts: float | None, with_date: bool = False) -> str:
    if ts is None:
        return "—"
    return et(ts).strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def in_window(ts: float, window: tuple[time, time]) -> bool:
    t = et(ts).time()
    return window[0] <= t <= window[1]
