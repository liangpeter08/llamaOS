"""Owner-managed positions and broker reconciliation settings (shared by broker, watchdog and api).

Manual trades are ordinary intents owned by the pseudo-strategy MANUAL, so every existing view
(positions, fills, trades, P&L) picks them up unchanged. Positions the owner marks "hold overnight"
are left alone by the intraday flatten deadline; the kill switch still closes everything.
"""
from __future__ import annotations

from . import db

MANUAL = "manual"              # strategy_hash and sid of owner-placed trades
AUTO_KEY = "reconcile.auto"    # book closes/opens made directly in Alpaca without asking (default on)
HOLD_KEY = "manual.hold_overnight"
COMMANDS = ("manual_open", "manual_close", "manual_protect", "manual_adopt", "manual_cancel", "manual_hold",
            "reconcile_accept")


def auto(conn) -> bool:
    v = db.kv_get(conn, AUTO_KEY)
    return True if v is None else bool(v)


def holds(conn) -> set[str]:
    return set(db.kv_get(conn, HOLD_KEY) or [])


def set_hold(conn, symbol: str, on: bool) -> set[str]:
    h = holds(conn)
    (h.add if on else h.discard)(symbol)
    db.kv_set(conn, HOLD_KEY, sorted(h))
    return h
