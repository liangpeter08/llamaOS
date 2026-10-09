"""Hash-chained, append-only audit log.

Each entry's hash covers (prev_hash, ts, kind, actor, payload). Editing or deleting
any row breaks the chain from that point on, which `verify` reports.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import db

GENESIS = "0" * 64


def _digest(prev: str, ts: float, kind: str, actor: str, payload: str) -> str:
    h = hashlib.sha256()
    for part in (prev, repr(ts), kind, actor, payload):
        h.update(part.encode())
        h.update(b"\x1f")
    return h.hexdigest()


def log(conn: db.Conn, kind: str, actor: str, payload: dict | None = None) -> str:
    body = json.dumps(payload or {}, sort_keys=True, default=str)
    ts = time.time()
    with db.tx(conn):
        conn.execute("SELECT pg_advisory_xact_lock(?)", (db.AUDIT_LOCK_KEY,))
        last = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else GENESIS
        h = _digest(prev, ts, kind, actor, body)
        conn.execute(
            "INSERT INTO audit(ts, kind, actor, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
            (ts, kind, actor, body, prev, h),
        )
    return h


def verify(conn: db.Conn) -> dict:
    prev = GENESIS
    n = 0
    for r in conn.execute("SELECT * FROM audit ORDER BY id"):
        if r["prev_hash"] != prev:
            return {"ok": False, "checked": n, "broken_at": r["id"], "why": "prev_hash mismatch"}
        if _digest(prev, r["ts"], r["kind"], r["actor"], r["payload"]) != r["hash"]:
            return {"ok": False, "checked": n, "broken_at": r["id"], "why": "hash mismatch"}
        prev = r["hash"]
        n += 1
    return {"ok": True, "checked": n, "head": prev}
