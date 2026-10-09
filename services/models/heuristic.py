"""Offline keyword model ("heuristic@v1").

Answers the same typed questions as the T1/T2 model providers, so the whole pipeline runs
without API keys. It is deliberately simple: mixed headlines come back with low confidence,
which exercises the escalation path.
"""
from __future__ import annotations

import re

from dsl.builtins import CATALYSTS

MODEL_ID = "heuristic@v1"

_CATALYST_RULES = [
    ("m_and_a", r"\b(acquired|acquire|acquisition|buyout|merger|takeover)\b"),
    ("analyst_upgrade", r"\b(upgrades?|upgraded|outperform)\b"),
    ("analyst_downgrade", r"\b(downgrades?|downgraded|underperform)\b"),
    ("earnings", r"\b(eps|earnings|quarterly results|q[1-4])\b"),
    ("guidance", r"\b(guidance|outlook|forecast)\b"),
    ("legal", r"\b(sec|investigation|lawsuit|probe|regulator|class-action)\b"),
    ("contract", r"\b(contract|supply deal|awarded)\b"),
    ("product", r"\b(launch(es)?|unveils?|approval|recall|product)\b"),
    ("macro", r"\b(fed|cpi|payrolls|jobs report|treasury|yields)\b"),
]
_POS = r"\b(beats?|tops?|record|raises?|lifts?|upgraded?|upgrades|outperform|wins?|awarded|signs|approval|launch(es)?|strong|premium|acquired|dismissal|expands?|buyback|dividend|above consensus)\b"
_NEG = r"\b(miss(es)?|falls?|fall short|declines?|cuts?|lowers?|downgraded?|downgrades|underperform|loses|delays?|recall|lawsuit|investigation|probe|collapse|terminated|step down|offering|weaker|late|below consensus)\b"
# T2 phrase rules that resolve common ambiguities (heuristic "deep read").
_PHRASES = [
    (r"\bbut cuts\b", -2), (r"\bwins dismissal\b", 2), (r"\bto be acquired\b", 2),
    (r"\bagrees to buyout\b", 2), (r"\braises buyback\b", 1), (r"\brevenue misses\b", -1),
]
_RED_FLAG = r"\b(sec|investigation|probe|offering|late quarterly|recall|class-action|step down|terminated)\b"
_ROUNDUP = r"\b(movers|stocks to watch|recap|roundup|round-up)\b"


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.I))


def answer(headline: str, summary: str = "", deep: bool = False) -> dict:
    text = headline
    low = text.lower()
    catalyst, cat_conf = "other", 0.45
    for name, pat in _CATALYST_RULES:
        if re.search(pat, low):
            catalyst, cat_conf = name, 0.85
            break
    pos, neg = _count(_POS, low), _count(_NEG, low)
    phrase = 0
    if deep:
        for pat, w in _PHRASES:
            if re.search(pat, low):
                phrase += w
    net = pos - neg + phrase
    total = pos + neg + abs(phrase)
    if total == 0:
        direction, dir_conf = "neutral", 0.4
    else:
        direction = "positive" if net > 0 else "negative" if net < 0 else "neutral"
        dir_conf = 0.5 + 0.45 * abs(net) / total
        if deep and phrase:
            dir_conf = max(dir_conf, 0.75)
    if catalyst in ("earnings", "guidance", "m_and_a"):
        materiality = "high"
    elif catalyst in ("legal", "product", "contract", "analyst_upgrade", "analyst_downgrade"):
        materiality = "medium"
    else:
        materiality = "low"
    if "record" in low and materiality == "medium":
        materiality = "high"
    mat_conf = 0.7
    new_info = not re.search(_ROUNDUP, low)
    red_flag = bool(re.search(_RED_FLAG, low)) and direction != "positive"
    fundamental = catalyst in ("earnings", "guidance", "m_and_a", "contract", "product", "legal") and materiality != "low"
    sign = {"positive": 1, "negative": -1, "neutral": 0}[direction]
    weight = {"low": 0.3, "medium": 0.65, "high": 0.9}[materiality]
    score = round(sign * weight * min(1.0, dir_conf + 0.1), 3)
    assert catalyst in CATALYSTS
    rationale = (
        f"keywords: catalyst={catalyst}, +{pos}/-{neg}"
        + (f", phrase {phrase:+d}" if phrase else "")
        + f" -> {direction} ({materiality})"
    )
    return {
        "new_info": {"value": new_info, "confidence": 0.7},
        "catalyst": {"value": catalyst, "confidence": cat_conf},
        "direction": {"value": direction, "confidence": round(dir_conf, 3)},
        "materiality": {"value": materiality, "confidence": mat_conf},
        "fundamental": {"value": fundamental, "confidence": 0.6},
        "red_flag": {"value": red_flag, "confidence": 0.6},
        "score": score,
        "rationale": rationale,
    }


def is_roundup(headline: str) -> bool:
    return bool(re.search(_ROUNDUP, headline.lower()))


def parse_stance(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(avoid|stay away|don't touch|do not trade|no trade)\b", low):
        return "avoid"
    b = _count(r"\b(bull|bullish|long|buy|upside|strong)\b", low)
    s = _count(r"\b(bear|bearish|short|sell|downside|weak|fade)\b", low)
    return "bullish" if b > s else "bearish" if s > b else "neutral"
