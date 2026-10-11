import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from common import db, registry, sleeves
from common import timeutil as T
from common.config import load_risk
from core import portfolio as P
from dsl.explain import back_translate
from dsl.validator import validate

ROOT = Path(__file__).resolve().parent.parent
USER = (ROOT / "strategies" / "big_tech_software_tilt_hold.v1.yaml").read_text()
EXAMPLE = (ROOT / "prompts" / "examples" / "portfolio.yaml").read_text()


def strat(risk, text=USER):
    v = validate(text, risk)
    assert v.ok, v.errors
    return v.strategy


# ---------------------------------------------------------------- validator
def test_portfolio_files_validate(risk):
    s = strat(risk)
    assert s.kind == "portfolio" and len(s.symbols) == 10
    assert abs(sum(s.weights.values()) - 1) < 1e-9
    assert s.weights["MSFT"] == pytest.approx(0.14) and s.weights["NVDA"] == pytest.approx(0.06)
    assert [r.action for r in s.rules] == ["hold", "trim", "exit_and_wait", "add"]
    assert strat(risk, EXAMPLE).hold_sessions == 21
    text = back_translate(s)
    assert "sell 30% of the shares held" in text and "150%" in text and "2026-12-10" in text


def test_portfolio_refused_when_disabled_on_risk_page(conn):
    from common import risk_settings as RS
    from common.config import risk_defaults
    assert RS.save(conn, {"portfolio.enabled": False}, "owner", "turn off sleeves", risk_defaults()[0])["ok"]
    v = validate(USER, load_risk(conn=conn))
    assert not v.ok and "portfolio is disabled" in v.errors[0]


@pytest.mark.parametrize("old,new,msg", [
    ("capital: 60%", "capital: 70%", "above risk control portfolio.max_allocation"),
    ("weight: 70%\n      symbols: [MSFT, GOOGL, META, ORCL, NFLX]", "weight: 70%\n      symbols: [MSFT, GOOGL]", "above risk control portfolio.max_position"),
    ("weight: 30%", "weight: 20%", "add up to 90.00%"),
    ('"day_change > trim_gain"', '"day_change > 0.03"', "bare number"),
    ('"day_change > trim_gain"', '"rvol(20) > trim_gain"', "not available in portfolio rules"),
    ('"day_change > trim_gain"', '"close > trim_gain"', "unknown name 'close'"),
    ("{trim: trim_size}", "{trim: 0.3}", "must name a percent param"),
    ("{trim: trim_size}", "sell_some", "unknown action"),
    ("hold_sessions: 42", "hold_sessions: 400", "max_hold_sessions"),
    ('decide_at: "15:45"', 'decide_at: "15:58"', "portfolio.decide_at"),
    ("execution:\n  mode: manual", "execution:\n  mode: manual\nlimits:\n  max_drawdown: 40%", "loosens"),
    ("reentry:\n  when: \"day_change > reenter_gain\"\n  step: reenter_step\n", "", "reentry {when, step} is required"),
    ("kind: portfolio", "kind: swing", "kind must be"),
])
def test_portfolio_validator_rejects(risk, old, new, msg):
    assert old in USER
    v = validate(USER.replace(old, new), risk)
    assert not v.ok and any(msg in e for e in v.errors), v.errors


def test_intraday_files_unchanged(risk, orb_text):
    v = validate(orb_text, risk)
    assert v.ok and v.strategy.kind == "intraday"


# ---------------------------------------------------------------- engine
def quotes(s, change: dict | float, base=100.0):
    return {sym: P.Quote(base * (1 + (change.get(sym, 0) if isinstance(change, dict) else change)), base) for sym in s.symbols}


def fill_all(sl, hold, dp):
    for o in dp.orders:
        P.apply_fill(sl, hold, o.symbol, o.side, o.qty, o.price)


def test_daily_rules_follow_the_request(risk):
    s = strat(risk)
    sl = P.start(s, 100_000, "2026-10-12")
    assert sl.capital_usd == pytest.approx(60_000) and sl.symbols["MSFT"].base_usd == pytest.approx(8_400)
    hold: dict = {}
    dp = P.plan(s, sl, quotes(s, 0.0), hold, "2026-10-12", risk)
    assert {o.symbol: o.qty for o in dp.orders}["MSFT"] == 84 and all(o.side == "buy" for o in dp.orders)
    fill_all(sl, hold, dp)
    assert P.plan(s, sl, quotes(s, 0.0), hold, "2026-10-12", risk).orders == []  # once a day

    # +4% trims 30%, +6% holds, -3.5% adds 25% of the allocation, -6% exits and waits
    dp = P.plan(s, sl, quotes(s, {"MSFT": 0.04, "GOOGL": 0.06, "META": -0.035, "ORCL": -0.06}), hold, "2026-10-13", risk)
    got = {o.symbol: (o.side, o.qty, o.reason) for o in dp.orders}
    assert got == {"MSFT": ("sell", 25, "trim"), "META": ("buy", 21, "add"), "ORCL": ("sell", 84, "exit_and_wait")}
    assert dp.orders[0].side == "sell"  # sells go first
    fill_all(sl, hold, dp)
    assert sl.symbols["ORCL"].mode == "waiting" and sl.realized != 0

    # adds stop at 150% of the allocation
    for k in range(3):
        fill_all(sl, hold, P.plan(s, sl, quotes(s, {"META": -0.04}, base=100), hold, f"2026-10-1{4 + k}", risk))
    assert hold["META"].qty * 96 <= 1.5 * 8_400 + 1e-6

    # waiting: a down day does nothing, the first up day re-enters 20%, then 20% per session
    dp = P.plan(s, sl, quotes(s, {"ORCL": -0.01}), hold, "2026-10-19", risk)
    assert not [o for o in dp.orders if o.symbol == "ORCL"]
    dp = P.plan(s, sl, quotes(s, {"ORCL": 0.01}), hold, "2026-10-20", risk)
    orcl = [o for o in dp.orders if o.symbol == "ORCL"]
    assert orcl[0].reason == "reentry" and orcl[0].qty == 16  # 20% of 8,400 at 101
    fill_all(sl, hold, dp)
    dp = P.plan(s, sl, quotes(s, {"ORCL": -0.02}), hold, "2026-10-21", risk)  # keeps rebuilding on a down day
    assert [o.reason for o in dp.orders if o.symbol == "ORCL"] == ["reentry"]


def test_horizon_and_drawdown_sell_everything(risk):
    s = strat(risk)
    sl, hold = P.start(s, 100_000, "2026-10-12"), {}
    fill_all(sl, hold, P.plan(s, sl, quotes(s, 0.0), hold, "2026-10-12", risk))
    dp = P.plan(s, sl, quotes(s, -0.04, base=80), hold, "2026-10-13", risk)  # every stock -20% from cost
    assert dp.event == "max_drawdown" and sl.status == "stopped"
    assert {o.symbol for o in dp.orders} == set(hold) and all(o.side == "sell" for o in dp.orders)

    sl, hold = P.start(s, 100_000, "2026-10-12"), {}
    fill_all(sl, hold, P.plan(s, sl, quotes(s, 0.0), hold, "2026-10-12", risk))
    dp = P.plan(s, sl, quotes(s, 0.0), hold, "2026-12-10", risk)  # end_date
    assert dp.event == "horizon" and sl.status == "finished"


def test_missing_quote_fails_closed(risk):
    s = strat(risk)
    sl = P.start(s, 100_000, "2026-10-12")
    q = quotes(s, 0.0)
    del q["NFLX"]
    dp = P.plan(s, sl, q, {}, "2026-10-12", risk)
    assert "NFLX" not in {o.symbol for o in dp.orders}
    assert sl.symbols["NFLX"].mode == "building"  # retried next session


def test_order_checks(risk):
    o = P.Order("MSFT", "buy", 100, 200.0, "add")
    book = P.Book(equity=100_000, gross_exposure=50_000, sleeve_exposure=50_000, symbol_value=0, held_qty=0, orders_today=0)
    checks = {c.name: c.ok for c in P.check_order(o, book, risk, adv=1e9)}
    assert not checks["max_position"] and checks["max_allocation"]  # $20k > 15%
    assert not {c.name: c.ok for c in P.check_order(replace(o, qty=10), replace(book, intraday_symbols={"MSFT"}), risk, 1e9)}["not_intraday_symbol"]
    assert not {c.name: c.ok for c in P.check_order(replace(o, qty=10), book, risk, None)}["min_adv"]
    sell = {c.name: c.ok for c in P.check_order(P.Order("MSFT", "sell", 5, 200.0, "trim"), replace(book, held_qty=3), risk, None)}
    assert not sell["sell_within_holding"] and "min_adv" not in sell


# ---------------------------------------------------------------- backtest
def seed_daily(conn, symbols, n=70, start=date(2026, 6, 1)):
    d, rows = start, []
    for k in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        for j, sym in enumerate(symbols):
            c = 100 * (1 + 0.04 * ((k * (j + 3)) % 7 - 3) / 3)  # swings of up to +-4%
            rows.append((sym, d.isoformat(), c, c, c, c, 5_000_000))
        d += timedelta(days=1)
    conn.executemany("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)", rows)


def test_portfolio_backtest(conn, risk):
    from backtest import engine as bt
    s = strat(risk)
    with pytest.raises(ValueError, match="No daily bars"):
        bt.run(conn, s, risk)
    seed_daily(conn, s.symbols)
    a, b = bt.run(conn, s, risk), bt.run(conn, s, risk)
    assert a["metrics"] == b["metrics"] and a["kind"] == "portfolio"
    assert len(a["days"]) == 42 and a["sleeve_end"]["status"] == "finished"
    assert not a["holdings_end"]  # everything sold at the horizon
    assert a["metrics"]["total_pnl"] == pytest.approx(a["equity_curve"][-1]["equity"] - 100_000, abs=1.0)  # per-sale rounding
    reasons = {o["reason"] for o in a["orders"]}
    assert {"initial", "horizon"} <= reasons and reasons & {"trim", "add", "exit_and_wait"}
    assert all(p["equity"] == pytest.approx(p["cash"] + p["stock"], abs=0.02) for p in a["equity_curve"])
    first, last = a["orders"][0], a["orders"][-1]
    assert first["side"] == "buy" and first["deployed_pct"] > 0 and first["cash_after"] < 100_000
    assert last["allocated_pct"] == pytest.approx(0) and a["equity_curve"][-1]["stock"] == 0  # sold out at the horizon
    assert bt.simulate(conn, [s], risk, days=[])["trades"] == []  # the intraday replay ignores portfolio files


# ---------------------------------------------------------------- paper desk
class FakeAlpaca:
    feed = "iex"

    def __init__(self, prices, prior):
        self.prices, self.prior, self.sent, self.n = prices, prior, [], 0

    def snapshots(self, symbols):
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
        return {s: {"latestTrade": {"t": now, "p": self.prices[s]}, "prevDailyBar": {"t": "2026-10-09T04:00:00Z", "c": self.prior[s]}}
                for s in symbols}

    def market(self, symbol, qty, side, cid):
        self.n += 1
        o = {"id": f"o{self.n}", "client_order_id": cid, "symbol": symbol, "side": side, "qty": str(qty), "status": "filled",
             "filled_qty": str(qty), "filled_avg_price": str(self.prices[symbol]), "created_at": "2026-10-12T19:46:00Z",
             "filled_at": "2026-10-12T19:46:01Z"}
        self.sent.append(o)
        return o

    def order(self, oid):
        return next(o for o in self.sent if o["id"] == oid)

    def by_client_id(self, cid):
        return next((o for o in self.sent if o["client_order_id"] == cid), None)


def test_desk_runs_a_sleeve_on_paper(conn, risk, monkeypatch):
    from services.broker.portfolio_desk import PortfolioDesk
    s = strat(risk)
    seed_daily(conn, s.symbols, n=30, start=date(2026, 8, 31))
    registry.sync(conn, risk)
    registry.set_status(conn, s.hash, "paper", "pytest")
    from common import monitoring
    monitoring.activate(conn, s, "pytest")  # what promoting to paper does
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    from common import funds
    funds.ensure(conn, risk, s, "pytest", 0.9)
    day = date(2026, 10, 12)
    close = T.close_ts(day)
    monkeypatch.setattr(time, "time", lambda: T.ts_at(day, s.decide_at) + 60)
    api = FakeAlpaca({sym: 100.0 for sym in s.symbols}, {sym: 100.0 for sym in s.symbols})
    desk = PortfolioDesk(conn, api, risk)
    assert "big_tech_software_tilt_hold" in desk.reload()
    clock = {"timestamp": "2026-10-12T19:46:00Z", "is_open": True}
    acct = {"equity": "100000", "buying_power": "200000"}

    desk.tick(clock, acct, [], close, kill=False, paused=False, intraday_symbols=set())
    pending = db.rows(conn, "SELECT * FROM portfolio_orders ORDER BY id")
    assert len(pending) == 10 and {r["status"] for r in pending} == {"pending"}  # manual mode waits for approval
    assert not api.sent
    conn.execute("UPDATE portfolio_orders SET status='approved'")
    desk.tick(clock, acct, [], close, kill=False, paused=False, intraday_symbols=set())
    assert len(api.sent) == 10
    assert {r["status"] for r in db.rows(conn, "SELECT status FROM portfolio_orders")} == {"filled"}
    hold, _, _ = sleeves.holdings(conn, s.hash)
    assert hold["MSFT"].qty == 84
    assert set(sleeves.owned_symbols(conn)) == set(s.symbols)
    st = sleeves.load_state(conn, s.hash)
    assert st.sessions == 1 and st.last_day == "2026-10-12"

    # Alpaca agrees with the sleeve; a missing position is a mismatch that blocks decisions
    positions = [{"symbol": sym, "qty": str(h.qty), "market_value": str(h.qty * 100), "current_price": "100"} for sym, h in hold.items()]
    assert desk.reconcile(positions) == ""
    assert "MSFT: Alpaca 0 vs sleeves 84" in desk.reconcile([p for p in positions if p["symbol"] != "MSFT"])

    # kill switch: sleeve sold through the desk, so the books stay in sync
    desk.halt("kill switch")
    desk.liquidate("kill")
    hold, _, _ = sleeves.holdings(conn, s.hash)
    assert all(h.qty == 0 for h in hold.values())
    assert sleeves.load_state(conn, s.hash).status == "halted"
    assert db.row(conn, "SELECT COUNT(*) n FROM trades WHERE strategy_hash=?", (s.hash,))["n"] == 10


def test_runnable_splits_kinds(conn, risk):
    registry.sync(conn, risk)
    for r in db.rows(conn, "SELECT hash FROM strategies"):
        registry.set_status(conn, r["hash"], "paper", "pytest")
    intraday = registry.runnable(conn, risk)
    portfolio = registry.runnable(conn, risk, kind="portfolio")
    assert intraday and all(s.kind == "intraday" for _, s in intraday)
    assert [(s.id, s.version) for _, s in portfolio] == [("big_tech_software_tilt_hold", 1), ("big_tech_software_tilt_hold", 2)]


# ---------------------------------------------------------------- api and paper broker views
def test_api_portfolio_strategy_and_sleeve_approval(conn, risk):
    from tests.test_api import login
    s = strat(risk)
    c, csrf, _ = login(conn)
    rows = c.get("/api/strategies").json()["rows"]
    assert next(r for r in rows if r["hash"] == s.hash)["kind"] == "portfolio"
    d = c.get(f"/api/strategies/{s.hash}").json()
    assert d["kind"] == "portfolio" and d["decide_at"] == "15:45" and d["validation"]["ok"]
    assert "a long-only portfolio held overnight" in d["back_translation"] and d["portfolio"]["state"] is None
    r = c.post(f"/api/strategies/{s.hash}/backtest", headers={"x-csrf-token": csrf})
    assert r.status_code == 400 and "No daily bars" in r.json()["detail"]
    seed_daily(conn, s.symbols)
    r = c.post(f"/api/strategies/{s.hash}/backtest", headers={"x-csrf-token": csrf})
    assert r.status_code == 200 and "plain buy-and-hold" in r.json()["message"]
    assert db.row(conn, "SELECT status FROM strategies WHERE hash=?", (s.hash,))["status"] == "backtested"

    oid = conn.insert("INSERT INTO portfolio_orders(strategy_hash,sid,day,symbol,side,qty,ref_price,reason,decision,guard,status,expires_wall)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (s.hash, s.id, "2026-10-12", "MSFT", "buy", 10, 500, "initial", "[]", "[]",
                                                            "pending", time.time() + 600))
    assert [o["id"] for o in c.get("/api/approvals").json()["portfolio_pending"]] == [oid]
    assert c.post(f"/api/portfolio-orders/{oid}/approve", json={}).status_code == 403  # CSRF
    assert c.post(f"/api/portfolio-orders/{oid}/approve", json={}, headers={"x-csrf-token": csrf}).status_code == 200
    assert db.row(conn, "SELECT status, decided_by FROM portfolio_orders WHERE id=?", (oid,)) == {"status": "approved", "decided_by": "owner1"}
    assert c.post(f"/api/portfolio-orders/{oid}/reject", json={}, headers={"x-csrf-token": csrf}).status_code == 409


def test_paper_broker_keeps_sleeves_out_of_intraday_limits(conn, monkeypatch):
    from services.broker.paper import PaperBroker
    from tests.test_paper import Fake
    b = PaperBroker(api=Fake(), conn=conn)
    try:
        b.day = date(2026, 10, 12)
        b.account_data = {"equity": "98000", "last_equity": "100000"}
        b.positions = [{"symbol": "MSFT", "qty": "40", "market_value": "20000", "current_price": "500"},
                       {"symbol": "AMD", "qty": "10", "market_value": "1500", "current_price": "150"}]
        b.open_orders = []
        b.sleeve_syms, b.sleeve_day_pnl = {"MSFT": "h" * 64}, -1500.0
        st = b.state()
        assert st.open_symbols == {"AMD"}  # the sleeve doesn't use up intraday position slots
        assert st.gross_exposure == 21500  # but does count toward exposure
        assert st.day_pnl == -500  # the sleeve's -1,500 today isn't the intraday strategies' loss
    finally:
        conn.execute("SELECT pg_advisory_unlock(?)", (0x5041504552,))
