"""Owner switch for sending news to Claude: on/off, plus optional active hours (US/Eastern).

Off, or outside the active hours, news triage uses Laya, Gemini or the offline keyword model only.
The window is checked against wall-clock time when a story is triaged, because the point is to bound
when Claude is called (and billed), not the simulated market time.
"""
from __future__ import annotations

import re
import time

from common import db
from common.timeutil import et

ON_KEY = "news.send_to_claude"
HOURS_KEY = "news.claude_hours"
DEFAULT_HOURS = {"enabled": False, "start": "09:00", "end": "17:00", "weekdays_only": False}
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def valid_hhmm(s: str) -> bool:
    return bool(_HHMM.fullmatch(s or ""))


def _minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def settings(conn) -> dict:
    stored = db.kv_get(conn, HOURS_KEY) or {}
    hours = DEFAULT_HOURS | {k: stored[k] for k in DEFAULT_HOURS if k in stored}
    if not (valid_hhmm(hours["start"]) and valid_hhmm(hours["end"])):
        hours = DEFAULT_HOURS | {"enabled": bool(hours["enabled"])}
    return {"on": db.kv_get(conn, ON_KEY, True) is not False, "hours": hours}


def in_hours(hours: dict, ts: float) -> bool:
    """True when ts falls in the window. start > end wraps past midnight; start == end means all day."""
    if not hours.get("enabled"):
        return True
    d = et(ts)
    if hours.get("weekdays_only") and d.weekday() >= 5:
        return False
    m, s, e = d.hour * 60 + d.minute, _minutes(hours["start"]), _minutes(hours["end"])
    if s == e:
        return True
    return s <= m < e if s < e else (m >= s or m < e)


def allowed(conn, ts: float | None = None) -> bool:
    st = settings(conn)
    return st["on"] and in_hours(st["hours"], time.time() if ts is None else ts)


def status(conn, ts: float | None = None) -> dict:
    """Settings plus whether Claude is usable right now, for the dashboard."""
    st = settings(conn)
    return st | {"active_now": st["on"] and in_hours(st["hours"], time.time() if ts is None else ts)}
