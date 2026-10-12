"""Strategy registry: status and approvals keyed by the file's SHA-256.

Promoting a strategy never changes its file. Any edit is a new hash, so it starts at draft.

    draft -> backtested -> paper -> live ;  draft -> paper (owner, no backtest) ;  any -> retired
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
# Execution mode = account stage + approval. *_manual holds each trade for the owner; *_auto submits it.
# live_* needs status live (hash approved). There is one broker account per stack: until a real-money
# connection exists, live modes trade on whatever account the stack runs (paper today).
MODES = ("paper_manual", "paper_auto", "live_manual", "live_auto")
LEGACY_MODES = {"manual": "paper_manual", "auto": "paper_auto", "paper": "paper_auto"}


def norm_mode(m: str | None) -> str:
    m = LEGACY_MODES.get(m or "", m or "")
    return m if m in MODES else "paper_manual"


def is_auto(m: str | None) -> bool:
    return norm_mode(m).endswith("_auto")


def demote_live_auto(conn, actor: str, reason: str) -> list[str]:
    """Kill switch tripped: every live_auto strategy drops to live_manual (the owner re-enables it after review)."""
    rows = db.rows(conn, "SELECT hash, sid FROM strategies WHERE mode='live_auto'")
    if rows:
        conn.execute("UPDATE strategies SET mode='live_manual', updated_at=? WHERE mode='live_auto'", (time.time(),))
        for r in rows:
            audit.log(conn, "strategy.mode", actor, {"hash": r["hash"], "mode": "live_manual", "from": "live_auto", "why": f"kill switch: {reason}"})
    return [r["hash"] for r in rows]


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
             "paper_manual", now, now, json.dumps(v.as_dict())),
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


def runnable(conn, risk: RiskConfig, kind: str = "intraday") -> list[tuple[dict, Strategy]]:
    """Paper/live strategies of one kind: 'intraday' (bar-by-bar, flat by the close) or 'portfolio'."""
    out = []
    for r in db.rows(conn, "SELECT * FROM strategies WHERE status IN ('paper','live') ORDER BY sid, version"):
        s, why = load(conn, r["hash"], risk)
        if s and getattr(s, "kind", "intraday") != kind:
            continue
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
    if status == "live":  # entering live keeps manual approval; live_auto is a separate, confirmed owner choice
        conn.execute("UPDATE strategies SET mode='live_manual' WHERE hash=? AND mode IN ('paper_manual', 'paper_auto', 'manual', 'auto', 'paper')", (h,))
    else:  # live modes need live status: keep the approval behaviour, move back to the paper stage
        conn.execute("UPDATE strategies SET mode=REPLACE(mode, 'live_', 'paper_') WHERE hash=? AND mode IN ('live_manual', 'live_auto')", (h,))
    audit.log(conn, "strategy.status", actor, {"hash": h, "status": status, **{k: v for k, v in extra.items() if k != 'backtest'}})


def set_category(conn, sid: str, category: str, actor: str):
    """Category of a strategy line (every version). Blank clears it."""
    category = category.strip()[:60]
    if category:
        conn.execute("INSERT INTO strategy_categories(sid, category, updated_at) VALUES (?,?,?)"
                     " ON CONFLICT(sid) DO UPDATE SET category=excluded.category, updated_at=excluded.updated_at",
                     (sid, category, time.time()))
    else:
        conn.execute("DELETE FROM strategy_categories WHERE sid=?", (sid,))
    audit.log(conn, "strategy.category", actor, {"sid": sid, "category": category or None})


def set_archived(conn, h: str, archived: bool, actor: str):
    r = db.row(conn, "SELECT status FROM strategies WHERE hash=?", (h,))
    if not r:
        raise ValueError("no such strategy")
    if archived and r["status"] in RUNNABLE:
        raise ValueError(f"{r['status'].title()} strategies can't be archived. Retire it first.")
    conn.execute("UPDATE strategies SET archived_at=? WHERE hash=?", (time.time() if archived else None, h))
    audit.log(conn, "strategy.archived" if archived else "strategy.unarchived", actor, {"hash": h})


# kv entries keyed by one strategy hash (monitoring, overlays, catalog pin, sleeve inputs, assumption blocks).
_KV_KEYS = ("monitoring.strategy.{h}", "overlays.enforce.{h}", "scenario_catalog.pin.{h}",
            "portfolio.inputs.{h}", "portfolio.observed.{h}", "assumption.block.{h}")


def delete_blockers(conn, h: str) -> list[str]:
    """Why this version can't be permanently deleted right now (empty when it can)."""
    from .sleeves import OPEN, holdings
    r = db.row(conn, "SELECT status FROM strategies WHERE hash=?", (h,))
    if not r:
        return ["no such strategy"]
    out = []
    if r["status"] in RUNNABLE:
        out.append(f"it is running on {r['status']}; retire it first")
    if db.row(conn, "SELECT symbol FROM positions WHERE strategy_hash=? AND qty<>0 LIMIT 1", (h,)):
        out.append("it has an open position")
    if any(x.qty for x in holdings(conn, h)[0].values()):
        out.append("its portfolio sleeve still holds shares")
    if db.row(conn, f"SELECT id FROM portfolio_orders WHERE strategy_hash=? AND status IN {OPEN} LIMIT 1", (h,)) or \
            db.row(conn, "SELECT id FROM intents WHERE strategy_hash=? AND status IN ('pending','approved','rechecking','submitting','submitted') LIMIT 1", (h,)):
        out.append("it has working orders")
    if db.row(conn, "SELECT id FROM backtest_runs WHERE strategy_hash=? AND status IN ('queued','running') LIMIT 1", (h,)):
        out.append("a backtest of it is still running")
    return out


def delete(conn, h: str, actor: str, delete_runs: bool = False) -> dict:
    """Permanently delete one version: its file, registry row and per-hash state.

    Trades, intents, orders and the audit log are history and stay. Saved backtest runs keep a copy of the
    strategy text, so they stay too unless delete_runs is set.
    """
    blockers = delete_blockers(conn, h)
    if blockers:
        raise ValueError("Can't delete: " + "; ".join(blockers) + ".")
    r = db.row(conn, "SELECT sid, version, path FROM strategies WHERE hash=?", (h,))
    p = STRATEGIES_DIR.parent / r["path"]
    with db.tx(conn):
        for table in ("portfolio_state", "portfolio_days", "portfolio_events", "assumption_checks", "assumption_replays"):
            conn.execute(f"DELETE FROM {table} WHERE strategy_hash=?", (h,))
        conn.execute("DELETE FROM kv WHERE k = ANY(?)", ([k.format(h=h) for k in _KV_KEYS],))
        runs = 0
        if delete_runs:
            ids = [x["id"] for x in db.rows(conn, "SELECT id FROM backtest_runs WHERE strategy_hash=?", (h,))]
            conn.execute("UPDATE backtest_runs SET parent_id=NULL WHERE parent_id = ANY(?)", (ids,))
            runs = conn.execute("DELETE FROM backtest_runs WHERE strategy_hash=?", (h,)).rowcount
        conn.execute("DELETE FROM strategies WHERE hash=?", (h,))
        last = not db.row(conn, "SELECT hash FROM strategies WHERE sid=? LIMIT 1", (r["sid"],))
        if last:
            conn.execute("DELETE FROM strategy_categories WHERE sid=?", (r["sid"],))
            conn.execute("DELETE FROM strategy_funds WHERE sid=?", (r["sid"],))
        # Remove the file last, inside the transaction: if it fails, nothing is deleted and sync can't re-register it.
        if p.exists() and file_hash(p.read_text()) == h:
            p.unlink()
        audit.log(conn, "strategy.deleted", actor, {"hash": h, "sid": r["sid"], "version": r["version"],
                                                     "path": r["path"], "backtest_runs_deleted": runs})
    return {"sid": r["sid"], "version": r["version"], "runs": runs, "last_version": last}


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
