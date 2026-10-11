"""Account-configured market input feeds (config/market_inputs.yaml) and stored index history.

The broker reads index quotes through these feeds; the api checks them before a strategy that needs an
index can be promoted to paper. There is no proxy fallback: an index without a feed is unavailable.
"""
from __future__ import annotations

from . import db
from .config import load_yaml

PROVIDERS = ("yahoo",)


def feeds() -> dict[str, dict]:
    """instrument -> {provider, symbol} for every configured, supported index feed."""
    out = {}
    for inst, spec in (load_yaml("market_inputs.yaml").get("index_feeds") or {}).items():
        if isinstance(spec, dict) and spec.get("provider") in PROVIDERS and spec.get("symbol"):
            out[str(inst)] = {"provider": spec["provider"], "symbol": str(spec["symbol"])}
    return out


def missing_feeds(s) -> list[str]:
    """Index instruments a strategy needs that have no configured feed."""
    v2 = getattr(s, "v2", None)
    if v2 is None:
        return []
    have = feeds()
    return [inst for inst in v2.index_instruments if inst not in have]


def prior_close(conn, instrument: str, day: str) -> float | None:
    r = db.row(conn, "SELECT c FROM index_daily WHERE instrument=? AND day<? ORDER BY day DESC LIMIT 1", (instrument, day))
    return r["c"] if r else None


def store_history(conn, instrument: str, rows: list[tuple], source: str) -> int:
    """rows: (day, o, h, l, c) for completed sessions."""
    conn.executemany("INSERT INTO index_daily(instrument, day, o, h, l, c, source) VALUES (?,?,?,?,?,?,?)"
                     " ON CONFLICT(instrument, day) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c, source=excluded.source",
                     [(instrument, d, o, h, lo, c, source) for d, o, h, lo, c in rows])
    return len(rows)
