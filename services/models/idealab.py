"""Idea Lab: code measures, Claude interprets, you curate.

Event studies (forward returns after recorded news, grouped by catalyst x direction) are
computed deterministically here. Ideas come from either the offline rules generator or Claude
(Opus) when a key is set. Every generated hypothesis is counted; each run reserves a
time-based holdout on which an idea is scored exactly once, at creation.
"""
from __future__ import annotations

import json
import math
import statistics
import time
import uuid
from collections import defaultdict

from common import audit, db
from common import marketdata as MD
from common import timeutil as T
from common.config import load_risk, load_yaml

from . import llm
from .quicklayer import _model_id, prompt

HORIZONS = (5, 15, 60)


def _events(conn) -> list[dict]:
    """Recorded (non-dropped) history news with the signals that were written for it."""
    out = []
    for r in db.rows(conn, "SELECT id, ts, symbols, headline FROM news WHERE session='history' AND triage IN ('t1','escalated') ORDER BY ts"):
        sigs = {s["name"]: json.loads(s["value"]) for s in db.rows(
            conn, "SELECT name, value FROM signals WHERE news_id=?", (r["id"],))}
        if "news.catalyst" not in sigs:
            continue
        score = sigs.get("news.score", 0) or 0
        out.append({
            "id": r["id"], "ts": r["ts"], "symbol": json.loads(r["symbols"])[0], "headline": r["headline"],
            "catalyst": sigs["news.catalyst"], "score": score,
            "direction": "positive" if score > 0.05 else "negative" if score < -0.05 else "neutral",
        })
    return out


def _reaction(conn, ev: dict) -> dict | None:
    d = T.et(ev["ts"]).date()
    rows = db.rows(conn, "SELECT ts, o, c FROM bars WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                   (ev["symbol"], T.open_ts(d), T.close_ts(d)))
    if len(rows) < 2:
        return None
    if ev["ts"] < T.open_ts(d):
        k, ref = 0, rows[0]["o"]  # premarket news: react from the open
    else:
        k = next((j for j, b in enumerate(rows) if b["ts"] + 60 >= ev["ts"] + 30), None)
        if k is None:
            return None
        ref = rows[k]["c"]
    out = {"day": d.isoformat(), "when": "premarket" if ev["ts"] < T.open_ts(d) else "intraday"}
    for h in HORIZONS:
        j = k + h - (1 if out["when"] == "premarket" else 0)
        out[f"r{h}"] = rows[j]["c"] / ref - 1 if j < len(rows) else None
    out["rclose"] = rows[-1]["c"] / ref - 1
    return out


def _stats(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    m = statistics.mean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    return {"n": n, "mean": m, "median": statistics.median(vals), "hit": sum(v > 0 for v in vals) / n,
            "t": (m / (sd / math.sqrt(n))) if sd > 0 else None}


def event_study(conn, holdout_days: int | None = None) -> dict:
    cfg = load_yaml("idealab.yaml")
    holdout_days = int(cfg.get("holdout_days", 5)) if holdout_days is None else holdout_days
    days = MD.bar_days(conn)
    hold = set(d.isoformat() for d in days[-holdout_days:]) if holdout_days else set()
    groups: dict[tuple, dict] = defaultdict(lambda: {"disc": [], "hold": []})
    for ev in _events(conn):
        rx = _reaction(conn, ev)
        if rx is None:
            continue
        sign = 1 if ev["direction"] == "positive" else -1 if ev["direction"] == "negative" else 0
        rec = {**ev, **rx, "sign": sign}
        key = (ev["catalyst"], ev["direction"])
        groups[key]["hold" if rx["day"] in hold else "disc"].append(rec)
    table = []
    for (cat, dirn), g in sorted(groups.items()):
        row = {"catalyst": cat, "direction": dirn, "n": len(g["disc"]), "n_holdout": len(g["hold"]),
               "event_ids": [e["id"] for e in g["disc"]]}
        for h in [*map(str, HORIZONS), "close"]:
            key = f"r{h}"
            st = _stats([e[key] for e in g["disc"]])
            # "signed" = return in the direction of the news: >0 continuation, <0 fade
            signed = _stats([e[key] * e["sign"] for e in g["disc"] if e[key] is not None]) if dirn != "neutral" else {"n": 0}
            row[f"h{h}"] = {"mean": st.get("mean"), "t": st.get("t"), "signed_mean": signed.get("mean"),
                            "signed_hit": signed.get("hit"), "signed_t": signed.get("t")}
        table.append(row)
    return {"groups": table, "holdout_days": sorted(hold), "discovery_days": [d.isoformat() for d in days if d.isoformat() not in hold],
            "_raw": groups}


def _holdout_score(groups_raw, catalyst: str, direction: str, horizon: str, kind: str = "continuation") -> dict:
    g = groups_raw.get((catalyst, direction), {"hold": []})["hold"]
    key = f"r{horizon}"
    sign = (1 if direction == "positive" else -1) * (-1 if kind == "fade" else 1)
    st = _stats([e[key] * sign for e in g if e.get(key) is not None])
    return {"n": st.get("n", 0), "signed_mean": st.get("mean"), "signed_hit": st.get("hit"), "scored_at": time.time()}


def _store(conn, run_id: str, idea: dict, raw, min_events: int, model: str) -> int:
    g = idea["group"]
    ho = _holdout_score(raw, g["catalyst"], g["direction"], idea.get("horizon", "60"), idea.get("kind", "continuation"))
    return conn.insert(
        "INSERT INTO ideas(created_at, run_id, title, hypothesis, mechanism, falsify, evidence, event_ids, sample_size,"
        " status, anecdotal, thesis_ids, model, holdout, spec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), run_id, idea["title"], idea["hypothesis"], idea["mechanism"], idea["falsify"],
         json.dumps(idea.get("evidence")), json.dumps(idea["event_ids"]), idea["n"], "proposed",
         int(idea["n"] < min_events), json.dumps(idea.get("theses") or {}), model, json.dumps(ho),
         json.dumps({"group": g, "horizon": idea.get("horizon", "60"), "kind": idea.get("kind")})),
    )


def run(conn, focus: str = "", actor: str = "idealab") -> dict:
    cfg = load_yaml("idealab.yaml")
    key="idealab.quota."+time.strftime("%Y-%m-%d",time.gmtime())
    cap=int(load_yaml("models.yaml").get("budgets",{}).get("idealab_runs_per_day",4))
    with db.tx(conn):
        conn.execute("SELECT pg_advisory_xact_lock(?)",(0x494445414c,))
        used=int(db.kv_get(conn,key) or 0)
        if used>=cap:raise llm.LLMUnavailable("Daily Idea Lab run limit reached")
        db.kv_set(conn,key,used+1)
    min_events = int(cfg.get("min_events", 8))
    study = event_study(conn)
    raw = study.pop("_raw")
    run_id = uuid.uuid4().hex[:10]
    theses = db.rows(conn, "SELECT id, text, tags, confidence, expires FROM theses WHERE active=1")
    if llm.available():
        ideas, model = _claude_ideas(conn, study, theses, focus, min_events), _model_id("claude_opus")
    else:
        ideas, model = _rules_ideas(study, theses), "rules@v1"
    ids = []
    with db.tx(conn):
        for idea in ideas:
            ids.append(_store(conn, run_id, idea, raw, min_events, model))
    audit.log(conn, "idealab.run", actor, {"run_id": run_id, "model": model, "ideas": ids, "focus": focus})
    return {"run_id": run_id, "model": model, "ideas": ids, "groups": len(study["groups"])}


def _rules_ideas(study: dict, theses: list[dict]) -> list[dict]:
    """Offline generator: one idea per group whose 60-min signed reaction is clearly non-zero."""
    out = []
    for g in study["groups"]:
        if g["direction"] == "neutral" or g["n"] < 3:
            continue
        h = g["h60"]
        t = h.get("signed_t")
        if t is None or abs(t) < 2.0:
            continue
        kind = "continuation" if h["signed_mean"] > 0 else "fade"
        cat, dirn = g["catalyst"], g["direction"]
        rel = [th["id"] for th in theses if cat in (th["tags"] or "") or dirn in (th["tags"] or "")]
        move = "keeps moving in the direction of the news" if kind == "continuation" else "reverses part of its initial reaction"
        out.append({
            "title": f"{cat.replace('_', ' ')} / {dirn}: {kind} over 60 min",
            "hypothesis": f"After {dirn} {cat.replace('_', ' ')} news, price {move} over the next 60 minutes "
                          f"(mean {h['signed_mean']:+.2%} in the news direction, hit rate {h['signed_hit']:.0%}, t={t:.1f}, n={g['n']}).",
            "mechanism": ("Fundamental news is absorbed gradually as investors update models; the opening print under-reacts."
                          if kind == "continuation" else
                          "Opinion-only news triggers a sentiment move without a change in cash flows, which then mean-reverts."),
            "falsify": f"On out-of-sample days, the 60-min signed mean is <= 0 or the hit rate falls below 50% with n >= {max(5, g['n'] // 2)}.",
            "group": {"catalyst": cat, "direction": dirn}, "horizon": "60", "kind": kind,
            "n": g["n"], "event_ids": g["event_ids"], "evidence": {"h5": g["h5"], "h15": g["h15"], "h60": h, "hclose": g["hclose"]},
            "theses": {"related": rel},
        })
    return out


IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "catalyst": {"type": "string"},
                    "direction": {"type": "string", "enum": ["positive", "negative"]},
                    "horizon": {"type": "string", "enum": ["5", "15", "60", "close"]},
                    "kind": {"type": "string", "enum": ["continuation", "fade"]},
                    "hypothesis": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "falsify": {"type": "string"},
                    "supports_theses": {"type": "array", "items": {"type": "integer"}},
                    "contradicts_theses": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "catalyst", "direction", "horizon", "kind", "hypothesis", "mechanism",
                             "falsify", "supports_theses", "contradicts_theses"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ideas"],
    "additionalProperties": False,
}


def _claude_ideas(conn, study: dict, theses: list[dict], focus: str, min_events: int) -> list[dict]:
    table = [{k: v for k, v in g.items() if k != "event_ids"} for g in study["groups"]]
    msg = (
        f"Event-study table (discovery days only; {len(study['discovery_days'])} sessions, holdout reserved):\n"
        f"{json.dumps(table, default=lambda x: round(x, 5) if isinstance(x, float) else x)}\n\n"
        f"Operator theses:\n{json.dumps(theses)}\n\n"
        + (f"Focus requested by the operator: {focus}\n" if focus else "")
        + "Propose at most 5 hypotheses. Reference groups by catalyst and direction exactly as in the table."
    )
    _, parsed, _ = llm.call(conn, model=_model_id("claude_opus"), task="idealab",
                            system=prompt("idealab@v1").replace("{min_events}", str(min_events)),
                            messages=[{"role": "user", "content": msg}], max_tokens=16000, effort="high",
                            schema=IDEAS_SCHEMA)
    by_key = {(g["catalyst"], g["direction"]): g for g in study["groups"]}
    out = []
    for it in parsed["ideas"]:
        g = by_key.get((it["catalyst"], it["direction"]))
        if not g:
            continue  # cites a group that doesn't exist: dropped, still counted in the audit log
        out.append({
            "title": it["title"], "hypothesis": it["hypothesis"], "mechanism": it["mechanism"], "falsify": it["falsify"],
            "group": {"catalyst": it["catalyst"], "direction": it["direction"]}, "horizon": it["horizon"],
            "kind": it["kind"], "n": g["n"], "event_ids": g["event_ids"],
            "evidence": {"h5": g["h5"], "h15": g["h15"], "h60": g["h60"], "hclose": g["hclose"]},
            "theses": {"supports": it["supports_theses"], "contradicts": it["contradicts_theses"]},
        })
    return out


def template_strategy(idea: dict) -> tuple[str | None, str]:
    """Deterministic compile of a group idea into a draft DSL file. Returns (yaml, why)."""
    spec = json.loads(idea["spec"])
    g, kind = spec["group"], spec["kind"]
    risk = load_risk()
    long_ok = (g["direction"] == "positive" and kind == "continuation") or (g["direction"] == "negative" and kind == "fade")
    if not long_ok and not risk.allow_short:
        return None, "this idea needs a short position and risk.yaml has allow_short: false"
    sid = f"idea{idea['id']}_{g['catalyst']}_{kind}"
    if g["direction"] == "positive":
        score_param = "  score_min:  {value: 0.5, range: [0.2, 0.9]}"
        score_cond = "news.score >= score_min"
    else:
        score_param = "  score_max:  {value: -0.5, range: [-0.9, -0.2]}"
        score_cond = "news.score <= score_max"
    trigger = "crosses_above(close, or_high(or_minutes))" if kind == "continuation" else "crosses_above(close, vwap)"
    text = f"""dsl: 0.1
id: {sid}
version: 1
source:
  text: {json.dumps(idea['hypothesis'])}
  compiled_by: template@v1
  prompt: none
assumptions:
  - "idea #{idea['id']} ({g['catalyst']} / {g['direction']} / {kind}) traded long only"
  - entry trigger = {'opening-range breakout' if kind == 'continuation' else 'reclaim of VWAP'} after the news
  - stop = 1.5 x 14-bar ATR below entry; target = 2R
  - entry window 09:45-11:30 ET (most event drift happens in the first two hours)
suggestions:
  - tune or_minutes and atr_mult in a backtest before promoting
universe:
  from: {{news_today: true}}
  filters:
    - adv(20) >= min_adv
timeframe: 1min
params:
  min_adv:    {{value: 20_000_000, range: [10_000_000, 500_000_000]}}
{score_param}
  or_minutes: {{value: 15, range: [5, 30]}}
  atr_mult:   {{value: 1.5, range: [0.5, 4.0]}}
  target_r:   {{value: 2.0, range: [1.0, 4.0]}}
signals:
  news.score:    {{from: news_agent@v1, max_age: since_prior_close}}
  news.catalyst: {{from: news_agent@v1, max_age: since_prior_close}}
  news.red_flag: {{from: news_agent@v1, max_age: since_prior_close, default: false}}
entry:
  side: long
  window: "09:45-11:30"
  when_all:
    - news.catalyst in [{g['catalyst']}]
    - {score_cond}
    - {trigger}
    - not news.red_flag
sizing:
  risk_per_trade: 0.25%
exit:
  stop: entry_price - atr_mult * atr(14)
  target: entry_price + target_r * risk_per_share
  flatten_at: "15:50"
  when_any:
    - news.red_flag
execution:
  mode: manual
limits:
  max_trades_per_day: 3
"""
    if "or_minutes" not in trigger:
        text = text.replace("  or_minutes: {value: 15, range: [5, 30]}\n", "")
    return text, "ok"


def holdout_passed(idea: dict) -> bool | None:
    ho = json.loads(idea.get("holdout") or "{}")
    if not ho.get("n") or ho.get("signed_mean") is None:
        return None
    kind = json.loads(idea.get("spec") or "{}").get("kind")
    return ho["signed_mean"] > 0 if kind == "continuation" else ho["signed_mean"] < 0


def counter(conn) -> dict:
    """'tested N hypotheses, M survived': survived = not anecdotal and passed its one holdout score."""
    ideas = db.rows(conn, "SELECT * FROM ideas")
    survived = sum(1 for i in ideas if not i["anecdotal"] and holdout_passed(i))
    return {"tested": len(ideas), "survived": survived}
