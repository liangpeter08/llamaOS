"""DSL 0.2 portfolio overlays: validator, engine (the design doc's decision table), backtest, desk, api, monitor."""
import json
import time
from datetime import date

import pytest

from common import db, sleeves
from core import portfolio as P
from core import portfolio_v2 as P2
from dsl.validator import validate
from tests.test_portfolio import ROOT, seed_daily

V2 = (ROOT / "strategies" / "big_tech_software_tilt_hold.v2.yaml").read_text()
ENFORCE = V2.replace("  enabled: true\n  mode: shadow", "  enabled: true\n  mode: enforce")
NQ, HW = "nasdaq_session_return", "hardware_session_return"
SOFT = ["MSFT", "GOOGL", "META", "ORCL", "NFLX"]
OTHER = ["NVDA", "AAPL", "AMZN", "AVGO", "TSLA"]
MACRO = "nasdaq_two_percent_trim_and_rebuild"


def strat(risk, text=V2):
    v = validate(text, risk)
    assert v.ok, v.errors
    return v.strategy


def q(s, ch=None, base=100.0):
    ch = ch or {}
    return {sym: P.Quote(base * (1 + ch.get(sym, 0)), base) for sym in s.symbols}


def fill(sl, hold, dp, locked=None):
    for o in dp.orders:
        P.apply_fill(sl, hold, o.symbol, o.side, o.qty, o.price)
        if locked is not None and o.side == "sell":
            locked[0] += min(o.reserve_qty, o.qty) * o.price


def funded(s, risk):
    """A sleeve bought on 2026-10-12 at $100: 63 shares of each software name, 27 of each other name."""
    sl, hold = P.start(s, 100_000, "2026-10-12"), {}
    dp = P.plan(s, sl, q(s), hold, "2026-10-12", risk, inputs={NQ: 0.0, HW: 0.05})
    assert [e["kind"] for e in dp.events] == ["rotation_skipped"]  # no rotation on the funding session
    fill(sl, hold, dp)
    assert hold["MSFT"].qty == 63 and hold["NVDA"].qty == 27 and sl.funded and sl.sessions == 1
    return sl, hold


def sells(dp):
    return {o.symbol: (o.qty, o.reason) for o in dp.orders if o.side == "sell"}


# ---------------------------------------------------------------- validator
def test_v2_file_validates(risk):
    s = strat(risk)
    assert s.dsl == "0.2" and s.input_symbols == ["AMD"] and s.v2.mode == "shadow" and s.v2.review_required
    assert [(o.id, o.kind, o.on) for o in s.overlays] == [(MACRO, "macro", "every_fresh_observation"),
                                                          ("hardware_rally_software_to_cash", "rotation", "daily_decision")]
    assert s.v2.index_instruments == ["NASDAQ_COMPOSITE"] and s.v2.monitored()[0].id == "macro_context"
    assert any("group_definitions:" in a for a in s.assumptions)
    # the design proposal itself differs only in its version string and catalog name
    doc = (ROOT / "docs" / "design" / "portfolio-monitoring.example.yaml").read_text()
    assert validate(doc, risk).errors == ["unsupported dsl version '0.2-proposal' (expected 0.1 or 0.2)"] or not validate(doc, risk).ok


@pytest.mark.parametrize("old,new,msg", [
    ('dsl: "0.2"', "dsl: 0.1", "unknown top-level key 'market_inputs'"),
    ("precedence: [account_safety, horizon_exit", "precedence: [horizon_exit, account_safety", "the order the engine applies"),
    ("scenario_ref: portfolio_macro_session_decline", "scenario_ref: made_up", "is not in config/scenario-catalog.yaml"),
    ("retrigger: verified_minus_2_percent_on_new_session", "retrigger: verified_minus_3_percent_on_new_session", "must agree with the params"),
    ("[MSFT, GOOGL, META, ORCL, NFLX, NVDA, AAPL, AMZN, AVGO, TSLA, AMD]", "[MSFT, GOOGL, META, ORCL, NFLX, NVDA, AAPL, AMZN, AVGO, TSLA]",
     "required_symbols must list every held and market-input ticker"),
    ("silent_proxy_fallback: prohibited", "silent_proxy_fallback: allowed", "only 'prohibited' is supported"),
    ("missing_member: unknown_do_not_reweight", "missing_member: reweight", "only 'unknown_do_not_reweight'"),
    ("on_contradicted: [log, notify_dashboard, request_t2]", "on_contradicted: [log, block_new_entries]", "needs critical: true"),
    ("bindings: {metric: hardware_session_return, target_group: software}", "bindings: {metric: nasdaq_session_return, target_group: software}",
     "must be a symbols basket"),
    ("macro_decline_return: {value: \"-2%\", range: [\"-10%\", \"-0.5%\"]}", "macro_decline_return: {value: \"2%\", range: [\"0.5%\", \"10%\"]}",
     "must be a negative return"),
    ("instrument: NASDAQ_COMPOSITE", "instrument: QQQ", "instrument must be one of"),
    ("action: trim_group_to_cash", "action: liquidate_sleeve", "response.action must be one of ['trim_group_to_cash']"),
    ("shadow_mode: audit_only_no_orders_or_buy_pauses", "shadow_mode: trade", "only 'audit_only_no_orders_or_buy_pauses'"),
    ("        includes_other_strategies: false\n", "        includes_other_strategies: false\n        sells_everything: true\n", "unknown key"),
])
def test_v2_validator_rejects(risk, old, new, msg):
    assert old in V2, old
    v = validate(V2.replace(old, new), risk)
    assert not v.ok and any(msg in e for e in v.errors), v.errors


def test_v1_files_unchanged(risk):
    v1 = validate((ROOT / "strategies" / "big_tech_software_tilt_hold.v1.yaml").read_text(), risk)
    assert v1.ok and v1.strategy.dsl == "0.1" and v1.strategy.v2 is None and v1.strategy.input_symbols == []


# ---------------------------------------------------------------- engine: the design doc's decision table
@pytest.mark.parametrize("nq,hw,moves,want", [
    # Nasdaq -2%, hardware +5%: everything trims 40% (software: 40% > 30%), buys paused
    (-0.02, 0.05, {}, {**{x: (25, "macro_trim") for x in SOFT}, **{x: (10, "macro_trim") for x in OTHER}}),
    # Nasdaq -1%, hardware +5%, software +6%: rotation overrides that name's hold
    (-0.01, 0.05, {"MSFT": 0.06}, {x: (18, "rotation_trim") for x in SOFT}),
    # hardware +2.5%, software +4%: daily 30% beats rotation 15%
    (-0.01, 0.025, {"MSFT": 0.04}, {"MSFT": (18, "trim"), **{x: (9, "rotation_trim") for x in SOFT[1:]}}),
    # hardware +10%, software +4%: rotation 60% beats daily 30%
    (-0.01, 0.10, {"MSFT": 0.04}, {x: (37, "rotation_trim") for x in SOFT}),
    # Nasdaq -2%, software -6%: full exit of that name, 40% of the rest
    (-0.02, 0.0, {"ORCL": -0.06}, {**{x: (25, "macro_trim") for x in SOFT if x != "ORCL"}, "ORCL": (63, "exit_and_wait"),
                                   **{x: (10, "macro_trim") for x in OTHER}}),
    # hardware +5%, software -4%: sell 30% and no add
    (0.0, 0.05, {"META": -0.04}, {x: (18, "rotation_trim") for x in SOFT}),
    # hardware data missing, Nasdaq -2% verified: the macro trim still happens
    (-0.02, None, {}, {**{x: (25, "macro_trim") for x in SOFT}, **{x: (10, "macro_trim") for x in OTHER}}),
    # boundaries: +5% is a trim (hold needs > +5%), +3% does nothing, -5% and -3% add
    (0.0, -0.01, {"MSFT": 0.05, "GOOGL": 0.03}, {"MSFT": (18, "trim")}),
])
def test_same_snapshot_resolution(risk, nq, hw, moves, want):
    s = strat(risk)
    sl, hold = funded(s, risk)
    dp = P.plan(s, sl, q(s, moves), hold, "2026-10-13", risk, inputs={NQ: nq, HW: hw})
    assert sells(dp) == want
    if nq <= -0.02 or (hw or 0) > 0:
        assert not [o for o in dp.orders if o.side == "buy" and (nq <= -0.02 or o.symbol in SOFT)]
    if hw and hw > 0:
        rot = {o.symbol: o.reserve_qty for o in dp.orders if o.reason == "rotation_trim"}
        assert all(v == math_floor(63 * min(hw / 0.05 * 0.3, 1.0)) for v in rot.values())


def math_floor(x):
    import math
    return math.floor(x + 1e-9)


def test_boundary_adds_and_exact_thresholds(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    dp = P.plan(s, sl, q(s, {"MSFT": -0.05, "GOOGL": -0.03}), hold, "2026-10-13", risk, inputs={NQ: -0.019, HW: -0.01})
    assert {o.symbol: o.reason for o in dp.orders} == {"MSFT": "add", "GOOGL": "add"}
    sl, hold = funded(s, risk)
    dp = P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: -0.02, HW: 0.0})  # exactly -2% triggers
    assert dp.events[0]["kind"] == "macro_trigger" and sl.overlays[MACRO]["phase"] == "reduction_pending"


def test_macro_wait_and_rebuild_to_cap(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    fill(sl, hold, P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: -0.03, HW: 0.0}))
    assert hold["MSFT"].qty == 38 and hold["NVDA"].qty == 17
    # same session again: no second trim; a 0% session doesn't start recovery; adds stay paused
    dp = P.plan(s, sl, q(s, {"MSFT": -0.04}), hold, "2026-10-14", risk, inputs={NQ: 0.0, HW: 0.0})
    assert not dp.orders and sl.overlays[MACRO]["phase"] == "awaiting_positive_session"
    assert any("waiting for a later positive session" in (r.get("note") or "") for r in dp.records)
    # first positive session: 25% of the original allocation per symbol, then every session, to 150%
    targets = []
    for k, (d, nq) in enumerate([("2026-10-15", 0.002), ("2026-10-16", -0.004), ("2026-10-19", 0.001), ("2026-10-20", 0.0),
                                 ("2026-10-21", 0.0), ("2026-10-22", 0.0)]):
        dp = P.plan(s, sl, q(s), hold, d, risk, inputs={NQ: nq, HW: -0.01})
        fill(sl, hold, dp)
        targets.append(hold["MSFT"].qty)
    assert targets[:4] == [53, 68, 83, 94]  # +15 shares ($1,575) per session, last step capped at 150% (94.5 shares)
    assert sl.overlays[MACRO]["phase"] == "normal"
    assert hold["NVDA"].qty == 40  # 150% of $2,700


def test_retrigger_restarts_wait_during_rebuild(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    fill(sl, hold, P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: -0.03, HW: 0.0}))
    fill(sl, hold, P.plan(s, sl, q(s), hold, "2026-10-14", risk, inputs={NQ: 0.01, HW: 0.0}))
    assert sl.overlays[MACRO]["phase"] == "rebuilding" and hold["MSFT"].qty == 53
    dp = P.plan(s, sl, q(s), hold, "2026-10-15", risk, inputs={NQ: -0.025, HW: 0.0})
    assert sells(dp)["MSFT"] == (21, "macro_trim") and sl.overlays[MACRO]["phase"] == "reduction_pending"  # until the sells fill


def test_two_rotation_days_compound(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    for d in ("2026-10-13", "2026-10-14"):
        fill(sl, hold, P.plan(s, sl, q(s), hold, d, risk, inputs={NQ: 0.0, HW: 0.05}))
    assert hold["MSFT"].qty == 32  # 63 -> 45 -> 32: 30% of current shares each day (49% of the original before whole-share rounding)


def test_intraday_observation_then_decision(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    prices = {sym: 100.0 for sym in s.symbols}
    dp = P2.observe(s, sl, {NQ: -0.021}, hold, prices, "2026-10-13")
    assert sells(dp)["MSFT"] == (25, "macro_trim") and dp.events[0]["snapshot"] == "observation"
    assert not P2.observe(s, sl, {NQ: -0.03}, hold, prices, "2026-10-13").orders  # once per session
    fill(sl, hold, dp)
    # 15:45: the morning trim and the hardware trim are separate snapshots: 63 -> 38 -> 27 (30% of 38)
    dp = P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: -0.022, HW: 0.05})
    assert sells(dp)["MSFT"] == (11, "rotation_trim") and "macro_repeat" in [e["kind"] for e in dp.events]


def test_retained_reduction_until_reconciled(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    dp = P2.observe(s, sl, {NQ: -0.03}, hold, {x: 100.0 for x in s.symbols}, "2026-10-13")
    # nothing filled (the sell was rejected): the decision re-issues only the remainder to the target
    dp = P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: -0.03, HW: 0.0}, working={"MSFT": 10})
    assert sells(dp)["MSFT"] == (15, "macro_trim") and sl.overlays[MACRO]["phase"] == "reduction_pending"


def test_macro_before_initial_buy_blocks_funding(risk):
    s = strat(risk)
    sl, hold = P.start(s, 100_000, "2026-10-12"), {}
    dp = P.plan(s, sl, q(s), hold, "2026-10-12", risk, inputs={NQ: -0.025, HW: 0.0})
    assert not dp.orders and not sl.funded and sl.sessions == 0
    dp = P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: 0.01, HW: 0.0})
    fill(sl, hold, dp)
    assert {o.reason for o in dp.orders} == {"initial"} and hold["MSFT"].qty == 63  # 100%, not the 150% rebuild target
    assert sl.funded_day == "2026-10-13" and sl.sessions == 1 and sl.overlays[MACRO]["phase"] == "normal"


def test_locked_rotation_cash_limits_buys(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    locked = [0.0]
    fill(sl, hold, P.plan(s, sl, q(s), hold, "2026-10-13", risk, inputs={NQ: 0.0, HW: 0.05}), locked)
    assert locked[0] == pytest.approx(5 * 18 * 100)
    budget = P2.spendable(s, sl, hold, locked[0])
    dp = P.plan(s, sl, q(s, {x: -0.04 for x in s.symbols}), hold, "2026-10-14", risk, inputs={NQ: 0.0, HW: -0.01},
                locked_usd=locked[0] + 60_000)
    assert not [o for o in dp.orders if o.side == "buy"] and any("no unreserved sleeve cash" in (r.get("note") or "") for r in dp.records)
    sl.released_usd = locked[0]
    assert P2.spendable(s, sl, hold, locked[0]) == pytest.approx(budget + locked[0])


def test_shadow_mode_logs_without_trading(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    dp = P.plan(s, sl, q(s, {"META": -0.04}), hold, "2026-10-13", risk, inputs={NQ: -0.03, HW: 0.05}, enforce=False)
    assert {o.symbol: (o.side, o.reason) for o in dp.orders} == {"META": ("buy", "add")}  # the daily rule alone
    would = {x["symbol"]: x for x in dp.shadow}
    assert would["MSFT"]["would"] == "sell 25" and would["META"]["actual"] == "buy 16"
    assert sl.overlays[MACRO]["phase"] == "awaiting_positive_session"  # the state machine still advances for the log
    assert not P2.observe(s, sl, {NQ: -0.03}, hold, {x: 100.0 for x in s.symbols}, "2026-10-14", enforce=False).orders


def test_horizon_counts_from_funding_and_liquidates(risk):
    s = strat(risk)
    sl, hold = funded(s, risk)
    sl.sessions = 41
    dp = P.plan(s, sl, q(s), hold, "2026-12-09", risk, inputs={NQ: -0.05, HW: 0.2})
    assert dp.event == "horizon" and sells(dp)["MSFT"] == (63, "horizon") and sl.status == "finished"


# ---------------------------------------------------------------- backtest
def seed_index(conn, days, moves):
    c = 10_000.0
    for k, d in enumerate(days):
        c *= 1 + moves[k % len(moves)]
        conn.execute("INSERT INTO index_daily(instrument, day, c, source) VALUES (?,?,?,?)", ("NASDAQ_COMPOSITE", d, c, "test"))


def test_backtest_replays_inputs_and_overlays(conn, risk):
    from backtest import engine as bt
    s = strat(risk)
    seed_daily(conn, [*s.symbols, "AMD"], n=70)
    with pytest.raises(ValueError, match="No daily history stored for the index NASDAQ_COMPOSITE"):
        bt.run(conn, s, risk)
    days = [r["day"] for r in db.rows(conn, "SELECT DISTINCT day FROM daily ORDER BY day")]
    seed_index(conn, days, [0.004, -0.025, 0.006, 0.003, 0.0, 0.01])
    a = bt.run(conn, s, risk)
    assert a == bt.run(conn, s, risk) or a["metrics"] == bt.run(conn, s, risk)["metrics"]
    kinds = {e["kind"] for e in a["overlay_events"]}
    assert {"macro_trigger", "macro_recovery"} <= kinds and "rotation" in kinds
    reasons = {o["reason"] for o in a["orders"]}
    assert {"initial", "macro_trim", "rotation_trim", "macro_rebuild", "horizon"} <= reasons
    assert a["locked_cash"] > 0 and a["input_symbols"] == ["AMD"] and "Overlays simulated as enforced" in a["signal_note"]


# ---------------------------------------------------------------- paper desk
class FakeFeed:
    def __init__(self, value):
        self.value = value

    def quote(self, inst):
        return (self.value, time.time() - 5) if self.value else None

    def history(self, inst, start, before):
        return []


def sleeve_on_paper(conn, risk, text):
    from services.broker.portfolio_desk import PortfolioDesk
    from tests.test_portfolio import FakeAlpaca
    s = strat(risk, text)
    day = date(2026, 10, 13)
    seed_daily(conn, [*s.symbols, "AMD"], n=30, start=date(2026, 8, 31))
    conn.execute("INSERT INTO index_daily(instrument, day, c, source) VALUES ('NASDAQ_COMPOSITE', '2026-10-12', 10000, 'test')")
    sl, hold = funded(s, risk)
    sleeves.save_state(conn, s.hash, s.id, sl)
    for sym, h in hold.items():
        conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,symbol,side,qty,ref_price,reason,status,filled_qty,avg_price,client_id)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (s.hash, s.id, "2026-10-12", sym, "buy", h.qty, 100, "initial", "filled", h.qty, 100,
                                                          f"seed-{sym}"))
    api = FakeAlpaca({sym: 100.0 for sym in s.symbols}, {sym: 100.0 for sym in s.symbols})
    api.cancelled = []
    api.cancel = lambda oid: api.cancelled.append(oid)
    desk = PortfolioDesk(conn, api, risk, index_feed=FakeFeed(9_750.0))  # -2.5%
    conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,symbol,side,qty,ref_price,reason,status,client_id,broker_id)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (s.hash, s.id, str(day), "META", "buy", 5, 100, "add", "submitted", "w1", "b-1"))
    conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,symbol,side,qty,ref_price,reason,status,client_id)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)", (s.hash, s.id, str(day), "AAPL", "buy", 5, 100, "add", "approved", "w2"))
    return desk, s, day, api


def test_desk_observation_enforced(conn, risk, monkeypatch):
    desk, s, day, api = sleeve_on_paper(conn, risk, ENFORCE)
    db.kv_set(conn, f"overlays.enforce.{s.hash}", True)
    monkeypatch.setattr(time, "time", lambda: 1_792_000_000.0)  # 2026-10-13 during the session
    positions = [{"symbol": sym, "qty": "1", "market_value": "100", "current_price": "100"} for sym in s.symbols]
    desk.observe(s.hash, {"mode": "auto", "status": "paper"}, s, day, {"equity": "100000", "buying_power": "100000"}, positions,
                 time.time() + 3600, set())
    rows = db.rows(conn, "SELECT symbol, side, qty, reason, status, overlay FROM portfolio_orders WHERE reason='macro_trim' ORDER BY symbol")
    assert {r["symbol"]: r["qty"] for r in rows}["MSFT"] == 25 and {r["status"] for r in rows} == {"approved"}
    assert rows[0]["overlay"] == MACRO
    assert db.row(conn, "SELECT status FROM portfolio_orders WHERE client_id='w2'")["status"] == "void"  # unsent buy voided
    assert api.cancelled == ["b-1"]  # working buy cancelled at the broker
    ev = db.rows(conn, "SELECT kind, mode FROM portfolio_events WHERE strategy_hash=?", (s.hash,))
    assert ev == [{"kind": "macro_trigger", "mode": "enforce"}]
    assert desk.buys_paused(s.hash, s)


def test_desk_observation_shadow(conn, risk, monkeypatch):
    desk, s, day, api = sleeve_on_paper(conn, risk, V2)
    monkeypatch.setattr(time, "time", lambda: 1_792_000_000.0)
    positions = [{"symbol": sym, "qty": "1", "market_value": "100", "current_price": "100"} for sym in s.symbols]
    desk.observe(s.hash, {"mode": "auto", "status": "paper"}, s, day, {"equity": "100000"}, positions, time.time() + 3600, set())
    assert not db.rows(conn, "SELECT id FROM portfolio_orders WHERE reason='macro_trim'") and not api.cancelled
    ev = db.rows(conn, "SELECT kind, mode, payload FROM portfolio_events WHERE strategy_hash=? ORDER BY id", (s.hash,))
    assert [(e["kind"], e["mode"]) for e in ev] == [("macro_trigger", "shadow"), ("shadow", "shadow")]
    assert json.loads(ev[1]["payload"])["orders"][0]["would"].startswith("sell")
    assert not desk.buys_paused(s.hash, s)


def test_locked_cash_from_fills(conn, risk):
    s = strat(risk)
    conn.execute("INSERT INTO portfolio_orders(strategy_hash,sid,day,symbol,side,qty,ref_price,reason,status,filled_qty,avg_price,reserve_qty,client_id)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (s.hash, s.id, "2026-10-13", "MSFT", "sell", 25, 100, "macro_trim", "partially_filled",
                                                        20, 101.0, 18, "x1"))
    assert sleeves.locked_cash(conn, s.hash) == pytest.approx(18 * 101.0)  # only the rotation part, only what filled


# ---------------------------------------------------------------- api
def test_api_promotion_gates_and_enforcement(conn, risk, monkeypatch, tmp_path):
    from common import market_inputs as MI, registry
    from tests.test_api import login
    registry.sync(conn, risk)
    s = strat(risk)
    c, csrf, _ = login(conn)
    h = {"x-csrf-token": csrf}
    registry.set_status(conn, s.hash, "backtested", "pytest")
    monkeypatch.setattr(MI, "feeds", lambda: {})
    r = c.post(f"/api/strategies/{s.hash}/promote", json={"to": "paper"}, headers=h)
    assert r.status_code == 400 and "no index feed configured for NASDAQ_COMPOSITE" in r.json()["detail"]
    monkeypatch.setattr(MI, "feeds", lambda: {"NASDAQ_COMPOSITE": {"provider": "yahoo", "symbol": "^IXIC"}})
    r = c.post(f"/api/strategies/{s.hash}/promote", json={"to": "paper"}, headers=h)
    assert r.status_code == 409 and "proposed_requires_review" in r.json()["detail"]
    r = c.post(f"/api/strategies/{s.hash}/promote", json={"to": "paper", "reviewed": True}, headers=h)
    assert r.status_code == 200, r.text
    assert db.kv_get(conn, f"scenario_catalog.pin.{s.hash}") == s.v2.catalog_hash
    assert "AMD" in [x["symbol"] for x in db.rows(conn, "SELECT symbol FROM monitoring_watchlist WHERE enabled")]
    r = c.post(f"/api/strategies/{s.hash}/overlays", json={"enforce": True}, headers=h)
    assert r.status_code == 409 and "monitoring.mode: shadow" in r.json()["detail"]
    view = c.get(f"/api/strategies/{s.hash}").json()["portfolio"]["overlays"]
    assert view["file_mode"] == "shadow" and not view["enforced"] and view["catalog_pinned"] and view["input_symbols"] == ["AMD"]


# ---------------------------------------------------------------- assumption monitor
def test_assumption_monitor(conn, risk, monkeypatch):
    from services.models import assumption_monitor as AM, laya_provider, llm
    text = ENFORCE.replace("    critical: false", "    critical: true").replace(
        "on_contradicted: [log, notify_dashboard, request_t2]", "on_contradicted: [log, notify_dashboard, block_new_entries]")
    s = strat(risk, text)
    a = s.v2.monitored()[0]
    monkeypatch.setattr(laya_provider, "available", lambda: True)
    asked = []

    def fake_ask(conn_, state, typed, task=""):
        asked.append((task, typed))
        if "driver" in typed:
            return {"driver": {"choice": "broad_macro", "probabilities": {"broad_macro": 0.8}}}
        return {"macro_compatibility": {"choice": "adverse", "probabilities": {"adverse": 0.9}}}
    monkeypatch.setattr(laya_provider, "ask", fake_ask)
    db.kv_set(conn, f"overlays.enforce.{s.hash}", True)
    assert AM.evaluate(conn, s, a, "interval", time.time()) == "contradicted"
    row = db.row(conn, "SELECT tier, status, actions, mode FROM assumption_checks")
    assert row["tier"] == "t1" and json.loads(row["actions"]) == ["log", "notify_dashboard", "block_new_entries"]
    assert db.kv_get(conn, f"assumption.block.{s.hash}")["reasons"] == ["assumption macro_context contradicted"]
    # unchanged evidence within result_max_age is answered from the cache
    AM.evaluate(conn, s, a, "interval", time.time())
    assert len([t for t, _ in asked if t == "assumption.t1"]) == 1

    # T1 uncertain on a threshold crossing escalates to T2 (Claude); low confidence counts as unknown
    monkeypatch.setattr(laya_provider, "ask", lambda *x, **k: {"macro_compatibility": {"choice": "adverse", "probabilities": {"adverse": 0.5}}})
    monkeypatch.setattr(llm, "call", lambda conn_, **kw: ("", {"answers": [{"question_id": "macro_compatibility", "answer": "supportive",
                                                                            "confidence": 0.9, "evidence_ids": ["inputs"], "rationale": "r"}]}, None))
    conn.execute("INSERT INTO portfolio_events(strategy_hash,sid,day,ts,kind,overlay,mode,payload) VALUES (?,?,?,?,?,?,?,?)",
                 (s.hash, s.id, "2026-10-13", time.time(), "macro_trigger", MACRO, "enforce", json.dumps({"value": -0.021})))
    assert AM.evaluate(conn, s, a, "macro_threshold_crossing", time.time() + 1) == "supported"
    assert db.row(conn, "SELECT tier FROM assumption_checks ORDER BY id DESC LIMIT 1")["tier"] == "t2"

    # overlay triggers get typed context that never changes the numeric response
    monkeypatch.setattr(laya_provider, "ask", fake_ask)
    AM.overlay_context(conn, s)
    ctx = json.loads(db.row(conn, "SELECT context FROM portfolio_events WHERE kind='macro_trigger'")["context"])
    assert ctx["driver"] == "broad_macro" and "context only" in ctx["note"]
