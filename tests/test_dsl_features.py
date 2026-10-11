"""builtins@v2: daily-history features, peer() cross-symbol reads, references and universe.symbols."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from common import timeutil as T
from core import portfolio as P
from core.engine import StrategyRunner
from dsl import features as F
from dsl.evaluator import PEER_MAX_LAG, Daily, DayData, Evaluator, SignalBook
from dsl.explain import back_translate
from dsl.validator import validate

ROOT = Path(__file__).resolve().parent.parent

LEAD_LAG = """\
dsl: 0.1
id: lead_lag_probe
version: 1
source: {text: "Buy ACMX when BOLT is already up today, ACMX trends up over the week and volatility is calm."}
references: [BOLT]
universe:
  symbols: [ACMX]
  filters:
    - adv(20) >= min_adv
timeframe: 1min
params:
  min_adv:  {value: 1_000_000, range: [100_000, 1_000_000_000]}
  lead_min: {value: 0.1%, range: [-5%, 5%]}
  trend_n:  {value: 5, range: [2, 20]}
  trend_min: {value: -50%, range: [-50%, 50%]}
  vol_cap:  {value: 1.0, range: [0.5, 1.0]}
  atr_mult: {value: 1.5, range: [0.5, 4.0]}
  target_r: {value: 2.0, range: [1.0, 4.0]}
vars:
  lead: peer(BOLT, close / prior_close - 1)
entry:
  side: long
  window: "09:45-11:30"
  when_all:
    - lead >= lead_min
    - ret_d(trend_n) >= trend_min
    - vol_rank(5, 10) <= vol_cap
sizing:
  risk_per_trade: 0.25%
exit:
  stop: entry_price - atr_mult * atr(14)
  target: entry_price + target_r * risk_per_share
  flatten_at: "15:50"
execution:
  mode: manual
"""


def mutate(text, old, new):
    assert old in text
    return text.replace(old, new, 1)


def series(closes, start=date(2026, 1, 5)):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), c))
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- feature math
def test_feature_math():
    s = series([100, 110, 99, 99, 108.9])
    assert F.ret_d(s, 1) == pytest.approx(0.10)
    assert F.ret_d(s, 4) == pytest.approx(0.089)
    assert F.ret_d(s, 5) is None  # needs 6 closes: fails closed
    assert F.sma_d(s, 2) == pytest.approx((99 + 108.9) / 2)
    assert F.highest_d(s, 5) == 110 and F.lowest_d(s, 3) == 99
    assert F.up_days(s, 4) == pytest.approx(0.5)  # +10%, -10%, 0, +10%
    assert F.vol_d(s, 4) == pytest.approx(0.0957427, rel=1e-5)
    assert F.zscore_d(series([1, 2, 3]), 3) == pytest.approx(1.0)
    assert F.zscore_d(series([5, 5, 5]), 3) is None  # zero spread


def test_vol_rank_is_a_percentile_of_its_own_history():
    calm = [100 * (1.001 + 0.002 * (k % 2)) ** k for k in range(20)]
    shocked = series(calm + [calm[-1] * 1.10])
    assert F.vol_rank(shocked, 5, 10) == pytest.approx(1.0)  # today's 5-day vol tops all of the previous 10
    after = series(calm + [calm[-1] * 1.10] + [calm[-1] * 1.10 * 1.001 ** k for k in range(1, 7)])
    assert F.vol_rank(after, 5, 10) < 0.5  # once the shock leaves the 5-day window, vol ranks low again
    assert F.vol_rank(series(calm[:15]), 5, 10) is None  # needs 5 + 10 + 1 closes
    assert F.lookback("vol_rank", [5, 10]) == 16


def test_corr_and_beta_align_on_common_days():
    a = series([100, 102, 101, 104, 103, 106])
    b, px = [a[0][:1] + (50.0,)], 50.0
    for k in range(1, len(a)):  # every daily return exactly twice a's
        px *= 1 + 2 * (a[k][1] / a[k - 1][1] - 1)
        b.append((a[k][0], px))
    assert F.corr_d(a, b, 5) == pytest.approx(1.0)
    assert F.beta_d(a, b, 5) == pytest.approx(0.5, rel=1e-3)
    assert F.beta_d(b, a, 5) == pytest.approx(2.0, rel=1e-3)
    assert F.corr_d(a, b[:-1], 5) is None  # only 5 common sessions: needs 6 closes
    assert F.compute("ret_d", a, [0]) is None and F.compute("ret_d", a, [F.MAX_LOOKBACK + 1]) is None


# ---------------------------------------------------------------- validator
def test_lead_lag_file_validates(risk):
    v = validate(LEAD_LAG, risk)
    assert v.ok, v.errors
    s = v.strategy
    assert s.universe_symbols == ["ACMX"] and s.references == ["BOLT"] and s.input_symbols == ["BOLT"]
    assert s.daily_lookback == 21  # ret_d(trend_n) at the top of its range: 20 returns need 21 closes (vol_rank: 16)
    text = back_translate(s)
    assert "ACMX" in text and "never trades" in text and "for BOLT" in text and "20" not in text.split("Daily features")[0][-5:]


@pytest.mark.parametrize("old,new,err", [
    ("references: [BOLT]\n", "", "listed under references"),
    ("peer(BOLT, close / prior_close - 1)", "peer(CRUX, close / prior_close - 1)", "add CRUX to references"),
    ("peer(BOLT, close / prior_close - 1)", "peer(BOLT, close / entry_price - 1)", "a peer has no position"),
    ("peer(BOLT, close / prior_close - 1)", "peer(BOLT, peer(BOLT, close))", "can't be nested"),
    ("peer(BOLT, close / prior_close - 1)", "peer(close, close)", "listed under references"),
    ("trend_n:  {value: 5, range: [2, 20]}", "trend_n:  {value: 5, range: [2, 300]}", "the limit is 250"),
    ("trend_n:  {value: 5, range: [2, 20]}", "trend_n:  {value: 5, range: [1.5, 20]}", "whole numbers"),
    ("vol_rank(5, 10) <= vol_cap", "vol_rank(1, 10) <= vol_cap", "at least 2"),
    ("vol_rank(5, 10) <= vol_cap", "vol_rank(5, 10) <= 0.8", "bare number"),
    ("ret_d(trend_n) >= trend_min", "ret_d(close) >= trend_min", "whole-number literal or param"),
    ("  symbols: [ACMX]\n", "  symbols: [ACMX]\n  tickers: [X]\n", "unknown key universe.tickers"),
    ("  symbols: [ACMX]\n", "  symbols: [acmx]\n", "not a ticker"),
    ("references: [BOLT]", "references: [BOLT, BOLT]", "listed twice"),
])
def test_validator_guards_new_vocabulary(risk, old, new, err):
    v = validate(mutate(LEAD_LAG, old, new), risk)
    assert not v.ok and any(err in e for e in v.errors), v.errors


def test_unused_reference_is_a_warning(risk):
    v = validate(mutate(LEAD_LAG, "references: [BOLT]", "references: [BOLT, CRUX]"), risk)
    assert v.ok and any("CRUX" in w for w in v.warnings)


def test_signals_inside_peer_stay_fenced(risk):
    t = mutate(LEAD_LAG, "vars:\n", "signals:\n  news.score: {from: news_agent@v1}\nvars:\n")
    t = mutate(t, "    - adv(20) >= min_adv", "    - peer(BOLT, news.score) >= lead_min")
    v = validate(t, risk)
    assert not v.ok and any("may appear only in entry.when_all" in e for e in v.errors)


# ---------------------------------------------------------------- evaluator
def _day(sym, day, closes_hist, bars, start_min=0, step=60):
    hist = [Daily(d, c, c, c, c, 2_000_000) for d, c in series(closes_hist, day - timedelta(days=60))]
    dd = DayData(sym, day, hist)
    for k, p in enumerate(bars):
        dd.append(T.open_ts(day) + start_min * 60 + k * step, p, p, p, p, 10_000)
    return dd


def test_peer_reads_as_of_and_goes_stale(risk):
    s = validate(LEAD_LAG, risk).strategy
    d = date(2026, 10, 9)
    own = _day("ACMX", d, [50] * 30, [50, 50.5, 51, 51.5, 52])
    peer = _day("BOLT", d, [100] * 30, [101, 102])  # only two bars: then the feed stops
    ev = Evaluator(s, own, SignalBook(), {"BOLT": peer})
    lead = s.vars["lead"]
    assert ev.value(lead, 0) == pytest.approx(0.01)
    assert ev.value(lead, 1) == pytest.approx(0.02)
    assert ev.value(lead, 2) == pytest.approx(0.02)  # BOLT's last bar is 60s old: still fresh
    assert own.ts[3] - peer.ts[1] == PEER_MAX_LAG and ev.value(lead, 3) == pytest.approx(0.02)  # at the limit: fresh
    assert own.ts[4] - peer.ts[1] > PEER_MAX_LAG and ev.value(lead, 4) is None  # stale: fail closed
    peer.append(own.ts[4], 103, 103, 103, 103, 1)  # a late bar arrives (live polling): not memoized as missing
    assert ev.value(lead, 4) == pytest.approx(0.03)
    assert Evaluator(s, own, SignalBook(), {}).value(lead, 0) is None  # peer not loaded


def test_daily_features_read_completed_sessions_only(risk):
    s = validate(LEAD_LAG, risk).strategy
    d = date(2026, 10, 9)
    own = _day("ACMX", d, [100 + k for k in range(30)], [500, 500])  # today's bars must not matter
    ev = Evaluator(s, own, SignalBook(), {})
    node = s.entry_when_all[1].l  # ret_d(trend_n)
    assert ev.value(node, 1) == pytest.approx(129 / 124 - 1)


def test_runner_trades_only_its_universe(risk):
    s = validate(mutate(LEAD_LAG, "lead_min: {value: 0.1%", "lead_min: {value: -5%"), risk).strategy
    d = date(2026, 10, 9)
    days = {sym: _day(sym, d, [100 * 1.001 ** k for k in range(30)], [100 + k * 0.01 for k in range(60)], start_min=0)
            for sym in ("ACMX", "CRUX")}
    refs = {"BOLT": _day("BOLT", d, [100] * 30, [101] * 60)}
    r = StrategyRunner(s, risk)
    r.start_day(days, SignalBook(), refs)
    i = 30  # 10:00 ET, inside the entry window
    assert r.check_entry("CRUX", i, 100_000, busy=False) is None
    it = r.check_entry("ACMX", i, 100_000, busy=False)
    assert it is not None and it.symbol == "ACMX"
    assert any(rec["cond"] == "lead >= lead_min" and rec["ok"] for rec in it.decision)


# ---------------------------------------------------------------- backtest
def test_backtest_replays_references_without_trading_them(conn, risk):
    from backtest import engine as bt
    from services.broker.seed import seed
    seed(conn, history_days=8, seed_=7)
    loose = mutate(LEAD_LAG, "lead_min: {value: 0.1%", "lead_min: {value: -5%")
    loose = mutate(loose, "vol_cap:  {value: 1.0", "vol_cap:  {value: 1.0")
    s = validate(loose, risk).strategy
    a, b = bt.run(conn, s, risk, min_history_sessions=1), bt.run(conn, s, risk, min_history_sessions=1)
    assert a["metrics"] == b["metrics"]  # deterministic
    assert a["symbols"] == ["ACMX"]  # the strategy names its ticker
    assert all(t["symbol"] == "ACMX" for t in a["trades"])
    assert all(c.get("references", {}).get("BOLT") for c in a["coverage"] if c["bars"])
    failed = a["diagnostics"]["failed_conditions"]
    assert not failed.get("lead >= lead_min (missing value)")  # BOLT was replayed as-of every bar


# ---------------------------------------------------------------- portfolio
EXAMPLE = (ROOT / "prompts" / "examples" / "portfolio.yaml").read_text()
RISK_OFF = mutate(EXAMPLE, "holdings:\n", "references: [SPY]\nholdings:\n")
RISK_OFF = mutate(RISK_OFF, "  trim_gain:", "  spy_drop:     {value: -1.5%, range: [-5%, -0.5%]}\n"
                  "  trend_n:      {value: 10, range: [5, 40]}\n  trend_floor:  {value: 0%, range: [-10%, 5%]}\n  trim_gain:")
RISK_OFF = mutate(RISK_OFF, "rules:\n", 'rules:\n  - {when: "peer(SPY, day_change) <= spy_drop and ret_d(trend_n) < trend_floor", do: exit_and_wait}\n')


def test_portfolio_rules_read_peers_and_history(risk):
    v = validate(RISK_OFF, risk)
    assert v.ok, v.errors
    s = v.strategy
    assert s.references == ["SPY"] and s.input_symbols == ["SPY"] and s.daily_lookback == 41
    assert "SPY" in back_translate(s) and "40-session" not in back_translate(s)


@pytest.mark.parametrize("old,new,err", [
    ("peer(SPY, day_change)", "peer(SPY, holding)", "only the peer's day_change"),
    ("peer(SPY, day_change)", "peer(QQQ, day_change)", "listed under references"),
    ("ret_d(trend_n)", "sma(day_change, 3)", "not available in portfolio rules"),
    ("ret_d(trend_n)", "ret_d(day_change)", "whole-number literal or param"),
])
def test_portfolio_validator_guards_new_vocabulary(risk, old, new, err):
    v = validate(mutate(RISK_OFF, old, new), risk)
    assert not v.ok and any(err in e for e in v.errors), v.errors


def test_portfolio_plan_uses_peer_and_trend(risk):
    s = validate(RISK_OFF, risk).strategy
    falling = series([100 - k for k in range(45)])
    rising = series([100 + k for k in range(45)])

    def decide(spy_move, hist):
        sl = P.start(s, 100_000, "2026-10-01")
        held = {sym: P.Holding(10, 100.0) for sym in s.symbols}
        sl.sessions = 3
        for st in sl.symbols.values():
            st.mode = "holding"
        quotes = {sym: P.Quote(100.0, 100.0, hist) for sym in s.symbols}
        quotes["SPY"] = P.Quote(100 * (1 + spy_move), 100.0, hist)
        return P.plan(s, sl, quotes, held, "2026-10-09", risk)

    assert {o.reason for o in decide(-0.02, falling).orders} == {"exit_and_wait"}
    assert not decide(-0.02, rising).orders  # trend still up: no risk-off exit
    assert not decide(-0.01, falling).orders  # SPY not down enough
    dp = decide(-0.02, [])  # no history: the trend is missing, so the rule fails closed
    assert not dp.orders


def test_portfolio_backtest_with_reference(conn, risk):
    from backtest import engine as bt
    from tests.test_portfolio import seed_daily
    s = validate(RISK_OFF, risk).strategy
    with pytest.raises(ValueError, match="SPY"):
        bt.run(conn, s, risk)  # the reference is required data
    seed_daily(conn, [*s.symbols, "SPY"], n=90)
    a, b = bt.run(conn, s, risk), bt.run(conn, s, risk)
    assert a["metrics"] == b["metrics"]
    assert "SPY" not in {o["symbol"] for o in a["orders"]}  # read, never traded
