"""Read/write portfolio sleeve state in PostgreSQL (shared by broker, watchdog and api).

Holdings are never stored as a number that can drift: they are replayed from filled sleeve
orders in order, so the cost basis and realized P&L always match the broker's fills.
"""
from __future__ import annotations

import json
import time

from core.portfolio import Holding, Sleeve, replay_fills

from . import db
from .config import STRATEGIES_DIR, RiskConfig

OPEN = ("pending", "approved", "submitting", "submitted")


def filled_orders(conn, h: str | None = None) -> list[dict]:
    if h:
        return db.rows(conn, "SELECT * FROM portfolio_orders WHERE strategy_hash=? AND filled_qty>0 ORDER BY id", (h,))
    return db.rows(conn, "SELECT * FROM portfolio_orders WHERE filled_qty>0 ORDER BY id")


def holdings(conn, h: str) -> tuple[dict[str, Holding], float, list[dict]]:
    """(holdings, realized P&L, closed lots) of one sleeve."""
    return replay_fills(filled_orders(conn, h))


def all_holdings(conn) -> dict[str, dict[str, Holding]]:
    by: dict[str, list[dict]] = {}
    for r in filled_orders(conn):
        by.setdefault(r["strategy_hash"], []).append(r)
    return {h: replay_fills(rows)[0] for h, rows in by.items()}


def owned_symbols(conn) -> dict[str, str]:
    """symbol -> strategy hash, for every symbol a sleeve holds or has an order working in."""
    out: dict[str, str] = {}
    for h, hold in all_holdings(conn).items():
        for sym, x in hold.items():
            if x.qty > 0:
                out[sym] = h
    for r in db.rows(conn, f"SELECT DISTINCT symbol, strategy_hash FROM portfolio_orders WHERE status IN {OPEN}"):
        out.setdefault(r["symbol"], r["strategy_hash"])
    return out


def load_state(conn, h: str) -> Sleeve | None:
    r = db.row(conn, "SELECT state FROM portfolio_state WHERE strategy_hash=?", (h,))
    return Sleeve.from_json(json.loads(r["state"])) if r else None


def save_state(conn, h: str, sid: str, sl: Sleeve) -> None:
    conn.execute("INSERT INTO portfolio_state(strategy_hash, sid, state, updated_at) VALUES (?,?,?,?)"
                 " ON CONFLICT(strategy_hash) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                 (h, sid, json.dumps(sl.to_json()), time.time()))


def portfolio_symbols(conn, risk: RiskConfig) -> set[str]:
    """Symbols named by any registered, valid portfolio strategy (drafts included, so backtests have data)."""
    from dsl.validator import validate

    out: set[str] = set()
    for r in db.rows(conn, "SELECT path, status FROM strategies WHERE status != 'retired'"):
        p = STRATEGIES_DIR.parent / r["path"]
        if not p.exists() or "kind: portfolio" not in (text := p.read_text()):
            continue
        v = validate(text, risk)
        if v.ok and v.strategy.kind == "portfolio":
            out |= set(v.strategy.symbols)
    return out
