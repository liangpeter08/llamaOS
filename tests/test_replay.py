"""Model replay for backtests: influence, cost estimate and confirmation, offline/paid triage, assumption blocks."""
import json
import time
from datetime import date

import pytest

from common import db, history, registry, replay as RP
from common import timeutil as T
from backtest import workspace as W
from tests.test_portfolio_v2 import ENFORCE, V2, strat
from tests.test_portfolio import ROOT


def load(risk, name):
    from dsl.validator import validate
    v = validate((ROOT / "strategies" / name).read_text(), risk)
    assert v.ok, v.errors
    return v.strategy


CRITICAL = ENFORCE.replace("    critical: false", "    critical: true").replace(
    "on_contradicted: [log, notify_dashboard, request_t2]", "on_contradicted: [log, notify_dashboard, block_new_entries]")


def test_influence_says_when_replay_cannot_change_trades(risk):
    assert RP.influence(load(risk, "orb_news_long.v1.yaml"))["trades"]
    price_only = RP.influence(load(risk, "big_tech_dip_buy_intraday.v1.yaml"))
    assert not price_only["trades"] and "price-only" in price_only["note"]
    assert not RP.influence(load(risk, "big_tech_software_tilt_hold.v1.yaml"))["assumptions"]
    v2 = RP.influence(strat(risk))
    assert v2["assumptions"] and not v2["trades"] and "trades and returns stay the same" in v2["note"]
    assert RP.influence(strat(risk, CRITICAL))["trades"]  # a critical assumption can block buys


def seed_news(conn, n, start_ts, syms=("ACME",), tag="t", every=3600):
    for k in range(n):
        conn.execute("INSERT INTO news(ext_id, ts, session, symbols, headline, summary, source, triage) VALUES (?,?,?,?,?,?,?,?)",
                     (f"{tag}{k}", int(start_ts + every * k), "test", json.dumps(list(syms)),
                      f"ACME beats earnings estimates and raises full-year guidance, story {k}", "Revenue rose sharply.", "test", "replay_only"))


def test_estimate_and_high_cost_confirmation(conn, risk, orb_text):
    registry.sync(conn, risk)
    s = load(risk, "orb_news_long.v1.yaml")
    start, end = date(2025, 3, 3), date(2025, 3, 14)
    seed_news(conn, 40, T.open_ts(start))
    for sym in ("ACME",):
        db.kv_set(conn, f"news.fetched.{sym}", {"start": T.open_ts(start) - 86400 * 2, "end": T.close_ts(end)})
    db.kv_set(conn, RP.WORKER_KEY, {"ts": time.time(), "laya": False, "claude": True})
    cfg = W.RunConfig(strategy_hash=s.hash, symbols=["ACME"], start_date=start, end_date=end, model_replay="models", model_budget_usd=50)
    est = RP.estimate(conn, s, cfg, ["ACME"], T.open_ts(start) - 86400, T.close_ts(end), 10)
    assert est["stories"] == 40 and not est["news_estimated"] and 0.2 < est["claude_usd"] < 0.4 and not est["high_cost"]
    # many stories: the estimate crosses the confirmation threshold
    seed_news(conn, 900, T.open_ts(start) + 30, tag="more", every=60)
    est = RP.estimate(conn, s, cfg, ["ACME"], T.open_ts(start) - 86400, T.close_ts(end), 10)
    assert est["high_cost"] and est["claude_usd"] >= RP.HIGH_COST_USD
    db.kv_set(conn, history.WORKER_KEY, {"ts": time.time()})
    with pytest.raises(ValueError, match="confirm the estimate"):
        W.create_run(conn, cfg, "pytest", submit=False)
    assert W.create_run(conn, cfg.model_copy(update={"confirm_model_cost": True}), "pytest", submit=False)
    # offline replay is free and never needs confirmation
    off = RP.estimate(conn, s, cfg.model_copy(update={"model_replay": "offline"}), ["ACME"], T.open_ts(start) - 86400, T.close_ts(end), 10)
    assert off["claude_usd"] == 0 and not off["high_cost"]


def job(conn, spec):
    from services.models import replay_worker as RW
    rid = history.request(conn, spec, "pytest", worker="models")
    RW.fulfil(conn, dict(db.row(conn, "SELECT * FROM data_requests WHERE id=?", (rid,))))
    r = db.row(conn, "SELECT status, result, error FROM data_requests WHERE id=?", (rid,))
    assert r["status"] == "done", r["error"]
    return json.loads(r["result"])


def test_offline_replay_writes_cached_answers_not_live_signals(conn, risk):
    from services.models import triage
    start = T.open_ts(date(2025, 3, 3))
    seed_news(conn, 5, start)
    spec = {"mode": "offline", "budget_usd": 0, "news": {"symbols": ["ACME"], "start_ts": start - 86400, "end_ts": start + 86400 * 2}}
    res = job(conn, spec)
    assert res["news"]["stories"] == 5 and res["news"]["assessed"] == 5 and res["cost_usd"] == 0
    assert not db.rows(conn, "SELECT id FROM signals")  # live signals untouched
    assert triage.run_pending(conn, int(time.time())) == 0  # replay_only stories never reach live triage
    book = RP.signal_source("offline", ["ACME"])(conn, start - 86400, start + 86400 * 2)
    assert book.rows if hasattr(book, "rows") else book
    assert job(conn, spec)["news"]["cached"] == 5  # second run: nothing re-assessed


def test_paid_replay_stops_at_budget(conn, risk, monkeypatch):
    from services.models import laya_provider, llm, quicklayer
    start = T.open_ts(date(2025, 3, 3))
    seed_news(conn, 6, start)
    monkeypatch.setattr(laya_provider, "available", lambda: False)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(quicklayer, "first_available", lambda tier: "claude_haiku" if tier == "t1" else "claude_sonnet")
    seen = []

    def fake_call(conn_, *, model, task, **kw):
        assert llm.REPLAY.get()  # replay calls run under their own quota
        seen.append(task)
        conn_.execute("INSERT INTO model_calls(ts, provider, model, task, latency_ms, ok, cost_usd) VALUES (?,?,?,?,?,?,?)",
                      (time.time(), "anthropic", model, "replay." + task, 1, 1, 0.02))
        conf = {"catalyst_confidence": 0.9, "direction_confidence": 0.9, "materiality_confidence": 0.9}
        if task == "t1":
            return "", {"new_info": True, "catalyst": "earnings", "direction": "positive", "materiality": "medium", "fundamental": True,
                        "red_flag": False, "rationale": "r", **conf}, None
        return "", {"new_info": True, "catalyst": "earnings", "direction": "positive", "materiality": "medium", "fundamental": True,
                    "red_flag": False, "score": 0.7, "confidence": 0.9, "rationale": "r"}, None
    monkeypatch.setattr(llm, "call", fake_call)
    res = job(conn, {"mode": "models", "budget_usd": 0.05,
                     "news": {"symbols": ["ACME"], "start_ts": start - 86400, "end_ts": start + 86400 * 2}})
    assert res["cost_usd"] >= 0.05 and res["fallback"] > 0  # paid calls stopped at the budget
    models = [r["model"] for r in db.rows(conn, "SELECT model FROM news_assessments WHERE mode='models' ORDER BY news_id")]
    assert models[0].startswith("claude") and models[-1].startswith("heuristic")


def test_assumption_replay_blocks_buys_for_critical_assumptions(conn, risk, monkeypatch):
    from backtest import engine as bt
    from services.models import assumption_monitor as AM, laya_provider
    from tests.test_history import FakeData, FakeIndex
    from services.broker.history_worker import HistoryWorker
    s = strat(risk, CRITICAL)
    conn.execute("INSERT INTO strategies(hash, sid, version, path, status, mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                 (s.hash, s.id, 3, "strategies/x.yaml", "draft", "manual", 0, 0))
    monkeypatch.setattr(registry, "text_of", lambda c, h: CRITICAL)
    cfg = W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 4, 30), model_replay="models", model_budget_usd=10)
    spec = history.plan(conn, "portfolio", [*s.symbols, "AMD"], ["NASDAQ_COMPOSITE"], cfg)
    HistoryWorker(FakeData(), db.url(), index_feed=FakeIndex()).fulfil(conn, {"id": history.request(conn, spec, "t"), "spec": json.dumps(spec)})
    days = [str(d) for d in W.session_days(conn, "portfolio", cfg, s)]
    monkeypatch.setattr(laya_provider, "available", lambda: True)
    calls = []

    def fake_ask(conn_, s_, a, tier, ev):
        calls.append(ev["as_of"])
        day = T.et(ev["as_of"]).date().isoformat()
        return {"macro_compatibility": {"answer": "adverse" if day in days[30:40] else "supportive", "confidence": 0.9,
                                        "evidence_ids": [], "rationale": ""}}, 0.0
    monkeypatch.setattr(AM, "ask_tier", fake_ask)
    res = job(conn, {"mode": "models", "budget_usd": 10, "assumptions": {"strategy_hash": s.hash, "days": days}})
    assert res["assumptions"]["assessed"] == len(days) and max(calls) <= T.ts_at(date.fromisoformat(days[-1]), s.decide_at)
    blocks, timeline = RP.assumption_blocks(conn, s, "models", days)
    assert sorted(blocks) == days[30:40] and len(timeline) == len(days)
    rep = bt.run(conn, s, risk, days=[date.fromisoformat(d) for d in days], assumption_blocks=blocks, repeat_cycles=True)
    assert not [o for o in rep["orders"] if o["side"] == "buy" and o["day"] in blocks]
