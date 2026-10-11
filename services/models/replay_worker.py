"""Model replay for backtests (data_requests rows with worker='models').

A backtest can ask for its window's news to be triaged by the same pipeline live trading uses (T0 code,
T1 quick layer, T2 deep read), either offline (heuristic@v1, free) or with the live models (Laya, Claude,
paid). For DSL 0.2 portfolio files it can also assess monitored assumptions once per session. Results are
cached (news_assessments, assumption_replays) and never written to live signals; a story assessed once is
not paid for again. Paid calls run under their own daily quota and stop at the run's budget, after which
the remaining stories use the offline model and are reported as such.

Look-ahead caveat: the models were trained on data after many of these dates. The prompts give only
point-in-time evidence, but a model may still "know" what happened next; the report says so.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date

from common import db, registry
from common.config import load_risk

from . import heuristic, laya_provider, llm, triage

log = logging.getLogger("models.replay")
WORKER_KEY = "replay.worker"
LATENCY_S = 30  # signals become available 30s after publication, as in the offline news backfill


def providers() -> dict:
    return {"laya": laya_provider.available(), "claude": llm.available()}


class ReplayWorker(threading.Thread):
    def __init__(self, dsn: str | None = None):
        super().__init__(daemon=True, name="replay")
        self.dsn = dsn
        self.stop = threading.Event()

    def run(self):
        c = db.connect(self.dsn)
        while not self.stop.is_set():
            try:
                db.kv_set(c, WORKER_KEY, {"ts": time.time(), **providers()})
                row = c.execute("UPDATE data_requests SET status='running', started_at=? WHERE id=(SELECT id FROM data_requests"
                                " WHERE status='pending' AND worker='models' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
                                (time.time(),)).fetchone()
                if row:
                    fulfil(c, dict(row))
                    continue
            except Exception:  # noqa: BLE001 - keep serving later jobs
                log.exception("replay worker error")
            self.stop.wait(1.0)


def fulfil(c, r: dict):
    spec = json.loads(r["spec"])
    paid = spec["mode"] == "models"
    result = {"news": {}, "assumptions": {}, "cost_usd": 0.0, "budget_usd": spec.get("budget_usd", 0), "fallback": 0}
    spent = [0.0]

    def note(frac, text):
        c.execute("UPDATE data_requests SET progress=?, note=? WHERE id=?", (frac, text, r["id"]))

    token = llm.REPLAY.set(True)
    try:
        if spec.get("news"):
            result["news"] = news(c, spec, paid, spent, result, note)
        if spec.get("assumptions") and paid:
            result["assumptions"] = assumptions(c, spec, spent, result, note)
        result["cost_usd"] = round(spent[0], 4)
        c.execute("UPDATE data_requests SET status='done', progress=1, note='done', result=?, finished_at=? WHERE id=?",
                  (json.dumps(result), time.time(), r["id"]))
    except Exception as e:  # noqa: BLE001
        c.execute("UPDATE data_requests SET status='failed', error=?, finished_at=? WHERE id=?",
                  (f"{type(e).__name__}: {e}"[:500], time.time(), r["id"]))
        log.exception("replay job %s failed", r["id"])
    finally:
        llm.REPLAY.reset(token)


def _cost_since(c, mark: int) -> float:
    return float(db.row(c, "SELECT COALESCE(SUM(cost_usd),0) v FROM model_calls WHERE id>? AND task LIKE 'replay.%'", (mark,))["v"])


def _mark(c) -> int:
    return int(db.row(c, "SELECT COALESCE(MAX(id),0) m FROM model_calls")["m"])


def news(c, spec, paid, spent, result, note) -> dict:
    n = spec["news"]
    mode = spec["mode"]
    syms = n["symbols"]
    rows = [r for r in db.rows(c, "SELECT * FROM news WHERE ts>=? AND ts<=? ORDER BY ts, id", (int(n["start_ts"]), int(n["end_ts"])))
            if set(json.loads(r["symbols"] or "[]")) & set(syms)]
    counts = {"stories": len(rows), "cached": 0, "assessed": 0, "kept": 0, "dropped": 0}
    for k, r in enumerate(rows):
        cached = db.row(c, "SELECT status, model FROM news_assessments WHERE news_id=? AND mode=?", (r["id"], mode))
        if cached and not (paid and (cached["model"] or "").startswith("heuristic")):
            counts["cached"] += 1
            counts["kept" if cached["status"] != "dropped" else "dropped"] += 1
            continue
        allow = paid and spent[0] < spec.get("budget_usd", 0)
        if paid and not allow:
            result["fallback"] += 1
        item = dict(r, symbols=json.loads(r["symbols"] or "[]"))
        mark = _mark(c)
        try:
            a = triage.assess(c, item, allow_paid=allow, symbols=syms, replay=True)
        except Exception as e:  # noqa: BLE001 - a provider failure falls back to the offline model for this story
            a = triage.assess(c, item, allow_paid=False, symbols=syms, replay=True)
            a["reason"] = f"{a.get('reason', '')} (fallback: {type(e).__name__})"
        cost = _cost_since(c, mark)
        spent[0] += cost
        final = a.get("final") or {}
        sigs = {k2: list(v) for k2, v in triage._signals_from(final).items()} if a["status"] != "dropped" else {}
        model = (final.get("model") or (heuristic.MODEL_ID if not allow else "")) or "t0"
        c.execute("INSERT INTO news_assessments(news_id, mode, status, reason, symbols, tier, answers, model, cost_usd, ts)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(news_id, mode) DO UPDATE SET status=excluded.status, reason=excluded.reason,"
                  " symbols=excluded.symbols, tier=excluded.tier, answers=excluded.answers, model=excluded.model,"
                  " cost_usd=excluded.cost_usd, ts=excluded.ts",
                  (r["id"], mode, a["status"], a.get("reason"), json.dumps(a.get("symbols") or []), a.get("tier"),
                   json.dumps(sigs), model, cost, time.time()))
        counts["assessed"] += 1
        counts["kept" if a["status"] != "dropped" else "dropped"] += 1
        if k % 5 == 0:
            note(0.8 * (k + 1) / max(len(rows), 1), f"news {k + 1}/{len(rows)} · ${spent[0]:.2f} spent")
    return counts


def assumptions(c, spec, spent, result, note) -> dict:
    """Assess monitored assumptions once per session, with evidence as of that session's decision time."""
    from dsl.validator import validate

    from . import assumption_monitor as AM

    a_spec = spec["assumptions"]
    text = registry.text_of(c, a_spec["strategy_hash"])
    s = validate(text, load_risk()).strategy if text else None
    if s is None or getattr(s, "v2", None) is None:
        return {"error": "strategy not available"}
    out = {"sessions": len(a_spec["days"]), "assessed": 0, "cached": 0}
    for k, day in enumerate(a_spec["days"]):
        for a in s.v2.monitored():
            if db.row(c, "SELECT 1 FROM assumption_replays WHERE strategy_hash=? AND assumption_id=? AND day=? AND mode=?",
                      (s.hash, a.id, day, spec["mode"])):
                out["cached"] += 1
                continue
            if spent[0] >= spec.get("budget_usd", 0):
                result["fallback"] += 1
                continue  # out of budget: this session has no assessment (reported as missing)
            ev = _evidence(c, s, a, date.fromisoformat(day))
            status, tier, answers, err, mark = "uncertain", None, {}, None, _mark(c)
            for t in [x for x in ("t1", "t2") if x in a.tiers]:
                if t == "t2" and tier and status != "uncertain" and "request_t2" not in a.responses.get(f"on_{status}", []):
                    break
                cfg = s.v2.monitoring.tiers[t]
                if cfg["provider"] == "laya" and not laya_provider.available():
                    cfg = {**cfg, "provider": "claude_haiku"}  # replay fallback: Laya isn't running here
                    s.v2.monitoring.tiers[t] = cfg
                try:
                    answers, _ = AM.ask_tier(c, s, a, t, ev)
                    tier, status = t, AM.assess(a, answers, cfg["confidence_floor"])
                except Exception as e:  # noqa: BLE001
                    err = f"{t}: {type(e).__name__}"
            cost = _cost_since(c, mark)
            spent[0] += cost
            c.execute("INSERT INTO assumption_replays(strategy_hash, assumption_id, day, mode, status, tier, answers, cost_usd, error, ts)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                      (s.hash, a.id, day, spec["mode"], status if tier else "unavailable", tier, json.dumps(answers, default=str),
                       cost, err, time.time()))
            out["assessed"] += 1
        if k % 5 == 0:
            note(0.8 + 0.2 * (k + 1) / max(len(a_spec["days"]), 1), f"assumptions {k + 1}/{len(a_spec['days'])} sessions · ${spent[0]:.2f} spent")
    return out


def _evidence(c, s, a, day: date) -> dict:
    """Point-in-time evidence for one session: that day's market inputs at the close and the prior 24h of headlines."""
    from common import timeutil as T

    cut = T.ts_at(day, s.decide_at)
    vals = {}
    for name, inp in s.v2.inputs.items():
        if inp.kind == "index":
            rows = db.rows(c, "SELECT day, c FROM index_daily WHERE instrument=? AND day<=? ORDER BY day DESC LIMIT 2", (inp.instrument, str(day)))
            vals[name] = rows[0]["c"] / rows[1]["c"] - 1 if len(rows) == 2 and rows[0]["day"] == str(day) else None
        else:
            rets = []
            for sym in inp.symbols:
                rows = db.rows(c, "SELECT day, c FROM daily WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 2", (sym, str(day)))
                if len(rows) == 2 and rows[0]["day"] == str(day):
                    rets.append(rows[0]["c"] / rows[1]["c"] - 1)
            vals[name] = sum(rets) / len(rets) if len(rets) == len(inp.symbols) else None
    syms = set(s.v2.monitoring.required_symbols)
    news = [r for r in db.rows(c, "SELECT id, ts, symbols, headline FROM news WHERE ts>? AND ts<=? ORDER BY ts DESC LIMIT 60",
                               (cut - 86400, cut)) if set(json.loads(r["symbols"] or "[]")) & syms][:s.v2.monitoring.evidence["news_content"].get("max_articles", 8)]
    return {"as_of": cut, "strategy": s.id, "items": [{"id": "inputs", "kind": "market_inputs", "values": vals}]
            + [{"id": f"news:{n['id']}", "kind": "news", "ts": n["ts"], "text": n["headline"]} for n in news],
            "assumption": {"id": a.id, "statement": a.statement,
                           "questions": [{"id": q.id, "prompt": q.prompt, "answers": q.choices} for q in a.questions]}}
