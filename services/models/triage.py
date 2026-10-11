"""News triage: T0 code filter -> T1 quick layer -> (escalate) -> T2 deep read -> signals.

Signals are advisory, typed, bounded and timestamped. News text is data, never instructions:
model outputs are schema-checked enums/numbers and can only gate entries or trigger exits.
"""
from __future__ import annotations

import hashlib
import json
import re

from common import audit, db
from common.config import load_yaml, universe

from . import heuristic, llm
from .quicklayer import QuickLayer, provider_available, recent_headlines
from .news_text import article_text

SOURCE = "news_agent@v1"
DEDUPE_WINDOW_S = 6 * 3600


def norm_hash(headline: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", "", headline.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return hashlib.sha1(n.encode()).hexdigest()[:16]


def t0(conn, item: dict, symbols: list[str] | None = None, replay: bool = False) -> tuple[bool, str, list[str]]:
    """Returns (keep, reason, symbols-in-universe). Replays dedupe against every earlier story, triaged or not."""
    uni = set(symbols if symbols is not None else universe())
    syms = [s for s in item["symbols"] if s in uni]
    if not syms:
        return False, "no universe symbols", []
    if heuristic.is_roundup(item["headline"]) or len(item["symbols"]) > 3:
        return False, "recap/roundup", syms
    h = norm_hash(item["headline"])
    sql = ("SELECT headline FROM news WHERE ts >= ? AND ts <= ? AND id < ?" if replay else
           "SELECT headline FROM news WHERE ts >= ? AND ts <= ? AND id != ? AND triage IN ('t1','escalated')")
    for r in conn.execute(sql, (item["ts"] - DEDUPE_WINDOW_S, item["ts"], item["id"])):
        if norm_hash(r["headline"]) == h:
            return False, "duplicate (normalized headline within 6h)", syms
    return True, "kept", syms


def _active_context(conn, symbols: list[str], catalyst: str | None) -> dict:
    notes = db.rows(conn, "SELECT symbol, text, stance FROM notes WHERE active=1 AND symbol IN (%s)"
                    % ",".join("?" * len(symbols)), symbols)
    theses = db.rows(conn, "SELECT id, text, tags, confidence FROM theses WHERE active=1")
    rel = []
    for t in theses:
        tags = {x.strip().lower() for x in (t["tags"] or "").split(",") if x.strip()}
        if tags & ({s.lower() for s in symbols} | ({catalyst} if catalyst else set())):
            rel.append(t)
    return {"notes": notes, "theses": rel}


def _should_escalate(t1: dict, ctx: dict, cfg: dict, disagreement: bool) -> list[str]:
    esc = cfg.get("escalation") or {}
    min_conf = float(esc.get("min_confidence", 0.6))
    why = []
    for k in ("catalyst", "direction"):
        if t1[k]["confidence"] < min_conf:
            why.append(f"low confidence on {k} ({t1[k]['confidence']:.2f})")
    if t1["materiality"]["value"] in (esc.get("on_materiality") or ["high"]):
        why.append("high materiality")
    if esc.get("on_active_note", True) and (ctx["notes"] or ctx["theses"]):
        why.append("active note/thesis on ticker")
    if disagreement:
        why.append("T1 providers disagree")
    return why


def _signals_from(ans: dict) -> dict:
    sign = {"positive": 1, "negative": -1, "neutral": 0}[ans["direction"]["value"]]
    score = ans.get("score")
    if score is None:
        weight = {"low": 0.3, "medium": 0.65, "high": 0.9}[ans["materiality"]["value"]]
        score = round(sign * weight * ans["direction"]["confidence"], 3)
    return {
        "news.score": (max(-1.0, min(1.0, float(score))), ans["direction"]["confidence"]),
        "news.catalyst": (ans["catalyst"]["value"], ans["catalyst"]["confidence"]),
        "news.materiality": (ans["materiality"]["value"], ans["materiality"]["confidence"]),
        "news.fundamental": (bool(ans["fundamental"]["value"]), ans["fundamental"]["confidence"]),
        "news.red_flag": (bool(ans["red_flag"]["value"]), ans["red_flag"]["confidence"]),
    }


def write_signals(conn, news_id: int, symbols: list[str], ans: dict, tier: str, ts: int):
    sigs = _signals_from(ans)
    for sym in symbols:
        for name, (val, conf) in sigs.items():
            conn.execute(
                "INSERT INTO signals(ts, symbol, name, value, confidence, news_id, source, model, tier, rationale)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, sym, name, json.dumps(val), conf, news_id, SOURCE, ans.get("model"), tier, ans.get("rationale")),
            )
    return sigs


def assess(conn, item: dict, allow_paid: bool = True, symbols: list[str] | None = None, replay: bool = False) -> dict:
    """The triage decision for one story, without writing anything.

    Returns {status: dropped|t1|escalated, reason, symbols, final, tier, detail, why}. A replay (backtest) uses no
    notes or theses, because the current ones didn't exist when the story was published."""
    cfg = load_yaml("models.yaml")
    keep, reason, syms = t0(conn, item, symbols, replay)
    if not keep:
        return {"status": "dropped", "reason": reason, "symbols": syms, "detail": {}}

    ql = QuickLayer(conn)
    t1_provider = None
    try:
        t1 = ql.decide(item, provider=None if allow_paid else "heuristic")
        t1_provider = t1["provider"]
    except Exception as e:  # noqa: BLE001 - provider down -> offline model
        try:
            t1 = ql.decide(item, provider="claude_haiku" if allow_paid and llm.available() else "heuristic")
        except Exception:
            t1 = ql.decide(item, provider="heuristic")
        t1["rationale"] += f" (fallback: {type(e).__name__})"
    disagreement = False
    if allow_paid and (cfg.get("tiers", {}).get("t1", {}) or {}).get("cross_check"):
        others = [p for p in ("gemini", "claude_haiku") if p != t1_provider]
        for p in others:
            if provider_available(p):
                try:
                    alt = ql.decide(item, provider=p)
                    disagreement = alt["direction"]["value"] != t1["direction"]["value"]
                except Exception:  # noqa: BLE001
                    pass
                break

    ctx = {"notes": [], "theses": []} if replay else _active_context(conn, syms, t1["catalyst"]["value"])
    why = _should_escalate(t1, ctx, cfg, disagreement)
    final, tier = t1, "t1"
    detail = {"t1": {k: t1[k] for k in ("catalyst", "direction", "materiality")} | {"model": t1.get("model")}}
    content, truncated = article_text(item)
    detail["article"] = {"available": bool(content), "characters_used": len(content), "truncated": truncated}
    if why:
        budget = int((cfg.get("budgets") or {}).get("t2_calls_per_day", 30))
        paid_t2 = allow_paid and llm.available()
        if paid_t2 and not replay and llm.calls_today(conn, "t2") >= budget:
            why.append(f"T2 budget exhausted ({budget}/day): using offline deep read")
            paid_t2 = False
        context = {
            "recent_headlines": recent_headlines(conn, syms[0], item["ts"]),
            "your_notes": ctx["notes"], "your_theses": ctx["theses"],
        }
        try:
            if paid_t2:
                final = ql.deep(item, context)
            else:
                final = heuristic.answer(item["headline"], item.get("summary", ""), deep=True)
                final["model"], final["provider"] = heuristic.MODEL_ID + "+deep", "heuristic"
        except Exception as e:  # noqa: BLE001
            final = t1
            why.append(f"T2 failed ({type(e).__name__}); kept T1 answer")
        tier = "t2"
        detail["escalated_because"] = why
        detail["t2"] = {k: final[k] for k in ("catalyst", "direction", "materiality")} | {"model": final.get("model")}

    if not final["new_info"]["value"]:
        return {"status": "dropped", "reason": "no new information", "symbols": syms, "detail": detail, "final": final, "tier": tier}
    if final["catalyst"]["value"] == "macro":
        return {"status": "dropped", "reason": "macro story, not company-specific", "symbols": syms, "detail": detail,
                "final": final, "tier": tier}
    return {"status": "escalated" if tier == "t2" else "t1", "reason": "; ".join(why) if why else "T1 confident",
            "symbols": syms, "final": final, "tier": tier, "detail": detail, "why": why}


def triage(conn, item: dict, stamp_ts: int, allow_paid: bool = True) -> dict:
    """Triage one news row. stamp_ts is the (simulated) time the signals become available."""
    a = assess(conn, item, allow_paid)
    if a["status"] == "dropped":
        conn.execute("UPDATE news SET triage='dropped', triage_reason=?, triaged_at=?, tier_detail=? WHERE id=?",
                     (a["reason"], stamp_ts, json.dumps(a["detail"]) if a["detail"] else None, item["id"]))
        return {"triage": "dropped", "reason": "macro" if a["reason"].startswith("macro") else a["reason"]}
    final, tier, syms, why, detail = a["final"], a["tier"], a["symbols"], a["why"], a["detail"]
    sigs = write_signals(conn, item["id"], syms, final, tier, stamp_ts)
    status = a["status"]
    conn.execute("UPDATE news SET triage=?, triage_reason=?, triaged_at=?, tier_detail=? WHERE id=?",
                 (status, "; ".join(why) if why else "T1 confident", stamp_ts, json.dumps(detail), item["id"]))
    return {"triage": status, "signals": {k: v[0] for k, v in sigs.items()}, "rationale": final.get("rationale")}


def run_pending(conn, now_ts: int, allow_paid: bool = True, actor: str = "models") -> int:
    """Triage every untriaged news item released by now_ts."""
    pending = db.rows(conn, "SELECT * FROM news WHERE triage IS NULL AND ts <= ? ORDER BY ts LIMIT 50", (now_ts,))
    for r in pending:
        item = dict(r, symbols=json.loads(r["symbols"] or "[]"))
        res = triage(conn, item, stamp_ts=now_ts, allow_paid=allow_paid)
        if res["triage"] != "dropped":
            audit.log(conn, "signal.written", actor, {"news_id": r["id"], "headline": r["headline"], **res})
    return len(pending)


def backfill(conn, latency_s: int = 30) -> int:
    """Triage historical news offline (heuristic only), stamping signals at news.ts + latency.

    Recorded once and never recomputed: these become the point-in-time history that
    backtests and the Idea Lab read.
    """
    n = 0
    for r in db.rows(conn, "SELECT * FROM news WHERE triage IS NULL ORDER BY ts"):
        item = dict(r, symbols=json.loads(r["symbols"] or "[]"))
        triage(conn, item, stamp_ts=r["ts"] + latency_s, allow_paid=False)
        n += 1
    return n
