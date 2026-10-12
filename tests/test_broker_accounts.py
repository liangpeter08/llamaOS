import httpx
import pyotp
import pytest

from common import broker_accounts as BA, db
from services.broker.alpaca import Alpaca
from tests.test_api import login


@pytest.fixture
def vault(conn, monkeypatch):
    monkeypatch.delenv("BROKER_VAULT_KEY", raising=False)
    monkeypatch.setattr(BA, "ENV_FILE", "/nonexistent/broker.env")
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization as S
    k = X25519PrivateKey.generate()
    monkeypatch.setenv("BROKER_VAULT_KEY", BA._b64(k.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())))
    BA.publish(conn, k)
    monkeypatch.setattr(BA, "check", lambda key, secret: {"account_number": "PA" + key[-4:], "equity": "100000"})
    monkeypatch.setenv("ALPACA_KEY_ID", "ENVKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")
    yield k
    db.reset(conn)  # later tests build Alpaca() from the env keys, on the public schema
    db.init(conn)


def test_seal_roundtrip_and_tamper(vault):
    pub = BA._b64(BA._raw_pub(vault.public_key()))
    token = BA.seal(pub, "s3cret")
    assert "s3cret" not in token and BA.unseal(vault, token) == "s3cret"
    raw = bytearray(BA.base64.b64decode(token))
    raw[-1] ^= 1
    with pytest.raises(Exception):
        BA.unseal(vault, BA._b64(bytes(raw)))


def test_credentials_follow_active_account(conn, vault):
    assert BA.credentials(conn)[:2] == ("ENVKEY", "envsecret")  # nothing saved: env file
    a = BA.add(conn, "first", "PKAAAAAAAAAA1111", "secret-one-xxxxxxxxxxxx", "owner1", {"account_number": "PA1"})
    b = BA.add(conn, "second", "PKBBBBBBBBBB2222", "secret-two-xxxxxxxxxxxx", "owner1", {"account_number": "PA2"})
    assert "secret-one" not in db.row(conn, "SELECT secret_sealed FROM broker_accounts WHERE id=?", (a,))["secret_sealed"]
    BA.activate(conn, a, "owner1")
    assert BA.credentials(conn)[:3] == ("PKAAAAAAAAAA1111", "secret-one-xxxxxxxxxxxx", 1)
    with pytest.raises(ValueError):
        BA.remove(conn, a, "owner1")  # active
    BA.activate(conn, b, "owner1")
    key, secret, rev, source, schema = BA.credentials(conn)
    assert (key, secret, rev, source, schema) == ("PKBBBBBBBBBB2222", "secret-two-xxxxxxxxxxxx", 2, "database", f"acct_{b}")
    BA.remove(conn, a, "owner1")
    assert [r["key_id"] for r in BA.listing(conn)] == ["PKBB…2222"]
    assert {r["kind"] for r in db.rows(conn, "SELECT kind FROM audit")} >= {"broker_account.added", "broker_account.activated", "broker_account.removed"}


def test_alpaca_client_uses_db_account(conn, vault, monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    aid = BA.add(conn, "db", "PKCCCCCCCCCC3333", "secret-three-xxxxxxxxxx", "owner1", {})
    BA.activate(conn, aid, "owner1")
    api = Alpaca()
    assert api.http.headers["APCA-API-KEY-ID"] == "PKCCCCCCCCCC3333" and api.account_rev == 1


def test_apply_guard_patches_new_account(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    cfg = {"max_margin_multiplier": "4", "no_shorting": False}
    sent = []

    def handler(r):
        if r.method == "PATCH":
            sent.append(r.content)
            cfg.update(max_margin_multiplier="1", no_shorting=True)
        return httpx.Response(200, json=cfg)
    Alpaca(httpx.Client(transport=httpx.MockTransport(handler))).apply_guard(allow_short=False)
    assert len(sent) == 1 and cfg["no_shorting"]


def test_api_add_and_switch_need_2fa(conn, vault):
    c, csrf, secret = login(conn)
    h = {"x-csrf-token": csrf}
    body = {"label": "new", "key_id": "PKDDDDDDDDDD4444", "secret": "secret-four-xxxxxxxxxxx", "activate": True}
    assert c.post("/api/broker-accounts", json={**body, "code": "000000"}, headers=h).status_code == 403
    r = c.post("/api/broker-accounts", json={**body, "code": pyotp.TOTP(secret).now()}, headers=h)
    assert r.status_code == 200, r.text
    data = c.get("/api/broker-accounts").json()
    assert data["vault_ready"] and data["accounts"][0]["active"] and "secret" not in str(data["accounts"])
    other = BA.add(conn, "other", "PKEEEEEEEEEE5555", "secret-five-xxxxxxxxxxx", "owner1", {})
    conn.execute("INSERT INTO positions(symbol,qty,side,avg_price) VALUES ('AAPL',1,'long',100)")
    r = c.post(f"/api/broker-accounts/{other}/activate", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    assert r.status_code == 400 and "unmanaged" in r.json()["detail"]
    r = c.post(f"/api/broker-accounts/{other}/activate", json={"code": pyotp.TOTP(secret).now(), "confirm_open": True}, headers=h)
    assert r.status_code == 200, r.text
    assert BA.active(conn)["id"] == other and BA.rev(conn) == 2


def _scope(conn):
    db._scope["checked"] = 0  # don't wait for the TTL in tests
    conn.follow()
    return conn.scope


def test_each_account_has_its_own_book_risk_and_deployments(conn, vault, risk):
    from common import registry
    registry.sync(conn, risk)
    h = db.row(conn, "SELECT hash FROM strategies ORDER BY hash LIMIT 1")["hash"]
    registry.set_status(conn, h, "backtested", "pytest")
    conn.execute("INSERT INTO risk_settings(key,value,updated_by,updated_at) VALUES ('account.max_open_positions','3','o',0)")

    a = BA.add(conn, "A", "PKAAAAAAAAAA1111", "secret-one-xxxxxxxxxxxx", "owner1", {})
    b = BA.add(conn, "B", "PKBBBBBBBBBB2222", "secret-two-xxxxxxxxxxxx", "owner1", {})
    BA.activate(conn, a, "owner1")
    assert _scope(conn) == "acct_%d" % a
    # risk controls are copied into a new account, then independent
    assert db.row(conn, "SELECT value FROM risk_settings")["value"] == "3"
    conn.execute("UPDATE risk_settings SET value='5'")
    registry.set_status(conn, h, "paper", "pytest")
    conn.execute("UPDATE strategies SET mode='paper_auto' WHERE hash=?", (h,))
    conn.execute("INSERT INTO trades(sid,symbol,pnl) VALUES ('s','AAPL',12.5)")
    conn.execute("INSERT INTO equity_history(ts,source,session,equity) VALUES (1,'alpaca_paper','d',100000)")
    conn.execute("UPDATE strategies SET display_name='Shared name' WHERE hash=?", (h,))

    BA.activate(conn, b, "owner1")
    assert _scope(conn) == "acct_%d" % b
    assert db.row(conn, "SELECT COUNT(*) n FROM trades")["n"] == 0
    assert db.row(conn, "SELECT COUNT(*) n FROM equity_history")["n"] == 0
    assert db.row(conn, "SELECT value FROM risk_settings")["value"] == "3"
    s = db.row(conn, "SELECT status, mode, display_name FROM strategies WHERE hash=?", (h,))
    assert s == {"status": "backtested", "mode": "paper_manual", "display_name": "Shared name"}
    assert not registry.runnable(conn, risk) and not registry.runnable(conn, risk, kind="portfolio")

    BA.activate(conn, a, "owner1")
    _scope(conn)
    assert db.row(conn, "SELECT SUM(pnl) p FROM trades")["p"] == 12.5
    assert db.row(conn, "SELECT value FROM risk_settings")["value"] == "5"
    assert db.row(conn, "SELECT status, mode FROM strategies WHERE hash=?", (h,)) == {"status": "paper", "mode": "paper_auto"}
    # new strategy files register once, for every account
    assert db.row(conn, "SELECT COUNT(*) n FROM public.strategies")["n"] == db.row(conn, "SELECT COUNT(*) n FROM strategies")["n"]


def test_pinned_process_ignores_switch(conn, vault):
    a = BA.add(conn, "A", "PKAAAAAAAAAA1111", "secret-one-xxxxxxxxxxxx", "owner1", {})
    b = BA.add(conn, "B", "PKBBBBBBBBBB2222", "secret-two-xxxxxxxxxxxx", "owner1", {})
    BA.activate(conn, a, "owner1")
    db.pin("acct_%d" % a)
    BA.activate(conn, b, "owner1")
    conn.follow()
    assert conn.scope == "acct_%d" % a  # the broker keeps writing its own book until it restarts
    db.unpin()
