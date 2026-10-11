"""Model replay for backtests: what it can change, what it costs, and the replayed inputs the engine reads.

    recorded  signals recorded live (default; free, what actually happened)
    offline   the window's news triaged again by the offline keyword model (heuristic@v1; free, deterministic)
    models    the same triage with the live tiers (Laya, then Claude Haiku/Sonnet on escalation; paid, not deterministic)

Model answers only matter where a strategy file reads them: news-gated intraday rules, and in DSL 0.2 portfolio
files the monitored assumptions (context, plus block_new_entries for critical ones). Price-only rules are
unaffected, and the estimate says so instead of spending money.
"""
from __future__ import annotations

import json
import time

from . import db
from . import timeutil as T

MODES = ("recorded", "offline", "models")
HIGH_COST_USD = 5.0  # above this the owner must confirm the estimate
# Rough per-call costs from typical prompt sizes and the PRICES table in services/models/llm.py.
COST = {"haiku_t1": 0.0004, "sonnet_t2": 0.017, "assumption_t1": 0.0005, "assumption_t2": 0.010}
T2_RATE = 0.35  # share of kept stories that escalate (low confidence, high materiality)
NEWS_PER_SYMBOL_DAY = 4.0  # estimate for windows whose news isn't downloaded yet
WORKER_KEY = "replay.worker"


def influence(strat) -> dict:
    """Whether model answers can change this strategy's trades, and what replay would add."""
    kind = getattr(strat, "kind", "intraday")
    if kind == "intraday":
        if getattr(strat, "uses_signals", False):
            return {"news": True, "assumptions": False, "trades": True,
                    "note": f"Entries/exits read news signals ({', '.join(sorted(strat.signals))}); replayed answers change which trades happen."}
        return {"news": False, "assumptions": False, "trades": False,
                "note": "This strategy's rules use no news signals (price-only), so model replay can't change its trades."}
    v2 = getattr(strat, "v2", None)
    monitored = v2.monitored() if v2 else []
    if not monitored:
        return {"news": False, "assumptions": False, "trades": False,
                "note": "This portfolio file is price-only (no news signals or monitored assumptions); model replay can't change it."}
    blocking = [a.id for a in monitored if a.critical and any("block_new_entries" in acts for acts in a.responses.values())]
    if blocking:
        return {"news": False, "assumptions": True, "trades": True,
                "note": f"The trading rules are price-only, but critical assumption(s) {', '.join(blocking)} can block buys when "
                        "contradicted, so replayed assessments can change trades."}
    return {"news": False, "assumptions": True, "trades": False,
            "note": f"The YAML's trading rules and overlays are price-only. Replay only assesses the monitored assumption(s) "
                    f"{', '.join(a.id for a in monitored)} each session as context (log/notify): the report gains their history, "
                    "but trades and returns stay the same as without it."}


def providers(c) -> dict:
    w = db.kv_get(c, WORKER_KEY) or {}
    return {"available": time.time() - float(w.get("ts") or 0) < 60, "laya": bool(w.get("laya")), "claude": bool(w.get("claude"))}


def estimate(c, strat, config, symbols: list[str], start_ts: float, end_ts: float, sessions: int) -> dict:
    """Stories, calls and estimated Claude cost of a replay; cached answers cost nothing."""
    inf = influence(strat)
    prov = providers(c)
    out = {"mode": config.model_replay, "influence": inf, "providers": prov, "stories": 0, "cached": 0, "claude_usd": 0.0,
           "assumption_sessions": 0, "high_cost": False}
    if config.model_replay == "recorded":
        return out
    if inf["news"]:
        stored = [r for r in db.rows(c, "SELECT id, symbols FROM news WHERE ts>=? AND ts<=?", (int(start_ts), int(end_ts)))
                  if set(json.loads(r["symbols"] or "[]")) & set(symbols)]
        covered = all((db.kv_get(c, f"news.fetched.{s}") or {}).get("start", 1e18) <= start_ts and
                      (db.kv_get(c, f"news.fetched.{s}") or {}).get("end", 0) >= end_ts - 86400 for s in symbols)
        n = len(stored) if covered else max(len(stored), int(NEWS_PER_SYMBOL_DAY * len(symbols) * (end_ts - start_ts) / 86400))
        cached = db.row(c, "SELECT COUNT(*) n FROM news_assessments WHERE mode=? AND news_id = ANY(?) AND model NOT LIKE 'heuristic%'",
                        (config.model_replay, [r["id"] for r in stored]))["n"] if stored else 0
        out.update(stories=n, cached=cached, news_estimated=not covered)
        if config.model_replay == "models":
            todo = max(n - cached, 0)
            out["claude_usd"] += todo * ((0 if prov["laya"] else COST["haiku_t1"]) + T2_RATE * COST["sonnet_t2"])
    if inf["assumptions"] and config.model_replay == "models":
        k = len(strat.v2.monitored())
        out["assumption_sessions"] = sessions
        out["claude_usd"] += sessions * k * ((0 if prov["laya"] else COST["assumption_t1"]) + 0.3 * COST["assumption_t2"])
    out["claude_usd"] = round(out["claude_usd"], 2)
    out["high_cost"] = out["claude_usd"] >= HIGH_COST_USD
    out["over_budget"] = out["claude_usd"] > config.model_budget_usd
    return out


def signal_source(mode: str, symbols: list[str], latency_s: int = 30):
    """A signal_book(conn, start_ts, end_ts) built from replayed triage answers instead of recorded signals."""
    from dsl.evaluator import SignalBook

    wanted = set(symbols)

    def book(conn, start_ts, end_ts):
        rows = []
        for r in db.rows(conn, "SELECT n.ts, a.symbols, a.answers, a.tier, a.model FROM news_assessments a JOIN news n ON n.id=a.news_id"
                               " WHERE a.mode=? AND a.status!='dropped' AND n.ts+?>=? AND n.ts+?<=?",
                         (mode, latency_s, start_ts, latency_s, end_ts)):
            sigs = json.loads(r["answers"] or "{}")
            for sym in set(json.loads(r["symbols"] or "[]")) & wanted:
                for name, (value, conf) in sigs.items():
                    rows.append({"ts": r["ts"] + latency_s, "symbol": sym, "name": name, "value": value})
        return SignalBook(rows)
    return book


def assumption_blocks(c, strat, mode: str, days: list[str]) -> tuple[dict[str, list[str]], list[dict]]:
    """Buy blocks per session from critical assumptions, and the assessment timeline for the report."""
    blocks: dict[str, list[str]] = {}
    timeline = []
    by_id = {a.id: a for a in strat.v2.monitored()}
    rows = db.rows(c, "SELECT day, assumption_id, status, tier, error FROM assumption_replays WHERE strategy_hash=? AND mode=?"
                      " AND day = ANY(?) ORDER BY day", (strat.hash, mode, days))
    for r in rows:
        a = by_id.get(r["assumption_id"])
        if a is None:
            continue
        timeline.append(r)
        acts = a.responses.get(f"on_{r['status']}", [])
        if a.critical and "block_new_entries" in acts:
            blocks.setdefault(r["day"], []).append(f"assumption {a.id} {r['status']}")
    return blocks, timeline

