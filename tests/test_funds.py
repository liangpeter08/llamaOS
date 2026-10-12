import time

import pytest

from common import db, funds, registry
from common import risk_settings as RS
from common.config import load_risk, risk_defaults
from core import funds as F


def cfg(risk):
    assert risk.funds is not None
    return risk.funds


def acct(**kw):
    f = F.StrategyFund(sid="orb", kind="intraday", allocation=0.10, source="set", max_drawdown=0.25, daily_loss=0.10,
                       base_usd=10_000, deployed=kw.pop("sdeployed", 0.0))
    base = dict(equity=100_000, cash=100_000, deployed=0.0, intraday_deployed=0.0, strategies={"orb": f})
    base.update(kw)
    return F.Account(**base)


# ---------------------------------------------------------------- pure checks
def test_buy_checks_isolate_money(risk):
    c = cfg(risk)
    ok = {x.name: x.ok for x in F.check_buy("orb", "intraday", 9_000, acct(), c)}
    assert all(ok.values())
    assert not {x.name: x.ok for x in F.check_buy("orb", "intraday", 11_000, acct(), c)}["strategy_allocation"]
    assert not {x.name: x.ok for x in F.check_buy("orb", "intraday", 5_000, acct(cash=14_000), c)}["cash_reserve"]
    assert not {x.name: x.ok for x in F.check_buy("orb", "intraday", 5_000, acct(deployed=86_000), c)}["max_deployed"]
    assert not {x.name: x.ok for x in F.check_buy("orb", "intraday", 5_000, acct(intraday_deployed=36_000), c)}["intraday_budget"]
    assert not {x.name: x.ok for x in F.check_buy("other", "intraday", 1, acct(), c)}["strategy_allocated"]
    a = acct()
    a.strategies["orb"].status = "paused"
    assert not {x.name: x.ok for x in F.check_buy("orb", "intraday", 1, a, c)}["strategy_active"]
    assert F.room_usd("orb", "intraday", acct(sdeployed=4_000), c) == pytest.approx(6_000)
    assert F.room_usd("orb", "intraday", a, c) == 0


def test_breaches(risk):
    f = F.StrategyFund("orb", "intraday", 0.1, "set", 0.25, 0.10, base_usd=10_000, peak_pnl=1_000, pnl=-1_600)
    assert "drawdown" in F.breach(f)
    f = F.StrategyFund("orb", "intraday", 0.1, "set", 0.25, 0.10, base_usd=10_000, pnl=-500, pnl_today=-1_100)
    assert "daily loss" in F.breach(f)
    f.kind = "portfolio"
    assert F.breach(f) == ""  # sleeves are bought on down days; only drawdown stops them


def test_validate_allocation(risk):
    c = cfg(risk)
    assert F.validate_allocation("a", 0.3, 0.2, 0.05, {"b": 0.5}, c) == []
    errs = F.validate_allocation("a", 0.5, 0.2, 0.05, {"b": 0.5}, c)
    assert any("add up to 100.0%" in e for e in errs)
    assert any("drawdown" in e for e in F.validate_allocation("a", 0.1, 0.5, None, {}, c))


# ---------------------------------------------------------------- reserved part + shared pool
def two(rare_reserved=0.0, rare_deployed=0.0, busy_deployed=0.0, equity=100_000):
    """'busy' fully reserves 50%; 'rare' has a 60% ceiling with `rare_reserved` of it reserved."""
    busy = F.StrategyFund("busy", "intraday", 0.50, "set", 0.25, 0.10, base_usd=50_000, deployed=busy_deployed)
    rare = F.StrategyFund("rare", "intraday", 0.60, "set", 0.25, 0.10, base_usd=60_000, deployed=rare_deployed, reserved=rare_reserved)
    dep = busy_deployed + rare_deployed
    return F.Account(equity=equity, cash=equity - dep, deployed=dep, intraday_deployed=dep, strategies={"busy": busy, "rare": rare})


def test_seldom_trader_borrows_from_the_pool_but_not_from_reserves(risk):
    c = cfg(risk)
    lim = dict(c.__dict__, intraday_budget=1.0, cash_reserve=0.0, max_deployed=0.9)
    c2 = type(c)(**lim)
    # ceilings add up to 110% of equity, but only 50% is reserved: the rare one may use the unreserved 40%
    a = two()
    assert a.committed() == pytest.approx(50_000)
    assert F.room_usd("rare", "intraday", a, c2) == pytest.approx(40_000)
    assert {x.name: x.ok for x in F.check_buy("rare", "intraday", 40_000, a, c2)}["shared_pool"]
    assert not {x.name: x.ok for x in F.check_buy("rare", "intraday", 41_000, a, c2)}["shared_pool"]
    # even when 'busy' is idle its reserve is held; when it trades it can always spend all of it
    a = two(rare_deployed=40_000)
    assert F.room_usd("busy", "intraday", a, c2) == pytest.approx(50_000)
    assert F.room_usd("rare", "intraday", a, c2) == pytest.approx(0)
    # 'busy' bursting past its reserve can't eat into rare's reserve either
    a = two(rare_reserved=0.25, busy_deployed=50_000)  # rare reserves 15k
    assert F.room_usd("rare", "intraday", a, c2) == pytest.approx(40_000)
    assert F.room_usd("busy", "intraday", a, c2) == pytest.approx(0)  # at its 50% ceiling anyway
    busy = a.strategies["busy"]
    busy.allocation, busy.base_usd = 0.80, 80_000
    assert F.room_usd("busy", "intraday", a, c2) == pytest.approx(25_000)  # 90k - 50k used - 15k rare reserve


def test_validate_reserves(risk):
    c = cfg(risk)
    # a 60% ceiling with half reserved fits next to 50% where a full 60% wouldn't
    assert F.validate_allocation("a", 0.6, 0.2, 0.05, {"b": 0.5}, c) != []
    assert F.validate_allocation("a", 0.6, 0.2, 0.05, {"b": 0.5}, c, reserved=1 / 6) == []
    errs = F.validate_allocation("a", 0.6, 0.2, 0.05, {"b": 0.5}, c, reserved=0.9)
    assert any("unreserved" in e and "reserve at most" in e for e in errs)
    assert any("between 0% and 100%" in e for e in F.validate_allocation("a", 0.1, 0.2, None, {}, c, reserved=1.5))


def test_funds_api_reserved(conn, risk):
    from tests.test_api import login
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    r = c.post(f"/api/funds/{s.id}", json={"allocation": "40%", "reserved": "25%", "max_drawdown": "15%"}, headers=h)
    assert r.status_code == 200, r.text
    assert "25% reserved" in r.json()["message"]
    v = c.get("/api/funds").json()
    row = next(x for x in v["strategies"] if x["sid"] == s.id)
    assert row["reserved"] == pytest.approx(0.25) and row["reserved_usd"] == pytest.approx(10_000)
    assert row["activity"]["active_sessions"] == 0
    assert v["account"]["allocated"] == pytest.approx(0.10) and v["account"]["ceilings"] == pytest.approx(0.40)
    assert funds.unallocated(conn, risk) == pytest.approx(risk.funds.max_deployed - 0.10)
    # omitting reserved keeps it
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "30%"}, headers=h).status_code == 200
    assert db.row(conn, "SELECT reserved FROM strategy_funds WHERE sid=?", (s.id,))["reserved"] == pytest.approx(0.25)
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "30%", "reserved": "150%"}, headers=h).status_code == 400


def test_entry_is_sized_to_the_fund_room(risk, orb_text):
    from core.engine import StrategyRunner
    from dsl.validator import validate
    r = StrategyRunner(validate(orb_text, risk).strategy, risk)
    # max_notional caps qty like max_position does (sizing, not clamping a violation)
    import math
    assert math.floor(1000 / (50 * (1 + risk.max_slippage))) == 19


# ---------------------------------------------------------------- settings in postgres
def test_risk_settings_round_trip(conn):
    base = risk_defaults()[0]
    assert load_risk(conn=conn).funds.cash_reserve == pytest.approx(0.10)
    bad = RS.save(conn, {"funds.cash_reserve": "200%"}, "owner", "x", base)
    assert not bad["ok"] and "between" in bad["errors"][0]
    cross = RS.save(conn, {"funds.cash_reserve": "20%"}, "owner", "more cash", base)  # 20% + 90% deployed > 100%
    assert not cross["ok"] and "100% of equity" in cross["errors"][0]
    assert RS.save(conn, {"funds.cash_reserve": "20%", "funds.max_deployed": "80%", "funds.intraday_budget": "30%"},
                   "owner", "more cash", base)["ok"]
    r = load_risk(conn=conn)
    assert r.funds.cash_reserve == pytest.approx(0.2) and r.funds.max_deployed == pytest.approx(0.8)
    assert db.row(conn, "SELECT COUNT(*) n FROM risk_settings_history")["n"] == 3
    assert db.kv_get(conn, "risk.rev") == 1
    assert RS.save(conn, {"account.max_open_positions": "5", "market.flatten_by": "15:50"}, "owner", "x", base)["ok"]
    assert load_risk(conn=conn).max_open_positions == 5
    assert not RS.save(conn, {"market.flatten_by": "15:00"}, "owner", "x", base)["ok"]  # before entries end
    assert RS.save(conn, {"account.max_open_positions": "default"}, "owner", "back", base)["ok"]
    assert load_risk(conn=conn).max_open_positions == 3
    assert load_risk(conn=conn).sha256 != r.sha256


# ---------------------------------------------------------------- db-backed accounting and monitor
def running_orb(conn, risk):
    registry.sync(conn, risk)
    h = db.row(conn, "SELECT hash FROM strategies WHERE sid='orb_news_long'")["hash"]
    registry.set_status(conn, h, "paper", "pytest")
    return registry.load(conn, h, risk)[0]


def test_account_and_monitor(conn, risk):
    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 90_000})
    funds.ensure(conn, risk, s, "owner", 0.10)
    conn.execute("INSERT INTO positions(symbol, qty, side, avg_price, last_price, sid) VALUES ('AAPL', 50, 'long', 200, 190, ?)", (s.id,))
    conn.execute("INSERT INTO intents(sid, symbol, side, qty, limit_price, status) VALUES (?, 'MSFT', 'long', 10, 100, 'submitted')", (s.id,))
    a = funds.account(conn, risk)
    f = a.strategies[s.id]
    assert f.deployed == pytest.approx(50 * 190 + 1_000) and f.pnl == pytest.approx(-500)
    assert a.working_buys == pytest.approx(1_000)
    # a realized loss today beyond 10% of the $10k allocation pauses entries for the day
    conn.execute("INSERT INTO trades(sid, symbol, pnl, exit_ts) VALUES (?, 'NVDA', -700, ?)", (s.id, time.time()))
    assert funds.monitor(conn, risk) == ""
    assert funds.status(conn, s.id)[0] == "paused_today"
    # account drawdown beyond the limit returns a kill reason
    db.kv_set(conn, "funds.peak_equity", 130_000)
    assert "account drawdown" in funds.monitor(conn, risk)
    funds.resume(conn, s.id, "owner")
    assert funds.status(conn, s.id)[0] == "active"


# ---------------------------------------------------------------- api
def test_risk_and_funds_api(conn, risk):
    from tests.test_api import login
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    d = c.get("/api/risk").json()
    keys = {x["key"] for x in d["controls"]}
    assert {"funds.cash_reserve", "portfolio.max_allocation", "account.max_daily_loss"} <= keys
    assert c.post("/api/risk", json={"changes": {"funds.cash_reserve": "15%"}, "reason": "x"}, headers=h).status_code == 422
    r = c.post("/api/risk", json={"changes": {"funds.cash_reserve": "10%", "funds.default_allocation": "4%"}, "reason": "tune"}, headers=h)
    assert r.status_code == 200, r.text
    assert c.post("/api/risk", json={"changes": {"funds.cash_reserve": "15%"}, "reason": "nope"}).status_code == 403
    ctl = {x["key"]: x for x in c.get("/api/risk").json()["controls"]}
    assert ctl["funds.default_allocation"]["overridden"] and ctl["funds.default_allocation"]["updated_by"] == "owner1"

    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    v = c.get("/api/funds").json()
    row = next(x for x in v["strategies"] if x["sid"] == s.id)
    assert row["allocation"] == pytest.approx(0.04) and v["account"]["reserve_usd"] == pytest.approx(10_000)
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "95%", "max_drawdown": "20%"}, headers=h).status_code == 400
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "20%", "max_drawdown": "20%", "daily_loss": "5%"}, headers=h).status_code == 200
    assert c.post(f"/api/funds/{s.id}/pause", json={"reason": "review"}, headers=h).status_code == 200
    assert funds.status(conn, s.id) == ("paused", "review")
    assert c.post(f"/api/funds/{s.id}/resume", json={}, headers=h).status_code == 200


# ---------------------------------------------------------------- fixed-dollar allocations
def test_fixed_dollar_allocation(conn, risk):
    from common.units import parse_usd
    assert parse_usd("$25,000") == 25_000 and parse_usd("25k") == 25_000 and parse_usd("1.5m") == 1_500_000
    with pytest.raises(ValueError):
        parse_usd("ten bucks")
    assert F.StrategyFund("orb", "intraday", 0.5, "set", 0.2, None, fixed_usd=8_000).cap_usd(100_000) == 8_000

    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    assert funds.set_allocation(conn, risk, s, None, 0.2, 0.05, "owner", alloc_usd=15_000) == []
    f = funds.account(conn, risk).strategies[s.id]
    assert f.cap_usd(100_000) == 15_000 and f.allocation == pytest.approx(0.15) and f.base_usd == 15_000
    # the dollar cap holds as equity moves; its share of equity moves instead
    db.kv_set(conn, "account", {"equity": 150_000, "cash": 150_000})
    f = funds.account(conn, risk).strategies[s.id]
    assert f.cap_usd(150_000) == 15_000 and f.allocation == pytest.approx(0.10)
    assert funds.unallocated(conn, risk) == pytest.approx(risk.funds.max_deployed - 0.10)
    assert F.room_usd(s.id, "intraday", funds.account(conn, risk), risk.funds) == pytest.approx(15_000)
    # checked against the limits at today's equity
    errs = funds.set_allocation(conn, risk, s, None, 0.2, 0.05, "owner", alloc_usd=10_000_000)
    assert errs and "of equity" in errs[0]
    funds.pause(conn, s.id, "x", "owner")
    funds.resume(conn, s.id, "owner")
    assert db.row(conn, "SELECT base_usd FROM strategy_funds WHERE sid=?", (s.id,))["base_usd"] == 15_000


def test_funds_api_fixed_dollars_keeps_stops(conn, risk):
    from tests.test_api import login
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    s = running_orb(conn, risk)
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "10%", "max_drawdown": "15%", "daily_loss": "4%"}, headers=h).status_code == 200
    r = c.post(f"/api/funds/{s.id}", json={"allocation": "$12,500", "alloc_mode": "usd"}, headers=h)
    assert r.status_code == 200, r.text
    row = next(x for x in c.get("/api/funds").json()["strategies"] if x["sid"] == s.id)
    assert row["alloc_mode"] == "usd" and row["allocation_usd"] == 12_500
    assert row["max_drawdown"] == pytest.approx(0.15) and row["daily_loss"] == pytest.approx(0.04)
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "lots", "alloc_mode": "usd"}, headers=h).status_code == 400
    assert c.post(f"/api/funds/{s.id}", json={"allocation": "8"}, headers=h).status_code == 200  # bare number = percent
    row = next(x for x in c.get("/api/funds").json()["strategies"] if x["sid"] == s.id)
    assert row["alloc_mode"] == "pct" and row["allocation"] == pytest.approx(0.08)


def test_promote_with_allocation(conn, risk):
    from tests.test_api import login
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    registry.sync(conn, risk)
    hh = db.row(conn, "SELECT hash FROM strategies WHERE sid='orb_news_long'")["hash"]
    registry.set_status(conn, hh, "backtested", "pytest")
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    sug = c.get(f"/api/strategies/{hh}").json()["fund_suggest"]
    assert sug["room"] == pytest.approx(risk.funds.max_deployed) and sug["current"] is None
    r = c.post(f"/api/strategies/{hh}/promote", json={"to": "paper", "allocation": "20k", "alloc_mode": "usd"}, headers=h)
    assert r.status_code == 200, r.text
    assert "$20,000 fixed" in r.json()["message"]
    fr = db.row(conn, "SELECT alloc_usd FROM strategy_funds WHERE sid='orb_news_long'")
    assert fr["alloc_usd"] == 20_000


def test_draft_promotes_to_paper_without_backtest(conn, risk):
    from tests.test_api import login
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    registry.sync(conn, risk)
    hh = db.row(conn, "SELECT hash, status FROM strategies WHERE sid='orb_news_long'")
    assert hh["status"] == "draft"
    db.kv_set(conn, "account", {"equity": 100_000, "cash": 100_000})
    assert c.get(f"/api/strategies/{hh['hash']}").json()["fund_suggest"] is not None
    r = c.post(f"/api/strategies/{hh['hash']}/promote", json={"to": "paper", "allocation": "10%"}, headers=h)
    assert r.status_code == 200, r.text
    assert "without a backtest" in r.json()["message"]
    assert db.row(conn, "SELECT status FROM strategies WHERE hash=?", (hh["hash"],))["status"] == "paper"
    assert db.row(conn, "SELECT 1 x FROM audit WHERE kind='strategy.untested_promotion'")
