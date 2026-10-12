"""Backtest data on demand: work out what a run's window is missing, ask the broker to fetch it, wait.

The api never holds Alpaca keys. It writes a `data_requests` row; the paper broker's history worker
(services/broker/history_worker.py) downloads split-adjusted daily bars, 1-minute bars and index history
into the same tables live trading uses, and reports progress. Fetched data stays stored, so a repeated
run only downloads what is still missing. Without the paper broker (simulator backend) runs use the
stored data as before.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, timedelta

from . import db
from . import timeutil as T

LOOKBACK_DAYS = {"portfolio": 730, "intraday": 60}  # default window (calendar days) when no start date is given
MAX_MINUTE_DAYS = 190  # 1-minute history fetched for one run, calendar days
SLACK_DAYS = 5  # weekends and holidays at the window edges
WORKER_KEY = "history.worker"


def last_session() -> date:
    """The latest completed regular session (a weekday; holidays simply return no bars)."""
    now = T.et(time.time())
    d = now.date()
    if d.weekday() >= 5 or now.time() < T.CLOSE:
        d = T.prev_weekday(d)
    return d


def window(kind: str, config) -> tuple[date, date]:
    end = min(config.end_date or last_session(), last_session())
    start = config.start_date or end - timedelta(days=LOOKBACK_DAYS.get(kind, 60))
    return start, end


def warmup_days(config, lookback: int = 0) -> int:
    """Calendar days of daily history needed before the first session (prior closes, ADV, ATR, and the
    strategy's daily-history features: `lookback` sessions)."""
    return int(max(config.min_history_sessions, lookback) * 1.6) + 10


def live_data() -> bool:
    """Real market data (Alpaca paper backend): missing history must be downloaded, never silently skipped."""
    import os
    return os.environ.get("BROKER_BACKEND", "simulator") == "alpaca"


def available(c) -> bool:
    w = db.kv_get(c, WORKER_KEY) or {}
    return time.time() - float(w.get("ts") or 0) < 60


def first_available(c, sym: str) -> str | None:
    """The first day the feed has for a symbol, when a download found it starts later than asked (a recent listing)."""
    return (db.kv_get(c, f"history.first.{sym}") or {}).get("day")


def plan(c, kind: str, symbols: list[str], indexes: list[str], config, lookback: int = 0) -> dict | None:
    """What the window needs and the database doesn't have; None when nothing is missing."""
    start, end = window(kind, config)
    if start > end:
        return None
    daily_from = start - timedelta(days=warmup_days(config, lookback))
    need_daily, need_minute, need_index = [], [], []
    for sym in symbols:
        r = db.row(c, "SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM daily WHERE symbol=? AND day>=? AND day<=?",
                   (sym, daily_from.isoformat(), end.isoformat()))
        begins = max((daily_from + timedelta(days=SLACK_DAYS)).isoformat(), first_available(c, sym) or "")
        if not r["n"] or r["a"] > begins or r["b"] < (end - timedelta(days=SLACK_DAYS)).isoformat():
            need_daily.append(sym)
    for inst in indexes:
        r = db.row(c, "SELECT MIN(day) a, MAX(day) b, COUNT(*) n FROM index_daily WHERE instrument=? AND day>=? AND day<=?",
                   (inst, daily_from.isoformat(), end.isoformat()))
        if not r["n"] or r["a"] > (daily_from + timedelta(days=SLACK_DAYS)).isoformat() or r["b"] < (end - timedelta(days=SLACK_DAYS)).isoformat():
            need_index.append(inst)
    if kind == "intraday":
        for sym in symbols:
            days = db.row(c, "SELECT COUNT(*) n FROM daily WHERE symbol=? AND day>=? AND day<=?", (sym, start.isoformat(), end.isoformat()))["n"]
            have = db.row(c, "SELECT COUNT(DISTINCT (to_timestamp(ts) AT TIME ZONE 'America/New_York')::date) n FROM bars"
                             " WHERE symbol=? AND ts>=? AND ts<?", (sym, T.open_ts(start), T.close_ts(end)))["n"]
            if sym in need_daily or have < days:
                need_minute.append(sym)
    if not (need_daily or need_minute or need_index):
        return None
    if need_minute and (end - start).days > MAX_MINUTE_DAYS:
        raise ValueError(f"Intraday runs fetch at most {MAX_MINUTE_DAYS} days of 1-minute bars at a time; narrow the dates "
                         f"(requested {start} to {end}).")
    return {"daily": need_daily, "minute": need_minute, "index": need_index, "daily_from": daily_from.isoformat(),
            "start": start.isoformat(), "end": end.isoformat()}


def describe(spec: dict) -> str:
    parts = []
    if spec["daily"]:
        parts.append(f"daily bars for {', '.join(spec['daily'])}")
    if spec["minute"]:
        parts.append(f"1-minute bars for {', '.join(spec['minute'])}")
    if spec["index"]:
        parts.append(f"index history for {', '.join(spec['index'])}")
    return f"Will download {'; '.join(parts)} from {spec['daily_from']} to {spec['end']}."


def request(c, spec: dict, actor: str, worker: str = "broker") -> int:
    """Queue a job for the broker (downloads) or the models service (model replay). An identical open job is reused."""
    h = hashlib.sha256(json.dumps({**spec, "_worker": worker}, sort_keys=True).encode()).hexdigest()
    r = db.row(c, "SELECT id FROM data_requests WHERE spec_hash=? AND status IN ('pending','running') ORDER BY id LIMIT 1", (h,))
    if r:
        return r["id"]
    return c.insert("INSERT INTO data_requests(ts, spec, spec_hash, requested_by, worker) VALUES (?,?,?,?,?)",
                    (time.time(), json.dumps(spec), h, actor, worker))


def wait(c, rid: int, on_progress=None, timeout: float = 900) -> dict:
    """Block until the broker finishes the request; raises on failure, timeout or a stopped worker."""
    t0 = time.time()
    while True:
        r = db.row(c, "SELECT * FROM data_requests WHERE id=?", (rid,))
        if r["status"] == "done":
            return json.loads(r["result"] or "{}")
        if r["status"] == "failed":
            raise ValueError(f"{'Model replay' if r.get('worker') == 'models' else 'Data download'} failed: {r['error']}")
        if on_progress:
            on_progress(r["progress"] or 0.0, r["note"] or "waiting for the broker")
        if time.time() - t0 > timeout:
            raise ValueError("Data download timed out; the broker may be busy. Try again.")
        if r["status"] == "pending" and time.time() - t0 > 30:
            key = WORKER_KEY if r.get("worker", "broker") == "broker" else "replay.worker"
            if time.time() - float((db.kv_get(c, key) or {}).get("ts") or 0) > 60:
                raise ValueError("Data download needs the Alpaca paper broker running (make paper)." if key == WORKER_KEY
                                 else "Model replay needs the models service running.")
        time.sleep(1)
