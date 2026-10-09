"""Strategy registry: status and approvals keyed by the file's SHA-256.

Promoting a strategy never changes its file. Any edit is a new hash, so it starts at draft.

    draft -> backtested -> paper -> live ;  any -> retired
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from dsl.loader import file_hash
from dsl.validator import Strategy, validate

from . import audit, db
from .config import STRATEGIES_DIR, RiskConfig

STATUSES = ("draft", "backtested", "paper", "live", "retired")
RUNNABLE = ("paper", "live")


def sync(conn, risk: RiskConfig) -> list[dict]:
    """Register every strategy file not yet in the registry (as draft)."""
    added = []
    for p in sorted(STRATEGIES_DIR.glob("*.yaml")):
        text = p.read_text()
        h = file_hash(text)
        if db.row(conn, "SELECT hash FROM strategies WHERE hash=?", (h,)):
            continue
        v = validate(text, risk)
        sid, ver = _id_version(p, v)
        now = time.time()
        conn.execute(
            "INSERT INTO strategies(hash, sid, version, path, status, mode, created_at, updated_at, validation)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (h, sid, ver, str(p.relative_to(STRATEGIES_DIR.parent)), "draft",
             v.strategy.mode if v.ok else "manual", now, now, json.dumps(v.as_dict())),
        )
        audit.log(conn, "strategy.registered", "system", {"hash": h, "sid": sid, "version": ver, "valid": v.ok})
        added.append({"hash": h, "sid": sid, "version": ver, "ok": v.ok})
    return added


def _id_version(p: Path, v) -> tuple[str, int]:
    if v.strategy:
        return v.strategy.id, v.strategy.version
    m = re.match(r"(.+)\.v(\d+)\.yaml$", p.name)
    return (m.group(1), int(m.group(2))) if m else (p.stem, 0)


def text_of(conn, h: str) -> str | None:
    r = db.row(conn, "SELECT path FROM strategies WHERE hash=?", (h,))
    if not r:
        return None
    p = STRATEGIES_DIR.parent / r["path"]
    return p.read_text() if p.exists() else None


def load(conn, h: str, risk: RiskConfig) -> tuple[Strategy | None, str]:
    """Load and re-verify a registered strategy. The file must still hash to h."""
    text = text_of(conn, h)
    if text is None:
        return None, "file missing"
    if file_hash(text) != h:
        return None, "file changed since registration (hash mismatch)"
    v = validate(text, risk)
    if not v.ok:
        return None, "; ".join(v.errors)
    return v.strategy, "ok"


def runnable(conn, risk: RiskConfig) -> list[tuple[dict, Strategy]]:
    out = []
    for r in db.rows(conn, "SELECT * FROM strategies WHERE status IN ('paper','live') ORDER BY sid, version"):
        s, why = load(conn, r["hash"], risk)
        if s:
            out.append((r, s))
        else:
            audit.log(conn, "strategy.skipped", "broker", {"hash": r["hash"], "sid": r["sid"], "why": why})
    return out


def set_status(conn, h: str, status: str, actor: str, **extra):
    assert status in STATUSES
    sets = ["status=?", "updated_at=?"]
    args: list = [status, time.time()]
    for k, v in extra.items():
        sets.append(f"{k}=?")
        args.append(v)
    conn.execute(f"UPDATE strategies SET {', '.join(sets)} WHERE hash=?", (*args, h))
    audit.log(conn, "strategy.status", actor, {"hash": h, "status": status, **{k: v for k, v in extra.items() if k != 'backtest'}})


def next_version(sid: str) -> int:
    vs = [int(m.group(1)) for p in STRATEGIES_DIR.glob(f"{sid}.v*.yaml")
          if (m := re.match(rf"{re.escape(sid)}\.v(\d+)\.yaml$", p.name))]
    return max(vs, default=0) + 1


def write_new_version(sid: str, text: str) -> Path:
    """Write a new strategy file. Never overwrites an existing version."""
    ver = next_version(sid)
    text = re.sub(r"(?m)^version:\s*\d+\s*$", f"version: {ver}", text, count=1)
    p = STRATEGIES_DIR / f"{sid}.v{ver}.yaml"
    if p.exists():
        raise FileExistsError(p)
    p.write_text(text)
    return p
