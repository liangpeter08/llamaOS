import pytest

from dsl import expr as E
from dsl.evaluator import Daily, DayData, Evaluator, SignalBook
from dsl.loader import DSLError, load_text
from dsl.validator import validate
from common import timeutil as T
from datetime import date


def test_example_strategies_validate(risk, orb_text):
    v = validate(orb_text, risk)
    assert v.ok, v.errors
    assert v.strategy.id == "orb_news_long"
    assert set(v.strategy.signals) == {"news.score", "news.catalyst"}


@pytest.mark.parametrize("snippet,msg", [
    ("a: &x 1\nb: *x\n", "anchors"),
    ("a: !!str 1\n", "tags"),
    ("a: 1\na: 2\n", "duplicate key"),
])
def test_loader_rejects_unsafe_yaml(snippet, msg):
    with pytest.raises(DSLError, match=msg):
        load_text(snippet)


def mutate(text, old, new):
    assert old in text
    return text.replace(old, new, 1)


@pytest.mark.parametrize("old,new,err", [
    ("rvol(20) >= rvol_min", "rvol(20) >= 2.0", "bare number"),
    ("    - news.score >= score_min\n", "", None),  # still valid (declared but unused is a warning)
    ("stop: stop_level", "stop: stop_level * (1 - news.score)", "may appear only in entry.when_all"),
    ("risk_per_trade: 0.5%", "risk_per_trade: 2%", "max_risk"),
    ('window: "09:45-11:30"', 'window: "09:00-11:30"', "can only narrow"),
    ("max_trades_per_day: 3", "max_trades_per_day: 50", "loosens the risk control"),
    ("side: long", "side: short", "allow_short"),
    ("crosses_above(close, range_high)", "crosses_above(close, range_hi)", "undeclared name"),
    ("news.catalyst in [earnings, guidance, product, contract]", "news.catalyst in [earnings, rumor]", "not values"),
    ('flatten_at: "15:50"', 'flatten_at: "16:00"', "flatten_by"),
])
def test_validator_rules(risk, orb_text, old, new, err):
    v = validate(mutate(orb_text, old, new), risk)
    if err is None:
        assert v.ok, v.errors
    else:
        assert not v.ok and any(err in e for e in v.errors), v.errors


def test_signal_fencing_through_vars(risk, orb_text):
    t = mutate(orb_text, "  stop_level: or_low(or_minutes) * (1 - stop_buffer)",
               "  stop_level: or_low(or_minutes) * (1 - stop_buffer)\n  hot: news.score * 2")
    t = mutate(t, "    - adv(20) >= min_adv", "    - adv(20) >= min_adv\n    - hot >= score_min")
    v = validate(t, risk)
    assert not v.ok and any("depends on a signal" in e for e in v.errors)


def test_leverage_is_a_warning(risk, orb_text):
    v = validate(orb_text + "leverage: 4\n", risk)
    assert v.ok and any("leverage" in w for w in v.warnings)


def test_parser_precedence():
    n = E.parse("a + b * c >= d and not e or f")
    assert isinstance(n, E.BoolOp) and n.op == "or"


def _day(prices, vols=None, hist_v=1_000_000):
    d = date(2026, 10, 9)
    hist = [Daily(f"h{i}", 10, 10.5, 9.5, 10, hist_v) for i in range(25)]
    dd = DayData("TST", d, hist)
    for i, p in enumerate(prices):
        dd.append(T.open_ts(d) + 60 * i, p, p + 0.05, p - 0.05, p, (vols or [10_000] * len(prices))[i])
    return dd


def test_or_high_missing_until_range_completes(risk, orb_text):
    s = validate(orb_text, risk).strategy
    dd = _day([10 + 0.01 * i for i in range(20)])
    ev = Evaluator(s, dd, SignalBook())
    node = s.vars["range_high"]
    assert ev.value(node, 13) is None  # first 15 minutes not complete
    assert ev.value(node, 14) == pytest.approx(max(dd.h[:15]))


def test_missing_signal_fails_closed(risk, orb_text):
    s = validate(orb_text, risk).strategy
    ev = Evaluator(s, _day([10] * 30), SignalBook())
    cond = s.entry_when_all[2]  # news.score >= score_min
    assert ev.value(cond, 20) is None and ev.cond(cond, 20) is False


def test_signals_are_point_in_time(risk, orb_text):
    s = validate(orb_text, risk).strategy
    dd = _day([10] * 30)
    late = dd.close_ts(20) + 1  # stamped just after bar 20 closed
    book = SignalBook([{"ts": late, "symbol": "TST", "name": "news.score", "value": 0.9}])
    ev = Evaluator(s, dd, book)
    assert ev._signal("news.score", 20) is None
    assert ev._signal("news.score", 21) == 0.9
