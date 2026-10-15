import math
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
    v = validate((ROOT / "strategies" / "spy_noise_breakout_long.v1.yaml").read_text(), risk)
    assert v.ok, v.errors
    return v.strategy


def _day(closes, hist=None, skip=()):
    """One bar per minute from the open (minutes in `skip` have no bar); open = high = low = close."""
    d = date(2026, 3, 2)
    dd = DayData("SPY", d, hist or [Daily(f"h{i}", 100, 101, 99, 101, 1e6) for i in range(20)])
    for m, c in enumerate(closes):
        if m not in skip:
            dd.append(T.open_ts(d) + 60 * m, c, c, c, c, 1_000)
    return dd


def _val(s, dd, src, i):
    return Evaluator(s, dd, SignalBook()).value(E.parse(src), i)


def test_close_at_is_missing_until_the_minutes_complete(strat):
    dd = _day([100 + m for m in range(40)])
    assert _val(strat, dd, "close_at(30)", 28) is None
    assert _val(strat, dd, "close_at(30)", 29) == 129  # bar 29 closes at 10:00
    assert _val(strat, dd, "close_at(30)", 39) == 129  # fixed for the rest of the day


def test_close_at_uses_the_last_bar_inside_the_window(strat):
    # no bar for minute 29: the close of minute 28 stands, once a later bar shows the window is over
    dd = _day([100 + m for m in range(35)], skip={29})
    assert _val(strat, dd, "close_at(30)", dd.asof(T.open_ts(dd.day) + 60 * 30)) == 128


def test_noise_d_scales_the_average_open_to_close_move(strat):
    # prior sessions alternate +1% and -3% open-to-close: average |move| = 2%
    hist = [Daily(f"h{i}", 100, 104, 96, 101 if i % 2 else 97, 1e6) for i in range(14)]
    dd = _day([100] * 390, hist=hist)
    assert _val(strat, dd, "noise_d(14)", 389) == pytest.approx(0.02)
    assert _val(strat, dd, "noise_d(14)", 96) == pytest.approx(0.02 * math.sqrt(97 / 390))
    assert _val(strat, dd, "noise_d(20)", 389) is None  # not enough history


def test_on_interval_marks_each_n_minutes_after_the_open(strat):
    dd = _day([100] * 70)
    marks = [i for i in range(70) if _val(strat, dd, "on_interval(30)", i)]
    assert marks == [29, 59]  # the bars closing at 10:00 and 10:30


def test_noise_breakout_enters_above_both_anchors_on_the_half_hour(strat):
    # flat history of 1% moves; today gaps down (open 99 < prior close 101), so the prior close is the upper anchor
    hist = [Daily(f"h{i}", 100, 101, 99, 101, 1e6) for i in range(20)]
    dd = _day([99] * 30 + [102.5] * 35, hist=hist)
    ev = Evaluator(strat, dd, SignalBook())
    band = 0.01 * math.sqrt(60 / 390)
    assert ev.value(strat.vars["upper_prev"], 59) == pytest.approx(101 * (1 + band))
    assert all(ev.cond(c, 59) for c in strat.entry_when_all)  # 10:30 check
    assert not all(ev.cond(c, 45) for c in strat.entry_when_all)  # above the band, but between checks
    assert not ev.cond(strat.entry_when_all[2], 29)  # at 10:00, 99 is under the prior-close anchor
