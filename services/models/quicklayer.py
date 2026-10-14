"""T1 quick layer and T2 deep read behind one interface.

    QuickLayer.decide(item, questions) -> answers dict

config/models.yaml maps each tier to an ordered list of providers; the first available one
answers. Providers: heuristic (offline, always available), claude_haiku, claude_sonnet,
gemini (REST, needs GEMINI_API_KEY + GEMINI_MODEL).
"""
from __future__ import annotations

import json
import os
import time

from common import db, news_claude
from common.config import PROMPTS_DIR, load_yaml
from dsl.builtins import CATALYSTS

from . import heuristic, llm, laya_provider
from .news_text import model_item

T1_QUESTIONS = ("new_info", "catalyst", "direction", "materiality", "fundamental", "red_flag")

_ENUMS = {
    "catalyst": list(CATALYSTS),
    "direction": ["positive", "negative", "neutral"],
    "materiality": ["low", "medium", "high"],
}

T1_SCHEMA = {
    "type": "object",
    "properties": {
        "new_info": {"type": "boolean"},
        "catalyst": {"type": "string", "enum": _ENUMS["catalyst"]},
        "catalyst_confidence": {"type": "number"},
        "direction": {"type": "string", "enum": _ENUMS["direction"]},
        "direction_confidence": {"type": "number"},
        "materiality": {"type": "string", "enum": _ENUMS["materiality"]},
        "materiality_confidence": {"type": "number"},
        "fundamental": {"type": "boolean"},
        "red_flag": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
    "required": [
        "new_info", "catalyst", "catalyst_confidence", "direction", "direction_confidence",
        "materiality", "materiality_confidence", "fundamental", "red_flag", "rationale",
    ],
    "additionalProperties": False,
}

T2_SCHEMA = {
    "type": "object",
    "properties": {
        "new_info": {"type": "boolean"},
        "catalyst": {"type": "string", "enum": _ENUMS["catalyst"]},
        "direction": {"type": "string", "enum": _ENUMS["direction"]},
        "materiality": {"type": "string", "enum": _ENUMS["materiality"]},
        "fundamental": {"type": "boolean"},
        "red_flag": {"type": "boolean"},
        "score": {"type": "number"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["new_info", "catalyst", "direction", "materiality", "fundamental", "red_flag",
                 "score", "confidence", "rationale"],
    "additionalProperties": False,
}


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def _model_id(key: str) -> str | None:
    v = (load_yaml("models.yaml").get("models") or {}).get(key)
    if isinstance(v, str) and v.startswith("${"):
        return os.environ.get(v[2:-1])
    return v


def provider_available(name: str) -> bool:
    if name == "heuristic":
        return True
    if name in ("claude_haiku", "claude_sonnet", "claude_opus"):
        return llm.available()
    if name == "laya":
        return laya_provider.available()
    if name == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") and _model_id("gemini"))
    return False


def tier_providers(tier: str) -> list[str]:
    cfg = (load_yaml("models.yaml").get("tiers") or {}).get(tier) or {}
    return list(cfg.get("providers") or ["heuristic"])


NEWS_CLAUDE_KEY = news_claude.ON_KEY


def news_to_claude(conn) -> bool:
    """Owner switch (System and News pages): may news items be sent to Claude right now? Default on, the behaviour
    before the switch existed; when active hours are set, only inside that window."""
    return news_claude.allowed(conn)


def first_available(tier: str, claude: bool = True) -> str:
    for p in tier_providers(tier):
        if (claude or not p.startswith("claude")) and provider_available(p):
            return p
    return "heuristic"


def _user_block(item: dict, context: dict | None) -> str:
    """News text is wrapped and labelled as untrusted data."""
    parts = [
        "<news_item>",
        json.dumps(model_item(item), ensure_ascii=False),
        "</news_item>",
    ]
    if context:
        parts += ["<context>", json.dumps(context, default=str), "</context>"]
    return "\n".join(parts)


def _normalize_t1(d: dict, model: str) -> dict:
    def clamp(x):
        return max(0.0, min(1.0, float(x)))

    return {
        "new_info": {"value": bool(d["new_info"]), "confidence": 0.8},
        "catalyst": {"value": d["catalyst"], "confidence": clamp(d["catalyst_confidence"])},
        "direction": {"value": d["direction"], "confidence": clamp(d["direction_confidence"])},
        "materiality": {"value": d["materiality"], "confidence": clamp(d["materiality_confidence"])},
        "fundamental": {"value": bool(d["fundamental"]), "confidence": 0.7},
        "red_flag": {"value": bool(d["red_flag"]), "confidence": 0.7},
        "score": None,
        "rationale": str(d["rationale"])[:400],
        "model": model,
    }


class QuickLayer:
    def __init__(self, conn):
        self.conn = conn

    def decide(self, item: dict, questions=T1_QUESTIONS, provider: str | None = None) -> dict:
        claude = news_to_claude(self.conn)
        if not provider or (provider.startswith("claude") and not claude):
            provider = first_available("t1", claude)
        if provider == "laya":
            return laya_provider.decide(self.conn, item)
        if provider == "claude_haiku":
            model = _model_id("claude_haiku")
            _, parsed, _ = llm.call(self.conn, model=model, task="t1", system=prompt("news_agent@v1"),
                                    messages=[{"role": "user", "content": "Tier: T1 quick read.\n" + _user_block(item, None)}],
                                    max_tokens=2000, effort="low", schema=T1_SCHEMA)
            out = _normalize_t1(parsed, model)
        elif provider == "gemini":
            out = _normalize_t1(_gemini(self.conn, item), _model_id("gemini"))
        else:
            out = heuristic.answer(item["headline"], item.get("summary", ""))
            out["model"] = heuristic.MODEL_ID
        out["provider"] = provider
        return {k: v for k, v in out.items() if k in questions or k in ("score", "rationale", "model", "provider")}

    def deep(self, item: dict, context: dict) -> dict:
        provider = first_available("t2", news_to_claude(self.conn))
        if provider == "claude_sonnet":
            model = _model_id("claude_sonnet")
            _, d, _ = llm.call(self.conn, model=model, task="t2", system=prompt("news_agent@v1"),
                               messages=[{"role": "user", "content": "Tier: T2 deep read.\n" + _user_block(item, context)}],
                               max_tokens=4000, effort="low", schema=T2_SCHEMA)
            conf = max(0.0, min(1.0, float(d["confidence"])))
            return {
                "new_info": {"value": d["new_info"], "confidence": conf},
                "catalyst": {"value": d["catalyst"], "confidence": conf},
                "direction": {"value": d["direction"], "confidence": conf},
                "materiality": {"value": d["materiality"], "confidence": conf},
                "fundamental": {"value": d["fundamental"], "confidence": conf},
                "red_flag": {"value": d["red_flag"], "confidence": conf},
                "score": max(-1.0, min(1.0, float(d["score"]))),
                "rationale": str(d["rationale"])[:600],
                "model": model, "provider": provider,
            }
        out = heuristic.answer(item["headline"], item.get("summary", ""), deep=True)
        out["model"], out["provider"] = heuristic.MODEL_ID + "+deep", "heuristic"
        return out


def _gemini(conn, item: dict) -> dict:
    """Gemini via REST, JSON mode. Not exercised in the prototype's tests (needs a key)."""
    import httpx

    model = _model_id("gemini")
    t0 = time.time()
    body = {
        "systemInstruction": {"parts": [{"text": prompt("news_agent@v1")}]},
        "contents": [{"role": "user", "parts": [{"text": "Tier: T1 quick read. Reply with JSON keys: "
                                                         + ", ".join(T1_SCHEMA["required"]) + "\n" + _user_block(item, None)}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    try:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=15,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        d = json.loads(text)
        missing = [k for k in T1_SCHEMA["required"] if k not in d]
        if missing or d["catalyst"] not in _ENUMS["catalyst"] or d["direction"] not in _ENUMS["direction"] \
                or d["materiality"] not in _ENUMS["materiality"]:
            raise ValueError(f"schema check failed: missing={missing}")
        ok, err = True, None
        return d
    except Exception as e:  # noqa: BLE001 - logged, caller falls back
        ok, err = False, str(e)[:300]
        raise
    finally:
        conn.execute(
            "INSERT INTO model_calls(ts, provider, model, task, latency_ms, ok, error) VALUES (?,?,?,?,?,?,?)",
            (time.time(), "gemini", model, "t1", (time.time() - t0) * 1000, int(ok), err),
        )


def provider_status() -> list[dict]:
    names = ["heuristic", "laya", "gemini", "claude_haiku", "claude_sonnet", "claude_opus"]
    notes = {
        "laya": "local typed decisions, or configured LAYA_URL",
        "gemini": "needs GEMINI_API_KEY and GEMINI_MODEL",
        "heuristic": "offline keyword model, always on",
    }
    return [{"name": n, "available": provider_available(n), "model": ("laya:" + os.environ.get("LAYA_MODEL", "typed-decisions")) if n == "laya" else _model_id(n) if n != "heuristic" else heuristic.MODEL_ID,
             "note": notes.get(n, "needs KIMI_API_KEY")} for n in names]


def recent_headlines(conn, symbol: str, before_ts: int, limit: int = 8) -> list[dict]:
    return db.rows(
        conn,
        "SELECT ts, headline, triage FROM news WHERE symbols LIKE ? AND ts < ? AND ts > ? ORDER BY ts DESC LIMIT ?",
        (f'%"{symbol}"%', before_ts, before_ts - 3 * 86400, limit),
    )
