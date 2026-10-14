"""Owner-managed positions and reconciliation with trades made directly in the Alpaca app."""
import json
import time
from datetime import date

import pytest

from common import db, manual as M, sleeves
from services.broker import manual as MD
from services.broker.alpaca import iso
from services.broker.paper import PaperBroker


class FakeAlpaca:
    feed = "iex"

    def __init__(self):
        self.open, self.closed, self.sent, self.cancelled, self.price = [], [], [], [], {}

    def orders(self, status="open", after=None):
        return list(self.open if status == "open" else self.closed)

    def order(self, oid):
        return next((o for o in self.open + self.closed if o["id"] == oid), None)

    def by_client_id(self, cid):
        return None

    def snapshots(self, syms):
        return {s: {"latestTrade": {"p": self.price.get(s, 100.0), "t": iso(time.time())}} for s in syms}

    def asset(self, sym):
        return {"tradable": True, "shortable": True}

    def submit(self, body):
        o = {"id": f"o{len(self.sent) + 1}", "created_at": iso(time.time()), "status": "new", "filled_qty": "0",
             "filled_avg_price": None, "legs": [], **body}
        for k in ("take_profit", "stop_loss"):
            if k in body:
                leg_type = "limit" if k == "take_profit" else "stop"
                o["legs"].append({"id": o["id"] + k[0], "symbol": body["symbol"], "type": leg_type, "status": "held",
                                  "side": "sell" if body["side"] == "buy" else "buy", "qty": body["qty"], "filled_qty": "0",
                                  "created_at": o["created_at"], **({"limit_price": body[k]["limit_price"]} if leg_type == "limit"
                                                                     else {"stop_price": body[k]["stop_price"]})})
        self.sent.append(body)
        self.open.append(o)
        return o

    def cancel(self, oid):
        self.cancelled.append(oid)
        self.open = [o for o in self.open if o["id"] != oid]
        for o in self.open:
            for leg in o.get("legs") or []:
                if leg["id"] == oid:
                    leg["status"] = "canceled"
        # a filled parent whose legs are all done leaves the open list
        self.open = [o for o in self.open if o["status"] != "filled" or any(x["status"] not in ("canceled", "filled") for x in o["legs"])]

    def close_position(self, sym):
        return self.submit({"symbol": sym, "qty": "0", "side": "sell", "type": "market", "time_in_force": "day",
                            "client_order_id": "alpaca-close"})


def filled(oid, sym, side, qty, px, cid="web-app"):
    t = iso(time.time())
    return {"id": oid, "symbol": sym, "side": side, "qty": str(qty), "filled_qty": str(qty), "filled_avg_price": str(px),
            "status": "filled", "type": "market", "created_at": t, "filled_at": t, "client_order_id": cid, "legs": []}


def position(sym, qty, avg, last=None, side="long"):
    last = last or avg
    return {"symbol": sym, "qty": str(qty if side == "long" else -qty), "side": side, "avg_entry_price": str(avg),
            "current_price": str(last), "market_value": str(qty * last), "unrealized_pl": str((last - avg) * qty)}


@pytest.fixture
def b(conn, monkeypatch):
    monkeypatch.setenv("RISK_FILE", str(__import__("pathlib").Path("config/risk.example.yaml").resolve()))
    monkeypatch.setenv("MODE", "paper")
    monkeypatch.setattr(MD, "SETTLE", 0)
    br = PaperBroker(api=FakeAlpaca(), conn=conn)
    br.day = date.today()
    br.clock = {"is_open": True}
    br.account_data = {"equity": "1000000", "last_equity": "1000000", "cash": "1000000", "buying_power": "2000000"}
    yield br
    conn.execute("SELECT pg_advisory_unlock(?)", (0x5041504552,))


def run(b, kind, **payload):
    cid = b.conn.insert("INSERT INTO commands(ts, kind, payload, issued_by) VALUES (?,?,?,?)",
                        (time.time(), kind, json.dumps(payload), "owner1"))
    cmd = db.row(b.conn, "SELECT * FROM commands WHERE id=?", (cid,))
    return b.manual.handle(cmd)


def sleeve_buy(conn, h, sym, qty, px):
    conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,ts,symbol,side,qty,status,client_id,filled_qty,avg_price)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (h, "tilt", "2026-10-10", time.time() - 86400, sym, "buy", qty, "filled",
                                                     f"c-{h[:4]}-{sym}", qty, px))


def test_sleeve_close_in_alpaca_is_booked_at_the_real_fill(b, conn):
    sleeve_buy(conn, "a" * 64, "AAPL", 293, 200.0)
    sleeve_buy(conn, "b" * 64, "AAPL", 7, 210.0)
    b.sleeve_syms = b.desk.owned()
    b.api.closed = [filled("x1", "AAPL", "sell", 300, 250.0)]
    b.manual.reconcile(force=False)
    assert {h: x["AAPL"].qty for h, x in sleeves.all_holdings(conn).items()} == {"a" * 64: 0, "b" * 64: 0}
    sells = db.rows(conn, "SELECT sid, qty, filled_qty, avg_price, status, broker_id FROM portfolio_orders WHERE side='sell' ORDER BY qty DESC")
    assert [(r["qty"], r["avg_price"], r["status"], r["broker_id"]) for r in sells] == [(293, 250.0, "filled", "x1"), (7, 250.0, "filled", "x1")]
    assert round(db.row(conn, "SELECT SUM(pnl) p FROM trades")["p"], 2) == round(293 * 50 + 7 * 40, 2)
    assert b.manual.items == []
    b.manual.reconcile(force=True)  # booked once: the Alpaca order is now known
    assert db.row(conn, "SELECT COUNT(*) n FROM portfolio_orders WHERE side='sell'")["n"] == 2


def test_auto_off_waits_for_the_owner(b, conn):
    db.kv_set(conn, M.AUTO_KEY, False)
    sleeve_buy(conn, "a" * 64, "MSFT", 10, 400.0)
    b.sleeve_syms = b.desk.owned()
    b.manual.reconcile()
    assert [(x["symbol"], x["kind"], x["llama"], x["alpaca"]) for x in b.manual.items] == [("MSFT", "sleeve", 10, 0.0)]
    assert sleeves.all_holdings(conn)["a" * 64]["MSFT"].qty == 10
    assert run(b, "reconcile_accept", symbol="MSFT") == "booked 1 reconciliation fix"
    assert sleeves.all_holdings(conn)["a" * 64]["MSFT"].qty == 0  # no Alpaca order found: booked at the latest price


def test_open_protect_partial_close_and_external_close(b, conn):
    msg = run(b, "manual_open", symbol="nvda", side="long", qty=10, type="limit", limit_price=100, stop=95, target=110, tif="gtc")
    assert msg.startswith("buy 10 NVDA sent"), msg
    body = b.api.sent[-1]
    assert body["order_class"] == "bracket" and body["stop_loss"] == {"stop_price": "95.00"} and body["client_order_id"].startswith("llama-m")
    it = db.row(conn, "SELECT * FROM intents")
    assert (it["sid"], it["status"], it["stop"], it["target"], it["mode"]) == ("manual", "submitted", 95, 110, "manual")
    # it fills; the bracket legs stay open
    entry = b.api.open[0]
    entry.update(status="filled", filled_qty="10", filled_avg_price="100", filled_at=iso(time.time()))
    b.track_order(entry, it["id"], "entry")
    b.api.open = [entry]
    b.positions, b.open_orders = [position("NVDA", 10, 100, 104)], b.api.orders()
    assert b.owner_intent("NVDA")["sid"] == "manual"
    assert b.manual.protection("NVDA") == {"target": 110.0, "stop": 95.0}

    assert run(b, "manual_protect", symbol="NVDA", stop=98, target=None) == "NVDA: stop 98 set (good till cancelled)"
    assert b.api.sent[-1]["type"] == "stop" and b.api.sent[-1]["time_in_force"] == "gtc"
    assert db.row(conn, "SELECT stop, target FROM intents WHERE id=?", (it["id"],)) == {"stop": 98, "target": None}

    b.open_orders = b.api.orders()
    msg = run(b, "manual_close", symbol="NVDA", qty=4)
    assert msg.startswith("closing 4 of 10 NVDA") and "re-placed" in msg
    assert b.api.sent[-1]["qty"] == "4" and b.api.sent[-1]["side"] == "sell"
    follow = db.row(conn, "SELECT payload FROM commands WHERE kind='manual_protect' AND issued_by='owner1' ORDER BY id DESC LIMIT 1")
    assert json.loads(follow["payload"])["stop"] == 98

    # the rest is closed in the Alpaca app: booked as a trade at the real price
    b.api.open = []
    part = db.row(conn, "SELECT broker_id FROM paper_orders WHERE kind='manual'")["broker_id"]
    b.track_order(filled(part, "NVDA", "sell", 4, 105.0), it["id"], "manual")
    b.api.closed = [filled("web-1", "NVDA", "sell", 6, 107.0)]
    b.positions, b.open_orders = [], []
    b.manual.reconcile()
    trades = db.rows(conn, "SELECT qty, exit_price, pnl, exit_kind FROM trades ORDER BY id")
    assert [(t["qty"], t["exit_price"], t["exit_kind"]) for t in trades] == [(4, 105.0, "manual"), (6, 107.0, "manual")]
    assert b.owner_intent("NVDA") is None and b.manual.items == []


def test_alpaca_opened_position_and_order_are_adopted(b, conn):
    b.positions = [position("TSLA", 5, 300)]
    b.api.open = [{**filled("web-9", "AMD", "buy", 20, 0), "status": "new", "filled_qty": "0", "type": "limit", "limit_price": "150"}]
    b.open_orders = b.api.orders()
    b.manual.reconcile()
    ref = b.owner_intent("TSLA")
    assert ref and ref["sid"] == "manual" and ref["entry"] == 300
    amd = db.row(conn, "SELECT i.sid, p.broker_id FROM intents i JOIN paper_intents p ON p.intent_id=i.id WHERE i.symbol='AMD'")
    assert amd == {"sid": "manual", "broker_id": "web-9"}
    assert b.manual.items == []


def test_open_is_rejected_when_unsafe(b, conn):
    sleeve_buy(conn, "a" * 64, "AAPL", 10, 200.0)
    b.sleeve_syms = b.desk.owned()
    assert "portfolio sleeve" in run(b, "manual_open", symbol="AAPL", qty=1)
    assert "buying power" in run(b, "manual_open", symbol="NVDA", qty=100000)
    assert "max position" in run(b, "manual_open", symbol="NVDA", qty=15000)  # $1.5M of $1M equity
    assert "below the entry" in run(b, "manual_open", symbol="NVDA", qty=1, type="limit", limit_price=100, stop=101)
    b.clock = {"is_open": False}
    assert "market is closed" in run(b, "manual_open", symbol="NVDA", qty=1)
    db.kv_set(conn, "kill_switch", {"active": True})
    assert "kill switch" in run(b, "manual_open", symbol="NVDA", qty=1, type="limit", limit_price=100)
    assert b.api.sent == []


def test_hold_overnight_only_for_manual_positions(b, conn):
    assert run(b, "manual_open", symbol="IBM", qty=1, type="limit", limit_price=100).startswith("buy 1 IBM")
    it = db.row(conn, "SELECT id FROM intents")
    b.track_order({**b.api.open[0], "status": "filled", "filled_qty": "1", "filled_avg_price": "100", "filled_at": iso(time.time())}, it["id"], "entry")
    assert run(b, "manual_hold", symbol="IBM", on=True) == "IBM is held overnight"
    assert M.holds(conn) == {"IBM"}
    assert "only manual positions" in run(b, "manual_hold", symbol="SPY", on=True)


def test_api_queues_owner_commands(conn):
    from fastapi.testclient import TestClient
    from tests.test_api import login
    from services.api import app
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    r = c.post("/api/positions/order", json={"symbol": "aapl", "qty": 5, "type": "limit", "limit_price": 190, "stop": 180}, headers=h)
    assert r.status_code == 200, r.text
    cmd = db.row(conn, "SELECT kind, payload FROM commands WHERE id=?", (r.json()["command"],))
    assert cmd["kind"] == "manual_open" and json.loads(cmd["payload"])["symbol"] == "AAPL"
    assert c.post("/api/positions/order", json={"symbol": "AAPL", "qty": 5, "type": "limit"}, headers=h).status_code == 400
    assert c.post("/api/positions/AAPL/close", json={"qty": 2}, headers=h).status_code == 200
    assert c.post("/api/orders/not-an-id!/cancel", json={}, headers=h).status_code in (400, 404)
    assert c.post("/api/settings/auto-reconcile", json={"on": False}, headers=h).status_code == 200
    assert M.auto(conn) is False
    view = c.get("/api/positions/manage").json()
    assert view["auto"] is False and [x["kind"] for x in view["commands"]][:2] == ["manual_close", "manual_open"]
    assert c.get(f"/api/commands/{r.json()['command']}").json()["status"] == "pending"
    assert TestClient(app.app).post("/api/positions/order", json={"symbol": "AAPL", "qty": 1}).status_code in (401, 403)
