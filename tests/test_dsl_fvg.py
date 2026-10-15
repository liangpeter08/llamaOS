from datetime import date
from pathlib import Path

import pytest

from common import timeutil as T
from common.config import build_risk, risk_defaults
from dsl import expr as E
from dsl.evaluator import Daily, DayData, Evaluator, SignalBook
from dsl.validator import validate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def strat():
    d, raw = risk_defaults()
    d["account"]["allow_short"] = True
    risk = build_risk(d, raw)
    v = validate((ROOT / "strategies" / "ict_fvg_bias_long.v1.yaml").read_text(), risk)
    assert v.ok, v.errors
    return v.strategy


def _day(bars, hist=None):
    """bars: (high, low, close) per 1-min bar; open = close."""
    d = date(2026, 3, 2)
    dd = DayData("TST", d, hist or [Daily(f"h{i}", 10, 11, 9, 10, 1e6) for i in range(25)])
    for i, (h, l, c) in enumerate(bars):
        dd.append(T.open_ts(d) + 60 * i, c, h, l, c, 1_000)
    return dd


def _val(s, dd, src, i):
    return Evaluator(s, dd, SignalBook()).value(E.parse(src), i)


# bar 0..2 form a bullish gap: high[0] = 10.0 < low[2] = 10.3
BULL = [(10.0, 9.9, 9.95), (10.4, 10.0, 10.35), (10.5, 10.3, 10.45)]


def test_bull_gap_seen_on_later_retest(strat):
    dd = _day(BULL + [(10.6, 10.4, 10.55)] * 5 + [(10.5, 10.2, 10.25)])
    i = len(dd) - 1  # retest bar, 6 bars after the gap formed
    assert _val(strat, dd, "fvg_bull_bottom(60)", i) == pytest.approx(10.0)
    assert _val(strat, dd, "fvg_bull_top(60)", i) == pytest.approx(10.3)
    assert _val(strat, dd, "fvg_bull_top(3)", i) is None  # outside the window


def test_gap_not_visible_on_its_own_completing_bar(strat):
    dd = _day(BULL)
    assert _val(strat, dd, "fvg_bull_top(60)", 2) is None
    dd.append(dd.ts[-1] + 60, 10.4, 10.5, 10.35, 10.4, 1_000)
    assert _val(strat, dd, "fvg_bull_top(60)", 3) == pytest.approx(10.3)


def test_gap_closed_through_is_dropped(strat):
    dd = _day(BULL + [(10.4, 9.8, 9.9), (10.0, 9.8, 9.95)])  # bar 3 closes below the gap bottom
    assert _val(strat, dd, "fvg_bull_top(60)", 4) is None


def test_bear_gap(strat):
    # low[0] = 10.0 > high[2] = 9.7
    dd = _day([(10.1, 10.0, 10.05), (10.0, 9.6, 9.65), (9.7, 9.5, 9.55), (9.6, 9.5, 9.55)])
    assert _val(strat, dd, "fvg_bear_top(60)", 3) == pytest.approx(10.0)
    assert _val(strat, dd, "fvg_bear_bottom(60)", 3) == pytest.approx(9.7)
    assert _val(strat, dd, "fvg_bull_top(60)", 3) is None


def test_prior_high_low(strat):
    hist = [Daily("a", 10, 12, 8, 10, 1e6), Daily("b", 10, 11, 9.5, 10, 1e6)]
    dd = _day([(10, 10, 10)], hist)
    assert _val(strat, dd, "high_d(1)", 0) == 11
    assert _val(strat, dd, "low_d(1)", 0) == 9.5
    assert _val(strat, dd, "high_d(2)", 0) == 12
    assert _val(strat, dd, "low_d(3)", 0) is None  # not enough history
