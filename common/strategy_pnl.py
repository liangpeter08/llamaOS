"""Strategy P&L from the source records, shared by the funds view, the equity chart and the performance page.

Lifetime P&L of a strategy line at a moment = every trade it closed up to then (realized) + its open
positions marked then (unrealized). P&L over a period is the difference of two lifetime values, so a
position held across sessions or across an allocation change is counted once, for the part of its move
that happened inside the period.

Marks in the past come from equity_history: each broker row stores, per strategy, the session so far
(trades closed since the session opened + open positions marked to that minute). Adding the trades closed
before that session gives the lifetime value at that minute.
"""
from __future__ import annotations

import bisect
import json
import re
from datetime import date

from . import db
from . import timeutil as T

_VERSION = re.compile(r" v\d+$")


def sid_of(label: str) -> str:
    """'orb_news_long v2' (simulator and replay labels) -> 'orb_news_long' (the paper broker's key)."""
    return _VERSION.sub("", label or "") or "unmanaged"


def session_pnl(raw: str | None) -> dict[str, float]:
    """A row's strat_pnl JSON keyed by strategy line (versions of one line are summed)."""
    out: dict[str, float] = {}
    for k, v in json.loads(raw or "{}").items():
        out[sid_of(k)] = out.get(sid_of(k), 0.0) + float(v or 0.0)
    return out


def signed_qty(p: dict) -> float:
    q = float(p["qty"] or 0)
    return -abs(q) if p.get("side") == "short" else abs(q)


def unrealized(conn) -> dict[str, float]:
    """Open P&L per strategy line from the positions table (marked at the last broker sync)."""
    out: dict[str, float] = {}
    for p in db.rows(conn, "SELECT sid, qty, side, avg_price, last_price FROM positions"):
        if p["last_price"] and p["avg_price"]:
            k = p["sid"] or "unmanaged"
            out[k] = out.get(k, 0.0) + (p["last_price"] - p["avg_price"]) * signed_qty(p)
    return out


class Realized:
    """Cumulative realized P&L per strategy line at any time (one query, then bisect)."""

    def __init__(self, conn):
        self.ts: dict[str, list[float]] = {}
        self.cum: dict[str, list[float]] = {}
        for r in db.rows(conn, "SELECT COALESCE(sid,'unmanaged') sid, exit_ts, pnl FROM trades"
                               " WHERE exit_ts IS NOT NULL AND pnl IS NOT NULL ORDER BY exit_ts, id"):
            ts, cum = self.ts.setdefault(r["sid"], []), self.cum.setdefault(r["sid"], [])
            ts.append(float(r["exit_ts"]))
            cum.append((cum[-1] if cum else 0.0) + float(r["pnl"]))

    @property
    def sids(self) -> set[str]:
        return set(self.ts)

    def before(self, sid: str, ts: float) -> float:
        """Realized P&L of trades that closed strictly before `ts`."""
        i = bisect.bisect_left(self.ts.get(sid, []), ts)
        return self.cum[sid][i - 1] if i else 0.0

    def upto(self, sid: str, ts: float) -> float:
        """Realized P&L of trades that closed at or before `ts`."""
        i = bisect.bisect_right(self.ts.get(sid, []), ts)
        return self.cum[sid][i - 1] if i else 0.0

    def total(self, sid: str) -> float:
        return self.cum[sid][-1] if self.cum.get(sid) else 0.0

    def all_before(self, ts: float) -> dict[str, float]:
        return {sid: self.before(sid, ts) for sid in self.ts}


def session_open(session: str) -> int:
    return T.open_ts(date.fromisoformat(session))


def row_lifetime(row: dict, realized: Realized) -> dict[str, float]:
    """Lifetime P&L per strategy line at a broker equity_history row (not a replay row)."""
    before = realized.all_before(session_open(row["session"]))
    for sid, v in session_pnl(row["strat_pnl"]).items():
        before[sid] = before.get(sid, 0.0) + v
    return before


def lifetime_now(conn, realized: Realized | None = None) -> dict[str, float]:
    realized = realized or Realized(conn)
    out = {sid: realized.total(sid) for sid in realized.sids}
    for sid, v in unrealized(conn).items():
        out[sid] = out.get(sid, 0.0) + v
    return out


def lifetime_at(conn, ts: float, realized: Realized | None = None) -> dict[str, float]:
    """Lifetime P&L per strategy line at `ts`: trades closed by then + the open P&L of the last broker
    mark at or before it (positions are only marked during sessions, so overnight this is the prior close)."""
    realized = realized or Realized(conn)
    row = db.row(conn, "SELECT session, strat_pnl, ts FROM equity_history WHERE source<>'replay' AND ts<=? ORDER BY ts DESC LIMIT 1", (ts,))
    if not row:
        return {sid: realized.upto(sid, ts) for sid in realized.sids}
    # a trade closing between the mark and `ts` is already in the mark as open P&L: don't add it again
    return row_lifetime(row, realized)


def marks(conn, since: float, until: float | None = None, realized: Realized | None = None) -> list[tuple[float, dict[str, float]]]:
    """(ts, lifetime P&L per strategy line) at every broker mark in [since, until]."""
    realized = realized or Realized(conn)
    q = "SELECT ts, session, strat_pnl FROM equity_history WHERE source<>'replay' AND ts>=?"
    args: tuple = (since,)
    if until is not None:
        q += " AND ts<=?"
        args += (until,)
    return [(float(r["ts"]), row_lifetime(r, realized)) for r in db.rows(conn, q + " ORDER BY ts", args)]
