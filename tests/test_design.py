from pathlib import Path

import pytest

from common import db
from services.models import authoring, llm

ROOT = Path(__file__).resolve().parent.parent
GOOD = (ROOT / "strategies" / "orb_news_long.v1.yaml").read_text().replace("id: orb_news_long", "id: orb_news_designed")
BAD = GOOD.replace("rvol(20) >= rvol_min", "rvol(20) >= 2")
THREAD = "design:abc123def"


@pytest.fixture
def claude(monkeypatch):
    """Scripted Opus replies; records what each call was sent."""
    calls, replies = [], []
    monkeypatch.setattr(llm, "available", lambda: True)

    def fake_call(conn, **kw):
        calls.append(kw)
        return replies.pop(0), None, None
    monkeypatch.setattr(llm, "call", fake_call)
    return calls, replies


def test_design_turns_validate_and_repair(conn, claude):
    calls, replies = claude
    replies += ["Before drafting: what counts as strong volume? I propose rvol(20) >= 2."]
    res = authoring.design(conn, THREAD, "buy breakouts on news", "owner")
    assert res["draft"] is None
    first = calls[0]
    assert first["model"] == "claude-opus-5-5" and first["effort"] == "high" and first["cache"]
    assert "existing_strategies" in first["messages"][0]["content"] and "buy breakouts on news" in first["messages"][0]["content"]
    assert "strategy architect" in first["system"] and "DSL reference" in first["system"]

    # a draft that breaks a validator rule gets one automatic repair round
    replies += [f"Draft:\n```yaml\n{BAD}```", f"Fixed the bare number.\n```yaml\n{GOOD}```"]
    res = authoring.design(conn, THREAD, "yes, draft it", "owner")
    assert res["draft"]["ok"] and res["draft"]["id"] == "orb_news_designed"
    assert calls[-1]["messages"][-1]["content"].startswith("[validator]")
    roles = [(r["role"], r["model"]) for r in authoring.design_messages(conn, THREAD)]
    assert roles[-2] == ("user", "validator") and len(roles) == 6
    with pytest.raises(KeyError):
        authoring.design(conn, "strategy:nope", "x", "owner")


def test_design_api_backtest_and_save(conn, claude, monkeypatch, tmp_path):
    from tests.test_api import login
    from services.api import app
    from common import registry
    (tmp_path / "strategies").mkdir()
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path / "strategies")  # saves go to a scratch folder
    calls, replies = claude
    monkeypatch.setattr(app, "models_api", lambda path, payload, timeout=0: authoring.design(conn, payload["thread"], payload["message"], payload["actor"]))
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    replies += [f"Here is a first draft.\n```yaml\n{GOOD}```"]
    r = c.post(f"/api/design/{THREAD}", json={"message": "news breakouts"}, headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["draft"]["ok"] and v["messages"][0]["content"] == "news breakouts" and v["messages"][0]["has_context"]
    assert c.get("/api/design").json()["threads"][0]["title"] == "news breakouts"

    # backtest of the unsaved draft lands in the conversation for Claude's next turn
    from services.broker.seed import seed
    seed(conn, history_days=5, seed_=3)
    r = c.post(f"/api/design/{THREAD}/backtest", json={"yaml": v["draft"]["yaml"]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["backtest"]["sessions"] == 5
    assert r.json()["messages"][-1]["content"].startswith("[backtest] Draft orb_news_designed")

    r = c.post(f"/api/design/{THREAD}/save", json={"yaml": v["draft"]["yaml"], "name": ""}, headers=h)
    assert r.status_code == 200, r.text
    hsh = r.json()["hash"]
    assert db.row(conn, "SELECT status, sid FROM strategies WHERE hash=?", (hsh,)) == {"status": "draft", "sid": "orb_news_designed"}
    assert c.post(f"/api/design/{THREAD}/save", json={"yaml": BAD}, headers=h).status_code == 400
    assert c.post("/api/design/bad/backtest", json={"yaml": GOOD}, headers=h).status_code == 400
    assert (tmp_path / "strategies" / "orb_news_designed.v1.yaml").exists()


# ---------------------------------------------------------------- review panel
def test_review_stats():
    from core import review_stats as RS
    closes = [100, 104, 103, 96, 97, 102, 101, 99, 108, 107] * 3
    days = [f"d{i}" for i in range(len(closes))]
    st = RS.symbol_stats(closes, days)
    assert st["days_up_over_5pct"] >= 3 and st["days_down_over_5pct"] >= 3 and st["sessions"] == 30
    c = RS.correlations({"A": closes, "B": [x * 2 for x in closes], "C": list(reversed(closes))})
    assert c["highest"]["corr"] == pytest.approx(1.0) and c["highest"]["pair"] == "A/B"
    assert RS.basket_worst_day({"A": closes, "B": closes}, {"A": .5, "B": .5})["worst_day"] < -0.06


def panel_reply(role):
    return {"verdict": "approve_with_changes", "score": 6, "summary": f"{role} summary", "key_points": ["k"],
            "positives": ["p"], "concerns": [{"severity": "high", "text": f"{role} concern"}],
            "suggestions": [{"text": "lower capital", "change": "capital: 45%"}]}


def test_review_panel(conn, monkeypatch, tmp_path):
    from tests.test_api import login
    from services.api import app
    portfolio = (ROOT / "strategies" / "big_tech_software_tilt_hold.v1.yaml").read_text()
    seen = []
    monkeypatch.setattr(llm, "available", lambda: True)

    def fake_call(conn_, **kw):
        role = kw["task"].split(".")[1]
        seen.append((role, kw["model"], kw["schema"] is authoring.PANEL_SCHEMA, kw["system"]))
        if role == "trader":
            raise llm.LLMUnavailable("quota")
        return "", panel_reply(role), None
    monkeypatch.setattr(llm, "call", fake_call)
    monkeypatch.setattr(app, "models_api", lambda path, p, timeout=0: authoring.review(conn, p["thread"], p["yaml"], p["actor"], p["backtest"]))
    c, csrf, _ = login(conn)
    conn.execute("INSERT INTO chats(ts, thread, role, content, model) VALUES (1, ?, 'assistant', ?, 'claude-opus-5-5')",
                 (THREAD, f"Draft:\n```yaml\n{portfolio}```"))
    r = c.post(f"/api/design/{THREAD}/review", json={"yaml": portfolio}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    v = r.json()
    assert sorted(x[0] for x in seen) == ["analyst", "researcher", "risk", "trader"]
    assert all(m == "claude-opus-5-5" and schema for _, m, schema, _ in seen)
    assert any("RISK TEAM" in sysp for role, _, _, sysp in seen if role == "risk")
    assert v["reviews"]["risk"]["verdict"] == "approve_with_changes" and not v["reviews"]["trader"]["ok"]
    panel = v["messages"][-1]["content"]
    assert panel.startswith("[panel] Review panel on draft big_tech_software_tilt_hold")
    assert "Risk Team (approve_with_changes, 6/10)" in panel and "Trader: failed" in panel and "-> capital: 45%" in panel
    ev = authoring.panel_evidence(conn, authoring.validate(portfolio, authoring.load_risk()).strategy, authoring.load_risk())
    assert set(ev["symbols"]) == {"MSFT", "GOOGL", "META", "ORCL", "NFLX", "NVDA", "AAPL", "AMZN", "AVGO", "TSLA"}
    assert ev["exposure"]["peak_share_of_equity"] == pytest.approx(0.9)


# ---------------------------------------------------------------- duplicate / duplicate-and-improve
def test_duplicate_and_improve(conn, claude, monkeypatch, tmp_path):
    from tests.test_api import login
    from services.api import app
    from common import registry
    from common.config import load_risk
    (tmp_path / "strategies").mkdir()
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path / "strategies")
    orig = registry.write_new_version("orb_news_long", (ROOT / "strategies" / "orb_news_long.v1.yaml").read_text())
    registry.sync(conn, load_risk())
    src = db.row(conn, "SELECT hash FROM strategies WHERE sid='orb_news_long'")["hash"]
    registry.set_category(conn, "orb_news_long", "breakouts", "owner")
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}

    # plain copy: a new sid at v1, same rules, category carried over; the original is untouched
    r = c.post(f"/api/strategies/{src}/duplicate", json={"name": "ORB copy"}, headers=h)
    assert r.status_code == 200, r.text
    row = db.row(conn, "SELECT sid, version, status, display_name FROM strategies WHERE hash=?", (r.json()["hash"],))
    assert row == {"sid": "orb_copy", "version": 1, "status": "draft", "display_name": "ORB copy"}
    assert db.row(conn, "SELECT category FROM strategy_categories WHERE sid='orb_copy'")["category"] == "breakouts"
    assert orig.read_text() == (ROOT / "strategies" / "orb_news_long.v1.yaml").read_text()
    assert c.post(f"/api/strategies/{src}/duplicate", json={"name": "ORB copy"}, headers=h).status_code == 409
    assert c.post(f"/api/strategies/{src}/duplicate", json={"name": "orb news long"}, headers=h).status_code == 409
    assert c.post(f"/api/strategies/{'0' * 64}/duplicate", json={"name": "x y"}, headers=h).status_code == 404

    # improve: the first turn carries the original file and the task; the UI only shows the complaint
    seen = []
    monkeypatch.setattr(app, "models_api", lambda path, p, timeout=0: seen.append(p) or authoring.design(conn, p["thread"], p["message"], p["actor"], p["source"]))
    improved = GOOD.replace("id: orb_news_designed", "id: orb_news_long_improved")
    claude[1].append(f"The stop is too wide.\n```yaml\n{improved}```")
    r = c.post(f"/api/design/{THREAD}", json={"message": "losses are bigger than wins", "source": src}, headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    first = claude[0][0]["messages"][0]["content"]
    assert f'<strategy_to_improve sid="orb_news_long" version="1" sha256="{src}">' in first and "<task>" in first
    assert first.index("<strategy_to_improve") < first.index("<my_idea>")
    assert v["messages"][0]["content"] == "losses are bigger than wins"
    assert v["improving"] == {"sid": "orb_news_long", "version": 1, "hash": src}
    assert v["draft"]["ok"] and v["draft"]["id"] == "orb_news_long_improved"
    assert c.get("/api/design").json()["threads"][0]["title"] == "Improve orb_news_long v1: losses are bigger than wins"
    # later turns don't re-send the original
    claude[1].append("Sure.")
    c.post(f"/api/design/{THREAD}", json={"message": "ok", "source": src}, headers=h)
    assert "<strategy_to_improve" not in claude[0][-1]["messages"][-1]["content"]
    assert c.post("/api/design/design:zzz999zzz", json={"message": "x", "source": "0" * 64}, headers=h).status_code == 404
