from datetime import date, time

from common import timeutil as T
from core import risk_guard as G
from core.engine import Intent
from core.exchange import SimExchange


def intent(**kw):
    d = dict(strategy_hash="h", sid="s", symbol="ABC", side="long", ts=T.ts_at(date(2026, 10, 9), time(10, 0)),
             index=30, entry=50.0, limit=50.05, stop=49.0, target=52.0, qty=100, risk_usd=100.0, risk_pct=0.001,
             decision=[], universe=[], signals={})
    d.update(kw)
    return Intent(**d)


def state(**kw):
    d = dict(equity=100_000, start_equity=100_000, gross_exposure=0, open_symbols=set(), trades_today=0)
    d.update(kw)
    return G.AccountState(**d)


def failed(checks):
    return {c.name for c in checks if not c.ok}


def test_clean_intent_passes(risk):
    assert G.passed(G.check_intent(intent(), state(), risk, {}, adv=50e6))


def test_guard_rejects(risk):
    assert "max_risk" in failed(G.check_intent(intent(qty=1000, risk_usd=1500), state(), risk, {}, 50e6))
    assert "max_position" in failed(G.check_intent(intent(qty=300), state(), risk, {}, 50e6))
    assert "daily_loss_ok" in failed(G.check_intent(intent(), state(equity=97_000), risk, {}, 50e6))
    assert "kill_switch_off" in failed(G.check_intent(intent(), state(kill_switch=True), risk, {}, 50e6))
    assert "min_adv" in failed(G.check_intent(intent(), state(), risk, {}, adv=None))
    assert "side_allowed" in failed(G.check_intent(intent(side="short", stop=51, target=48), state(), risk, {}, 50e6))
    late = intent(ts=T.ts_at(date(2026, 10, 9), time(15, 45)))
    assert "entry_window" in failed(G.check_intent(late, state(), risk, {}, 50e6))
    assert "strategy_trades_per_day" in failed(
        G.check_intent(intent(), state(strategy_trades_today={"s": 3}), risk, {"max_trades_per_day": 3}, 50e6))


def test_drift_check(risk):
    assert G.drift_check(50.0, 50.1, risk).ok
    assert not G.drift_check(50.0, 50.5, risk).ok


def test_exchange_fills_next_bar_and_stop_first():
    ex = SimExchange(cash=100_000, slippage=0)
    ex.submit_entry("ABC", "long", 100, limit=50.05, stop=49.0, target=52.0, index=0)
    assert ex.on_bar("ABC", 0, 0, 50, 50.2, 49.9, 50.1) == []  # same bar as the decision: not worked yet
    f = ex.on_bar("ABC", 1, 60, 50.0, 50.2, 49.9, 50.1)
    assert [x.kind for x in f] == ["entry"] and f[0].price == 50.0
    f = ex.on_bar("ABC", 2, 120, 50.0, 52.5, 48.5, 50.0)  # both legs touched -> stop assumed first
    assert f[0].kind == "stop" and f[0].trade["pnl"] == -100.0
    assert ex.equity() == 100_000 - 100


def test_unfilled_entry_cancels():
    ex = SimExchange(cash=100_000, slippage=0, cancel_after_bars=2)
    ex.submit_entry("ABC", "long", 10, limit=50.0, stop=49.0, target=52.0, index=0)
    for i in (1, 2):
        ex.on_bar("ABC", i, 60 * i, 51, 51.5, 50.5, 51)
    assert ex.orders[0].status == "cancelled"
