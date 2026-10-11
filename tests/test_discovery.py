"""Single-ticker discovery: statistics, revisions, the recursive loop, the sealed holdout and the Claude generator."""
import json
import random
from datetime import date, timedelta

import pytest
import yaml

from common import db, registry
from discovery import mutate as M
from discovery import stats as ST
from discovery.run import Config, OfflineGenerator, run, summary
from discovery.templates import hypotheses
from dsl.validator import validate


# ---------------------------------------------------------------- statistics
def test_deflated_sharpe_discounts_many_trials():
    rng = random.Random(1)
    rets = [0.001 + rng.gauss(0, 0.01) for _ in range(250)]
    srs = [rng.gauss(0, 0.05) for _ in range(50)]
    few, many = ST.deflated_sharpe(rets, 2, srs[:2])["dsr"], ST.deflated_sharpe(rets, 50, srs)["dsr"]
    assert 0 <= many < few <= 1  # the same result is less convincing after 50 tries
    assert ST.expected_max_sharpe(1, 0.01) == 0.0
    assert ST.deflated_sharpe(rets[:5], 10, srs)["dsr"] is None


def test_pbo_separates_skill_from_noise():
    rng = random.Random(2)
    skilled = [[0.004 + rng.gauss(0, 0.002)] + [rng.gauss(0, 0.01) for _ in range(9)] for _ in range(240)]
    assert ST.pbo(skilled)["pbo"] < 0.1  # the in-sample winner stays on top out of sample
    noise = [[rng.gauss(0, 0.01) for _ in range(10)] for _ in range(240)]
    assert 0.2 < ST.pbo(noise)["pbo"] <= 1.0  # the in-sample winner is often below the median out of sample
    assert ST.pbo([[0.1]])["pbo"] is None


# ---------------------------------------------------------------- revisions
def test_templates_validate(risk):
    hs = hypotheses("AMD", risk, ["SPY"], {}, ("intraday", "daily"))
    assert {h["kind"] for h in hs} == {"intraday", "daily"} and len(hs) >= 10
    for h in hs:
        v = validate(h["yaml"], risk)
        assert v.ok, (h["family"], v.errors)
        assert v.strategy.source["prompt"] == "discovery@v1" and h["mechanism"] and h["falsify"]
        if h["kind"] == "intraday":
            assert v.strategy.universe_symbols == ["AMD"]
        else:
            assert v.strategy.symbols == ["AMD"]


def _template(risk, family):
    return next(h for h in hypotheses("AMD", risk, ["SPY"], {}, ("intraday", "daily")) if h["family"] == family)["yaml"]


def test_revisions_stay_valid_and_record_lineage(risk):
    orb = _template(risk, "orb_trend")
    text, change = M.apply(orb, {"op": "remove", "where": "entry.when_all", "index": 1, "src": "ret_d(trend_n) >= trend_min"})
    v = validate(text, risk)
    assert v.ok and len(v.strategy.entry_when_all) == 1 and "discovery revision: removed" in text
    text, change = M.apply(orb, {"op": "loosen", "param": "or_minutes", "direction": -1})
    assert validate(text, risk).strategy.params["or_minutes"] < 15
    text, change = M.apply(orb, {"op": "add_filter", "feature": "market", "bucket": "mkt_prev_down", "peer": "QQQ"})
    v = validate(text, risk)
    assert v.ok and "QQQ" in v.strategy.references and any("peer(QQQ" in c.src for c in v.strategy.entry_when_all)
    assert M.apply(text, {"op": "add_filter", "feature": "market", "bucket": "mkt_prev_down", "peer": "QQQ"}) is None  # no duplicates
    text, _ = M.apply(orb, {"op": "cap_trades"})
    assert validate(text, risk).strategy.limits["max_trades_per_day"] == 1


def test_daily_regime_filter_adds_exit_and_reentry(risk):
    stretch = _template(risk, "stretch_scaling")  # no exit_and_wait yet, so no reentry
    text, change = M.apply(stretch, {"op": "add_filter", "feature": "trend", "bucket": "trend_down"})
    s = validate(text, risk).strategy
    assert s.rules[0].action == "exit_and_wait" and "ret_d(20) < trend_floor" in s.rules[0].when.src
    assert s.reentry_when is not None and "rebuild once" in change
    trend = _template(risk, "trend_timing")  # existing reentry: both must hold
    text, _ = M.apply(trend, {"op": "add_filter", "feature": "vol", "bucket": "vol_high"})
    s = validate(text, risk).strategy
    assert " and " in s.reentry_when.src
    # removing the only exit_and_wait rule drops the reentry with it
    text, _ = M.apply(text, {"op": "remove", "where": "rules", "index": 0})
    text, _ = M.apply(text, {"op": "remove", "where": "rules", "index": 0}) or (text, "")
    assert validate(text, risk).ok


# ---------------------------------------------------------------- the loop on daily data with a planted edge
def seed_trending(conn, n=420, seed=3, edge=0.004):
    """TKR alternates 45-session up and down trends on top of SPY; holding it earns nothing, timing the trend does."""
    rng = random.Random(seed)
    d, px, mk, rows = date(2024, 1, 2), 100.0, 100.0, []
    for k in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        m = rng.gauss(0.0004, 0.01)
        regime = 1 if (k // 45) % 2 == 0 else -1
        mk *= 1 + m
        px *= 1 + 1.3 * m + edge * regime + rng.gauss(0, 0.008)
        rows += [("TKR", d.isoformat(), px, px * 1.01, px * 0.99, px, 8_000_000), ("SPY", d.isoformat(), mk, mk, mk, mk, 50_000_000)]
        d += timedelta(days=1)
    conn.executemany("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)", rows)


def test_daily_discovery_finds_planted_edge_and_seals_holdout(conn, risk, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"]), "test", risk=risk)
    r = rep["kinds"]["daily"]
    rows = db.rows(conn, "SELECT * FROM discovery_trials WHERE run_id=? ORDER BY id", (rep["run_id"],))
    assert len(rows) == r["trials"] >= 10 and r["improved"] >= 1
    assert any(t["generation"] > 0 and t["parent_id"] and t["change"] for t in rows)  # recursive revisions with lineage
    # the holdout is sealed: no trial is scored on it except the finalists, once each
    hold_start = r["holdout_sessions"][0]
    for t in rows:
        ev = json.loads(t["evaluation"] or "{}")
        assert all(d < hold_start for d in ev.get("days") or [])
    scored = [t for t in rows if t["holdout"]]
    assert {t["status"] for t in scored} <= {"holdout_pass", "holdout_fail"} and len(scored) == len(r["finalists"]) <= 2
    assert any(f["passed"] for f in r["finalists"]), summary(rep)
    best = next(f for f in r["finalists"] if f["passed"])
    assert best["holdout"]["dsr"]["n_trials"] == r["trials"]
    # survivors are saved as drafts with their evidence and lineage in the file
    assert rep["drafts"] and all((tmp_path / d["path"]).exists() for d in rep["drafts"])
    saved = (tmp_path / rep["drafts"][0]["path"]).read_text()
    assert f"discovery run #{rep['run_id']}" in saved and "sealed holdout" in saved
    st = db.row(conn, "SELECT status FROM strategies WHERE hash=?", (rep["drafts"][0]["hash"],))
    assert st and st["status"] == "draft"  # never promoted by discovery
    run_row = db.row(conn, "SELECT status, report FROM discovery_runs WHERE id=?", (rep["run_id"],))
    assert run_row["status"] == "completed" and json.loads(run_row["report"])["kinds"]["daily"]["trials"] == r["trials"]


def test_noise_is_not_promoted(conn, risk, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn, seed=5, edge=0.0)  # no edge at all
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=2), "test", risk=risk)
    r = rep["kinds"]["daily"]
    passed = [f for f in r["finalists"] if f["passed"]]
    # pure noise may survive by luck, but then its deflated Sharpe must stay unconvincing
    assert all((f["holdout"]["dsr"]["dsr"] or 0) < 0.95 for f in passed)


def test_missing_data_is_explained(conn, risk):
    with pytest.raises(ValueError, match="daily discovery needs at least 140"):
        run(conn, Config(ticker="NONE", kinds=["daily"]), "test", risk=risk)
    row = db.row(conn, "SELECT status, error FROM discovery_runs ORDER BY id DESC LIMIT 1")
    assert row["status"] == "failed" and "140" in row["error"]


# ---------------------------------------------------------------- intraday on simulated sessions
def test_intraday_discovery_on_simulated_sessions(conn, risk):
    from services.broker.seed import seed
    seed(conn, history_days=30, seed_=7)
    rep = run(conn, Config(ticker="ACMX", peers=["BOLT"], kinds=["intraday"], generations=2, min_trades=8, save_drafts=False),
              "test", risk=risk)
    r = rep["kinds"]["intraday"]
    assert r["peers"] == ["BOLT"] and r["trials"] >= 6 and r["evaluated"] == r["trials"] - r["duplicates"]
    assert rep["profile"]["intraday"]["sessions"] >= 10
    assert rep["drafts"] == []
    for t in db.rows(conn, "SELECT yaml FROM discovery_trials WHERE run_id=?", (rep["run_id"],)):
        s = validate(t["yaml"], risk).strategy
        assert s.universe_symbols == ["ACMX"] and set(s.references) <= {"BOLT"}
    assert "INTRADAY" in summary(rep)


# ---------------------------------------------------------------- Claude generator (model calls faked)
def test_claude_generator_proposes_repairs_and_falls_back(conn, risk, monkeypatch):
    from services.models import discovery as CD
    from services.models import llm
    orb = _template(risk, "orb_trend").replace("AMD", "TKR").replace("amd", "tkr")
    calls = []

    def fake_call(conn, *, task, schema, messages, **kw):
        calls.append(task)
        if task.startswith("discovery.propose"):
            bad = orb.replace("ret_d(trend_n) >= trend_min", "ret_d(trend_n) >= 0.02")  # bare number: validator rejects
            return "", {"hypotheses": [{"family": "Lead Lag!", "title": "t", "mechanism": "m", "falsify": "f", "evidence": "e", "yaml": bad}]}, None
        if task == "discovery.repair":
            return "", {"change": "fixed", "addresses": "validator", "why": "w", "yaml": orb}, None
        raise llm.LLMUnavailable("quota")

    monkeypatch.setattr(CD.llm, "call", fake_call)
    monkeypatch.setattr(CD, "_model_id", lambda key: "claude-opus-5-5")
    gen = CD.ClaudeGenerator()
    cfg = Config(ticker="TKR", kinds=["intraday"])
    out = gen.propose(conn, cfg, "intraday", {}, [], risk)
    mine = [h for h in out if h["origin"] == "claude-opus-5-5"]
    assert len(mine) == 1 and mine[0]["family"] == "lead_lag"
    s = validate(mine[0]["yaml"], risk).strategy
    assert s.id == "disc_tkr_lead_lag" and s.source["compiled_by"] == "claude-opus-5-5"  # ids and provenance pinned by code
    assert calls == ["discovery.propose.intraday", "discovery.repair"]
    # a failing revision call falls back to the deterministic fixes
    ev = {"weaknesses": [{"kind": "cost_sensitive", "fix": {"op": "cap_trades"}}], "regimes": {}}
    revs = gen.revise(conn, cfg, {"yaml": orb, "kind": "intraday", "family": "orb_trend", "id": 1}, ev, [], risk, 2)
    assert revs and revs[0]["origin"] == OfflineGenerator.name and gen.errors


# ---------------------------------------------------------------- API
def test_api_starts_and_reads_discovery_runs(conn, risk, monkeypatch, tmp_path):
    from services.api import app as api
    from tests.test_api import login
    sent = {}

    def fake_models(path, payload, timeout=300):
        sent.update(path=path, payload=payload)
        return {"run_id": 7, "model": "templates@v1"}

    monkeypatch.setattr(api, "models_api", fake_models)
    c, csrf, _ = login(conn)
    r = c.post("/api/discovery", json={"ticker": "amd", "kinds": ["daily"]}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200 and "#7" in r.json()["message"]
    assert sent["path"] == "/discovery/run" and sent["payload"]["config"]["kinds"] == ["daily"]
    assert c.post("/api/discovery", json={"ticker": "AMD"}).status_code == 403  # CSRF

    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=1, save_drafts=False), "test", risk=risk)
    runs = c.get("/api/discovery").json()["runs"]
    assert runs[0]["id"] == rep["run_id"] and runs[0]["status"] == "completed"
    detail = c.get(f"/api/discovery/{rep['run_id']}").json()
    assert detail["run"]["report"]["kinds"]["daily"]["trials"] == len(detail["trials"])
    assert all("summary" in t and "evaluation" not in t for t in detail["trials"])
    assert c.get("/api/discovery/99999").status_code == 404


def test_follow_up_run_iterates_seeds_and_counts_prior_trials(conn, risk, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    first = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=1), "test", risk=risk)
    r1 = first["kinds"]["daily"]
    pick = db.rows(conn, "SELECT id FROM discovery_trials WHERE run_id=? AND status IN ('evaluated','not_better','improved')"
                         " ORDER BY score DESC LIMIT 2", (first["run_id"],))
    follow = run(conn, Config(ticker="TKR", seed_trials=[p["id"] for p in pick], generations=2), "test", risk=risk)
    r2 = follow["kinds"]["daily"]
    assert follow["parent_run_id"] == first["run_id"] and list(follow["kinds"]) == ["daily"]
    assert r2["prior_trials"] == r1["trials"] and r2["holdout_prior_looks"] == len(r1["finalists"])
    rows = db.rows(conn, "SELECT origin, generation FROM discovery_trials WHERE run_id=? ORDER BY id", (follow["run_id"],))
    assert [r["origin"] for r in rows[:2]] == ["seed", "seed"] and all(r["origin"] != "seed" for r in rows[2:])  # no templates
    for f in r2["finalists"]:
        assert f["holdout"]["dsr"]["n_trials"] == r1["trials"] + r2["trials"]
        assert f["origin"] != "seed"  # unchanged seeds are never re-scored on the holdout
    stored = db.rows(conn, "SELECT evaluation FROM discovery_trials WHERE run_id=? AND status='evaluated'", (first["run_id"],))
    assert all("sr" in json.loads(x["evaluation"]) for x in stored)  # follow-ups read every ancestor trial's Sharpe
    # a saved strategy can be the seed too; it must be a single-ticker file for this ticker
    if first["drafts"]:
        again = run(conn, Config(ticker="TKR", seed_hash=first["drafts"][0]["hash"], generations=1, save_drafts=False), "test", risk=risk)
        assert again["seeds"][0].startswith("seed: strategy")
    with pytest.raises(ValueError, match="single-ticker"):
        run(conn, Config(ticker="SPY", seed_trials=[pick[0]["id"]]), "test", risk=risk)


def test_api_preflight_trial_detail_and_provenance(conn, risk, monkeypatch, tmp_path):
    from services.api import app as api
    from tests.test_api import login
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    c, csrf, _ = login(conn)
    pf = c.get("/api/discovery/preflight?ticker=tkr&peers=SPY").json()
    assert pf["ticker"] == "TKR" and pf["kinds"]["daily"]["ok"] and not pf["kinds"]["intraday"]["ok"]
    assert pf["peers"][0]["symbol"] == "SPY" and pf["peers"][0]["daily"] > 0
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=1), "test", risk=risk)
    detail = c.get(f"/api/discovery/{rep['run_id']}").json()
    assert any(t["promising"] for t in detail["trials"])
    tid = detail["trials"][0]["id"]
    t = c.get(f"/api/discovery/trials/{tid}").json()
    assert t["yaml"] and t["back_translation"] and "weaknesses" in t["evaluation"]
    sent = {}
    monkeypatch.setattr(api, "models_api", lambda path, payload, timeout=300: sent.update(payload) or {"run_id": 9, "model": "templates@v1"})
    c.post("/api/discovery", json={"ticker": "TKR", "seed_trials": [tid]}, headers={"x-csrf-token": csrf})
    assert sent["config"]["seed_trials"] == [tid] and sent["config"]["templates"] is False  # follow-ups iterate the seeds only
    if rep["drafts"]:
        s = c.get(f"/api/strategies/{rep['drafts'][0]['hash']}").json()["discovery"]
        assert s["ticker"] == "TKR" and s["kind"] == "daily" and s["made_by"]["run_id"] == rep["run_id"]


def test_any_evaluated_trial_can_be_saved_as_a_strategy(conn, risk, monkeypatch, tmp_path):
    from tests.test_api import login
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=1, save_drafts=False), "test", risk=risk)
    c, csrf, _ = login(conn)
    trials = c.get(f"/api/discovery/{rep['run_id']}").json()["trials"]
    t = next(x for x in trials if x["status"] in ("evaluated", "not_better", "improved"))
    r = c.post(f"/api/discovery/trials/{t['id']}/save", headers={"x-csrf-token": csrf}).json()
    s = c.get(f"/api/strategies/{r['hash']}").json()
    assert s["s"]["status"] == "draft" and s["s"]["sid"].endswith(f"_t{t['id']}")
    assert all(isinstance(a, str) for a in s["assumptions"]) and f"trial #{t['id']}" in s["assumptions"][-1]
    again = c.post(f"/api/discovery/trials/{t['id']}/save", headers={"x-csrf-token": csrf}).json()
    assert again["hash"] == r["hash"] and "already" in again["message"]  # idempotent
    bad = next((x for x in trials if x["status"] in ("invalid", "duplicate")), None)
    if bad:
        assert c.post(f"/api/discovery/trials/{bad['id']}/save", headers={"x-csrf-token": csrf}).status_code == 400


def test_save_all_saves_every_evaluated_trial(conn, risk, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)
    seed_trending(conn)
    rep = run(conn, Config(ticker="TKR", peers=["SPY"], kinds=["daily"], generations=1, save_all=True), "test", risk=risk)
    n = db.row(conn, "SELECT COUNT(*) n FROM discovery_trials WHERE run_id=? AND status IN"
                     " ('evaluated','improved','not_better','holdout_pass','holdout_fail')", (rep["run_id"],))["n"]
    assert len(rep["drafts"]) == n > 1 and len(list(tmp_path.glob("*.yaml"))) == n
