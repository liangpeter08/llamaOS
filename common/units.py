"""Parsing helpers for the human-friendly units used in risk.yaml and the DSL."""
from __future__ import annotations

import re
from datetime import time

_PCT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")
_DUR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr)\s*$")


def is_pct(v) -> bool:
    return isinstance(v, str) and bool(_PCT.match(v))


def parse_pct(v) -> float:
    """'2%' -> 0.02. Plain numbers are returned unchanged."""
    if isinstance(v, bool):
        raise ValueError(f"expected a number or percent, got {v!r}")
    if isinstance(v, (int, float)):
        return float(v)
    m = _PCT.match(str(v))
    if not m:
        raise ValueError(f"expected a percent like '0.5%', got {v!r}")
    return float(m.group(1)) / 100.0


def parse_number(v) -> float:
    if isinstance(v, bool):
        raise ValueError(f"expected a number, got {v!r}")
    if isinstance(v, (int, float)):
        return float(v)
    if is_pct(v):
        return parse_pct(v)
    try:
        return float(str(v).replace("_", ""))
    except ValueError:
        raise ValueError(f"expected a number, got {v!r}") from None


def parse_duration(v) -> float:
    """'90s' -> 90, '2min' -> 120, '1h' -> 3600. Bare numbers are seconds."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = _DUR.match(str(v))
    if not m:
        raise ValueError(f"expected a duration like '90s' or '2min', got {v!r}")
    n, unit = float(m.group(1)), m.group(2)
    return n * {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}[unit]


def parse_hhmm(v: str) -> time:
    if not isinstance(v, str):
        raise ValueError(f"times must be quoted strings like \"09:45\", got {v!r}")
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", v)
    if not m:
        raise ValueError(f"expected HH:MM, got {v!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"invalid time {v!r}")
    return time(h, mi)


def parse_window(v: str) -> tuple[time, time]:
    if not isinstance(v, str) or "-" not in v:
        raise ValueError(f"expected a window like \"09:45-11:30\", got {v!r}")
    a, b = v.split("-", 1)
    start, end = parse_hhmm(a), parse_hhmm(b)
    if end <= start:
        raise ValueError(f"window end must be after start: {v!r}")
    return start, end
