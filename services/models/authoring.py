"""Authoring lane (offline, model-drafted, human-approved): strategy chat and the compiler."""
from __future__ import annotations

import json
import re
import time

from common import audit, db, registry
from common.config import STRATEGIES_DIR, load_risk
from dsl.explain import back_translate
from dsl.validator import validate

from . import llm
from .quicklayer import _model_id, prompt

COMPILE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "yaml": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["id", "yaml", "notes"],
    "additionalProperties": False,
}


class Offline(RuntimeError):
    pass


def _need_llm():
    if not llm.available():
        raise Offline("Claude isn't configured: set ANTHROPIC_API_KEY for the models service to enable this.")


# ---------------------------------------------------------------- chat
def strategy_context(conn, h: str) -> str:
    risk = load_risk()
    row = db.row(conn, "SELECT * FROM strategies WHERE hash=?", (h,))
    if not row:
        raise KeyError(h)
    text = registry.text_of(conn, h) or ""
    v = validate(text, risk)
    bt = json.loads(row["backtest"] or "null")
    if bt:
        bt = {k: bt[k] for k in ("metrics", "per_day", "rejected_by_guard", "signal_note", "days") if k in bt} | {
            "sample_trades": bt.get("trades", [])[:15]}
    intents = db.rows(conn, "SELECT id, ts, symbol, qty, entry, stop, target, status, decision, reason FROM intents"
                            " WHERE strategy_hash=? ORDER BY id DESC LIMIT 10", (h,))
    trades = db.rows(conn, "SELECT id, symbol, qty, entry_price, exit_price, exit_kind, pnl, r_multiple FROM trades"
                           " WHERE strategy_hash=? ORDER BY id DESC LIMIT 20", (h,))
    parts = [
        f"<strategy_file status=\"{row['status']}\" mode=\"{row['mode']}\" sha256=\"{h}\">\n{text}\n</strategy_file>",
        f"<back_translation>\n{back_translate(v.strategy) if v.strategy else 'n/a (invalid)'}\n</back_translation>",
        f"<validation>{json.dumps(v.as_dict())}</validation>",
        f"<backtest_report>{json.dumps(bt, default=str) if bt else 'none yet'}</backtest_report>",
        f"<decision_records>{json.dumps(intents, default=str)}</decision_records>",
        f"<live_and_paper_trades>{json.dumps(trades, default=str)}</live_and_paper_trades>",
    ]
    return "\n".join(parts)


def trade_context(conn, trade_id: int) -> str:
    t = db.row(conn, "SELECT * FROM trades WHERE id=?", (trade_id,))
    if not t:
        raise KeyError(trade_id)
    it = db.row(conn, "SELECT * FROM intents WHERE id=?", (t["intent_id"],)) if t["intent_id"] else None
    fills = db.rows(conn, "SELECT * FROM fills WHERE symbol=? AND (intent_id=? OR intent_id IS NULL) ORDER BY ts", (t["symbol"], t["intent_id"]))
    return (f"<trade>{json.dumps(t, default=str)}</trade>\n<intent_decision_record>{json.dumps(it, default=str)}</intent_decision_record>\n"
            f"<fills>{json.dumps(fills, default=str)}</fills>")


def chat(conn, thread: str, message: str, actor: str) -> dict:
    """thread: 'strategy:<hash>:<n>' or 'trade:<id>:<n>'. Each thread is a fresh Claude conversation."""
    _need_llm()
    kind, ref, _ = thread.split(":", 2)
    history = db.rows(conn, "SELECT role, content FROM chats WHERE thread=? ORDER BY id", (thread,))
    if not history:
        ctx = strategy_context(conn, ref) if kind == "strategy" else trade_context(conn, int(ref))
        first = f"Context for this conversation:\n{ctx}\n\nMy question: {message}"
        msgs = [{"role": "user", "content": first}]
        stored_user = first
    else:
        msgs = [{"role": r["role"], "content": r["content"]} for r in history] + [{"role": "user", "content": message}]
        stored_user = message
    model = _model_id("claude_opus")
    text, _, _ = llm.call(conn, model=model, task="chat", system=prompt("chat@v1"), messages=msgs,
                          max_tokens=16000, effort="medium", cache=True)
    now = time.time()
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "user", stored_user, actor))
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (?,?,?,?,?)", (now, thread, "assistant", text, model))
    audit.log(conn, "chat", actor, {"thread": thread, "question": message[:2000], "answer": text[:4000], "model": model})
    diff = re.search(r"```diff\n(.*?)```", text, re.S)
    return {"answer": text, "model": model, "has_diff": bool(diff)}


# ---------------------------------------------------------------- compiler
def compile_text(conn, request: str, actor: str) -> dict:
    """English -> DSL draft via Claude Opus, validated; one repair round on validator errors."""
    _need_llm()
    risk = load_risk()
    example = (STRATEGIES_DIR / "orb_news_long.v1.yaml").read_text() if (STRATEGIES_DIR / "orb_news_long.v1.yaml").exists() else ""
    model = _model_id("claude_opus")
    system = (prompt("compiler@v1")
              .replace("{max_risk}", f"{risk.max_risk:.2%}").replace("{max_position}", f"{risk.max_position:.0%}")
              .replace("{max_trades}", str(risk.max_trades_per_day))
              .replace("{entries}", f"{risk.entries[0]:%H:%M}-{risk.entries[1]:%H:%M} ET")
              .replace("{flatten_by}", f"{risk.flatten_by:%H:%M} ET").replace("{long_only}", str(not risk.allow_short).lower())
              .replace("{example}", example))
    msgs = [{"role": "user", "content": f"Compiler model id: {model}\n\n<request>\n{request}\n</request>"}]
    attempts = []
    for _ in range(2):
        _, parsed, resp = llm.call(conn, model=model, task="compile", system=system, messages=msgs,
                                   max_tokens=16000, effort="high", schema=COMPILE_SCHEMA)
        text = parsed["yaml"].strip() + "\n"
        v = validate(text, risk)
        attempts.append({"ok": v.ok, "errors": v.errors})
        if v.ok:
            break
        msgs += [{"role": "assistant", "content": json.dumps(parsed)},
                 {"role": "user", "content": "The validator rejected it:\n- " + "\n- ".join(v.errors) + "\nReturn a corrected file."}]
    sid = re.sub(r"[^a-z0-9_]", "_", (v.strategy.id if v.ok else parsed["id"]).lower())[:60] or "draft"
    result = {"ok": v.ok, "errors": v.errors, "warnings": v.warnings, "notes": parsed["notes"], "attempts": attempts}
    if v.ok:
        path = registry.write_new_version(sid, text)
        registry.sync(conn, risk)
        result["path"] = str(path.name)
        result["hash"] = validate(path.read_text(), risk).hash
    else:
        result["yaml"] = text
    audit.log(conn, "strategy.compiled", actor, {"request": request, "model": model, **{k: v for k, v in result.items() if k != "yaml"}})
    return result


def apply_diff(conn, h: str, diff_text: str, actor: str) -> dict:
    """Accept a chat-proposed unified diff: creates a NEW draft version; the original is untouched."""
    risk = load_risk()
    old = registry.text_of(conn, h)
    if old is None:
        raise KeyError(h)
    new = _patch(old, diff_text)
    sid = db.row(conn, "SELECT sid FROM strategies WHERE hash=?", (h,))["sid"]
    v = validate(re.sub(r"(?m)^version:\s*\d+\s*$", f"version: {registry.next_version(sid)}", new, count=1), risk)
    if not v.ok:
        return {"ok": False, "errors": v.errors}
    path = registry.write_new_version(sid, new)
    registry.sync(conn, risk)
    audit.log(conn, "strategy.diff_applied", actor, {"from": h, "to": path.name})
    return {"ok": True, "path": path.name}


def _patch(text: str, diff: str) -> str:
    """Minimal unified-diff applier (context must match exactly)."""
    lines = text.splitlines()
    out, i = [], 0
    hunks = re.split(r"(?m)^@@.*@@.*$", diff)[1:]
    if not hunks:
        raise ValueError("no hunks in diff")
    for h in hunks:
        body = [ln for ln in h.strip("\n").splitlines() if not ln.startswith(("---", "+++"))]
        old_seq = [ln[1:] for ln in body if ln.startswith((" ", "-"))]
        new_seq = [ln[1:] for ln in body if ln.startswith((" ", "+"))]
        for k in range(i, len(lines) - len(old_seq) + 1):
            if lines[k:k + len(old_seq)] == old_seq:
                out += lines[i:k] + new_seq
                i = k + len(old_seq)
                break
        else:
            raise ValueError("diff context does not match the strategy file")
    out += lines[i:]
    return "\n".join(out) + "\n"
