"""Backtest data on demand: plan what's missing, the broker's history worker downloads it, the run replays."""
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

from common import db, history
from backtest import workspace as W
from tests.test_portfolio_v2 import V2, strat


class FakeData:
    """Alpaca-shaped daily and 1-minute bars for any symbol, recording each request's adjustment."""
    history_feed = "sip"

    def __init__(self):
        self.calls = []

    def history_bars(self, symbols, timeframe, start, end, adjustment="split"):
        return self.bars(symbols, timeframe, start, end, adjustment)

    def bars(self, symbols, timeframe, start, end, adjustment="raw"):
        self.calls.append((tuple(symbols), timeframe, adjustment))
        out = {}
        for k, sym in enumerate(symbols):
            rows, d = [], datetime.fromtimestamp(start, timezone.utc).date()
            last = datetime.fromtimestamp(end, timezone.utc).date()
            n = 0
            while d <= last:
                if d.weekday() < 5:
                    px = 100 * (1 + 0.03 * (((n * (k + 3)) % 7) - 3) / 3)
                    if timeframe == "1Day":
                        rows.append({"t": f"{d}T04:00:00Z", "o": px, "h": px, "l": px, "c": px, "v": 5_000_000})
                    else:
                        rows += [{"t": f"{d}T{h:02d}:{m:02d}:00Z", "o": px, "h": px, "l": px, "c": px, "v": 1000}
                                 for h, m in ((15, 0), (16, 0), (19, 30))]
                    n += 1
                d += timedelta(days=1)
            out[sym] = rows
        return out


class FakeIndex:
    def history(self, inst, start_ts, before):
        d, out, c = datetime.fromtimestamp(start_ts, timezone.utc).date(), [], 10_000.0
        while d < before:
            if d.weekday() < 5:
                c *= 1.004 if d.day % 5 else 0.975
                out.append((d.isoformat(), c, c, c, c))
            d += timedelta(days=1)
        return out


def worker(api=None):
    from services.broker.history_worker import HistoryWorker
    return HistoryWorker(api or FakeData(), db.url(), index_feed=FakeIndex())


def test_plan_lists_only_what_is_missing(conn, risk):
    s = strat(risk)
    cfg = W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 6, 30))
    spec = history.plan(conn, "portfolio", [*s.symbols, "AMD"], ["NASDAQ_COMPOSITE"], cfg)
    assert spec["daily"] == [*s.symbols, "AMD"] and spec["index"] == ["NASDAQ_COMPOSITE"] and not spec["minute"]
    assert spec["daily_from"] < "2025-01-06" and spec["end"] == "2025-06-30"  # warm-up history before the window
    w = worker()
    w.fulfil(conn, {"id": history.request(conn, spec, "pytest"), "spec": json.dumps(spec)})
    r = db.row(conn, "SELECT status, result FROM data_requests")
    assert r["status"] == "done" and json.loads(r["result"])["daily"] > 1000
    assert {c[2] for c in w.api.calls} == {"split"}  # split-adjusted, so a split isn't replayed as a crash
    assert json.loads(r["result"])["feed"] == "sip" and db.kv_get(conn, "history.feed.AMD")["feed"] == "sip"
    assert history.plan(conn, "portfolio", [*s.symbols, "AMD"], ["NASDAQ_COMPOSITE"], cfg) is None  # stored now: no second download
    # an identical request still pending is reused rather than queued twice
    assert history.request(conn, {**spec, "daily": ["X"]}, "a") == history.request(conn, {**spec, "daily": ["X"]}, "b")


def test_intraday_plan_needs_minute_bars_and_caps_the_window(conn, orb_text, risk):
    from dsl.validator import validate
    s = validate(orb_text, risk).strategy
    cfg = W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 3, 3), end_date=date(2025, 3, 14))
    spec = history.plan(conn, "intraday", ["ACME"], [], cfg)
    assert spec["minute"] == ["ACME"] and spec["daily"] == ["ACME"]
    worker().fulfil(conn, {"id": history.request(conn, spec, "pytest"), "spec": json.dumps(spec)})
    assert db.row(conn, "SELECT COUNT(*) n FROM bars WHERE symbol='ACME'")["n"] == 10 * 3
    assert history.plan(conn, "intraday", ["ACME"], [], cfg) is None
    import pytest
    with pytest.raises(ValueError, match="at most"):
        history.plan(conn, "intraday", ["ACME"], [], W.RunConfig(strategy_hash=s.hash, start_date=date(2024, 1, 2), end_date=date(2025, 1, 2)))


def test_failed_download_is_reported(conn, risk):
    class Empty(FakeData):
        def bars(self, *a, **k):
            return {}
    spec = {"daily": ["ZZZZ"], "minute": [], "index": [], "daily_from": "2025-01-01", "start": "2025-02-01", "end": "2025-03-01"}
    rid = history.request(conn, spec, "pytest")
    worker(Empty()).fulfil(conn, {"id": rid, "spec": json.dumps(spec)})
    r = db.row(conn, "SELECT status, error FROM data_requests WHERE id=?", (rid,))
    assert r["status"] == "failed" and "no daily bars for ZZZZ" in r["error"]


def test_preflight_without_broker_says_what_is_missing(conn, risk):
    from common import registry
    registry.sync(conn, risk)
    s = strat(risk)
    info = W.preflight(conn, W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 6, 30)))
    assert info["fetch"] is None and any("Missing daily bars for MSFT" in w and "make paper" in w for w in info["warnings"])
    db.kv_set(conn, history.WORKER_KEY, {"ts": time.time()})
    info = W.preflight(conn, W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 6, 30)))
    assert info["can_run"] and info["fetch"]["note"].startswith("Will download daily bars for MSFT")


def test_run_downloads_then_replays(conn, risk):
    from common import registry
    registry.sync(conn, risk)
    s = strat(risk)
    w = worker()
    w.start()
    try:
        for _ in range(50):
            if history.available(conn):
                break
            time.sleep(0.1)
        cfg = W.RunConfig(strategy_hash=s.hash, name="2025 H1", start_date=date(2025, 1, 6), end_date=date(2025, 6, 30))
        rid = W.create_run(conn, cfg, "pytest", submit=False)
        W.execute(db.url(), rid)
    finally:
        w.stop.set()
    row = db.row(conn, "SELECT status, error, stage, report FROM backtest_runs WHERE id=?", (rid,))
    assert row["status"] == "completed", row["error"]
    report = json.loads(row["report"])
    assert report["days"][0] >= "2025-01-06" and report["days"][-1] <= "2025-06-30" and len(report["days"]) >= 42
    assert "downloaded for this run" in report["manifest"]["data_source"] and row["stage"] == "Replaying"
    assert {e["kind"] for e in report["overlay_events"]} & {"macro_trigger", "rotation"}


def test_long_window_repeats_holding_periods(conn, risk):
    from backtest import portfolio as BP
    s = strat(risk)
    spec = history.plan(conn, "portfolio", [*s.symbols, "AMD"], ["NASDAQ_COMPOSITE"],
                        W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 12, 31)))
    worker().fulfil(conn, {"id": history.request(conn, spec, "pytest"), "spec": json.dumps(spec)})
    days = [d for d in W.session_days(conn, "portfolio", W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6),
                                                                      end_date=date(2025, 12, 31)), s)]
    once = BP.run(conn, s, risk, days=days)
    assert len(once["days"]) == 42 and "before the end of the requested window" in once["signal_note"]
    rep = BP.run(conn, s, risk, days=days, repeat_cycles=True)
    assert rep["days"][-1] == str(days[-1]) and len(rep["cycles"]) >= 5
    assert [c["reason"] for c in rep["cycles"][:-1]] == ["horizon"] * (len(rep["cycles"]) - 1)
    assert all(a["end"] < b["start"] for a, b in zip(rep["cycles"], rep["cycles"][1:]))


def test_paper_backend_refuses_to_silently_shrink_the_window(conn, risk, monkeypatch):
    import pytest
    from common import registry
    registry.sync(conn, risk)
    s = strat(risk)
    monkeypatch.setenv("BROKER_BACKEND", "alpaca")
    with pytest.raises(ValueError, match="history download isn't running"):
        W.preflight(conn, W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 1, 6), end_date=date(2025, 6, 30)))


def test_recent_listing_is_not_redownloaded_and_explains_the_first_testable_day(conn, orb_text, risk):
    from dsl.validator import validate
    listed = date(2025, 3, 3)

    class Listed(FakeData):  # the feed has nothing before the listing day
        def bars(self, symbols, timeframe, start, end, adjustment="raw"):
            begin = max(start, datetime(listed.year, listed.month, listed.day, tzinfo=timezone.utc).timestamp())
            return super().bars(symbols, timeframe, begin, end, adjustment) if begin <= end else {s: [] for s in symbols}

    s = validate(orb_text, risk).strategy
    cfg = W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 2, 3), end_date=date(2025, 3, 14))
    spec = history.plan(conn, "intraday", ["NEWCO"], [], cfg)
    worker(Listed()).fulfil(conn, {"id": history.request(conn, spec, "pytest"), "spec": json.dumps(spec)})
    first = db.row(conn, "SELECT MIN(day) d FROM daily WHERE symbol='NEWCO'")["d"]  # the fake's 04:00Z stamps land a day early in ET
    assert first < "2025-03-04" and history.first_available(conn, "NEWCO") == first
    assert history.plan(conn, "intraday", ["NEWCO"], [], cfg) is None  # no second download of history that doesn't exist
    gaps = W.history_gaps(conn, ["NEWCO"], cfg)
    assert len(gaps) == 1 and f"daily history starts {first}" in gaps[0] and "needs" in gaps[0] and "Only" in gaps[0]
    later = W.RunConfig(strategy_hash=s.hash, start_date=date(2025, 2, 3), end_date=date(2025, 4, 30))
    worker(Listed()).fulfil(conn, {"id": history.request(conn, history.plan(conn, "intraday", ["NEWCO"], [], later), "pytest"),
                                   "spec": json.dumps(history.plan(conn, "intraday", ["NEWCO"], [], later))})
    gaps = W.history_gaps(conn, ["NEWCO"], later)
    days = [r["day"] for r in db.rows(conn, "SELECT day FROM daily WHERE symbol='NEWCO' ORDER BY day")]
    assert f"first testable session is {days[20]}" in gaps[0]  # the 21st stored session: 20 prior ones
