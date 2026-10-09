import pyotp
from fastapi.testclient import TestClient

from services.api.app import app
from services.api.create_owner import create


def login(conn):
    secret = create(conn, "owner1", "correct horse battery", "owner")
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "owner1", "password": "correct horse battery", "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return c, r.json()["csrf"], secret


def test_login_requires_totp(conn):
    create(conn, "owner2", "correct horse battery", "owner")
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "owner2", "password": "correct horse battery", "code": "000000"}).status_code == 401
    assert c.get("/api/me").status_code == 401


def test_csrf_and_session_actions(conn):
    c, csrf, _ = login(conn)
    assert c.get("/api/me").json()["role"] == "owner"
    assert c.post("/api/theses", json={"text": "x"}).status_code == 403  # no CSRF header
    assert c.post("/api/theses", json={"text": "x"}, headers={"x-csrf-token": csrf}).status_code == 200
    # 2FA is checked at login only: a logged-in owner can act without another code
    r = c.post("/api/kill", json={"reason": "test"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert TestClient(app).post("/api/kill", json={}).status_code == 401  # but not without a session
    assert conn.execute("SELECT kind FROM commands").fetchone()["kind"] == "kill"


def test_viewer_is_read_only(conn):
    secret = create(conn, "viewer1", "correct horse battery", "viewer")
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "viewer1", "password": "correct horse battery", "code": pyotp.TOTP(secret).now()})
    csrf = r.json()["csrf"]
    assert c.get("/api/risk").status_code == 200
    assert c.post("/api/theses", json={"text": "x"}, headers={"x-csrf-token": csrf}).status_code == 403


def test_equity_ranges_and_strategy_attribution(conn, orb_text, risk):
    from common import registry
    from services.broker.replay import replay
    from services.broker.seed import seed
    from services.models.triage import backfill

    seed(conn, history_days=8, seed_=7)
    backfill(conn)
    registry.sync(conn, risk)
    for r in conn.execute("SELECT hash FROM strategies").fetchall():
        registry.set_status(conn, r["hash"], "paper", "pytest")
    info = replay(conn)
    assert info["sessions"] == 8 and info["points"] == 8 * 390

    c, csrf, _ = login(conn)
    full = c.get("/api/equity?range=ALL").json()
    st = full["stats"]
    assert st["sessions"] == 8 and len(full["equity"]) <= 600
    # strategy contributions add up to the account change
    assert abs(sum(b["pnl"] for b in full["by_strategy"]) - st["change"]) < 0.05
    one = c.get("/api/equity?range=1D").json()
    assert one["intraday"] and one["stats"]["sessions"] == 1
    days = [d["session"] for d in full["daily"]]
    part = c.get(f"/api/equity?range=custom&start={days[2]}&end={days[4]}").json()
    assert part["stats"]["sessions"] == 3
    assert abs(part["stats"]["start"] - full["daily"][1]["close"]) < 1e-6  # rebased on the prior close
    assert c.get(f"/api/equity?range=custom&start={days[4]}&end={days[2]}").status_code == 400
