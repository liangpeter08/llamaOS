"""Fetch backtest history on demand (data_requests rows written by the api).

Runs as a thread inside the paper broker, which is the only service with Alpaca keys, on its own
database connection so a long download never blocks the trading loop. Bars are split-adjusted, so a
split inside a long window isn't replayed as a crash. Each symbol's whole stored daily series is
rewritten at fetch time, keeping it on one adjustment basis.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, timedelta

import httpx

from common import db, market_inputs as MI, timeutil as T
from common.history import WORKER_KEY

from .alpaca import epoch
from .index_feed import IndexFeed

log = logging.getLogger("history")
MINUTE_CHUNK_DAYS = 20  # calendar days per 1-minute request
DAILY_BATCH = 25  # symbols per daily request


class HistoryWorker(threading.Thread):
    def __init__(self, api, dsn: str | None = None, index_feed=None):
        super().__init__(daemon=True, name="history")
        self.api, self.dsn = api, dsn
        self.index_feed = index_feed or IndexFeed()
        self.stop = threading.Event()

    def run(self):
        c = db.connect(self.dsn)
        while not self.stop.is_set():
            try:
                db.kv_set(c, WORKER_KEY, {"ts": time.time(), "backend": "alpaca", "feed": getattr(self.api, "feed", "")})
                r = self.claim(c)
                if r:
                    self.fulfil(c, r)
                    continue
            except Exception:  # noqa: BLE001 - the worker must keep serving later requests
                log.exception("history worker error")
            self.stop.wait(1.0)

    @staticmethod
    def claim(c) -> dict | None:
        row = c.execute("UPDATE data_requests SET status='running', started_at=? WHERE id=(SELECT id FROM data_requests"
                        " WHERE status='pending' AND worker='broker' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *", (time.time(),)).fetchone()
        return dict(row) if row else None

    def fulfil(self, c, r: dict):
        spec = json.loads(r["spec"])
        spec.setdefault("daily", []), spec.setdefault("index", []), spec.setdefault("minute", [])
        steps = len(spec["daily"]) + len(spec["index"]) + len(spec["minute"]) + (1 if spec.get("news") else 0)
        done = 0
        counts = {"daily": 0, "minute": 0, "index": 0, "news": 0}

        def note(text):
            c.execute("UPDATE data_requests SET progress=?, note=? WHERE id=?", (done / max(steps, 1), text, r["id"]))

        try:
            if spec.get("news"):
                n = spec["news"]
                note(f"news for {', '.join(n['symbols'])}")
                counts["news"] = self.news(c, n["symbols"], n["start_ts"], n["end_ts"])
                done += 1
            end = date.fromisoformat(spec["end"]) if spec.get("end") else None
            for k in range(0, len(spec["daily"]), DAILY_BATCH):
                syms = spec["daily"][k:k + DAILY_BATCH]
                note(f"daily bars for {', '.join(syms)}")
                counts["daily"] += self.daily(c, syms, date.fromisoformat(spec["daily_from"]), end)
                done += len(syms)
            for inst in spec["index"]:
                note(f"index history for {inst}")
                rows = self.index_feed.history(inst, T.open_ts(date.fromisoformat(spec["daily_from"])), end + timedelta(days=1))
                if not rows:
                    raise ValueError(f"no history for {inst}: is it configured in config/market_inputs.yaml?")
                with db.tx(c):
                    counts["index"] += MI.store_history(c, inst, rows, MI.feeds().get(inst, {}).get("provider", ""))
                done += 1
            for sym in spec["minute"]:
                note(f"1-minute bars for {sym}")
                counts["minute"] += self.minute(c, sym, date.fromisoformat(spec["start"]), end)
                done += 1
            c.execute("UPDATE data_requests SET status='done', progress=1, note=?, result=?, finished_at=? WHERE id=?",
                      ("done", json.dumps(counts), time.time(), r["id"]))
            log.info("data request %s done: %s", r["id"], counts)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            msg = f"{type(e).__name__}: {e}"[:500]
            c.execute("UPDATE data_requests SET status='failed', error=?, finished_at=? WHERE id=?", (msg, time.time(), r["id"]))
            log.warning("data request %s failed: %s", r["id"], msg)

    def news(self, c, syms: list[str], start_ts: float, end_ts: float) -> int:
        """Historical Alpaca news for a backtest's model replay. Stored as replay_only: live triage never spends calls on it."""
        items = self.api.news(syms, start_ts, end_ts)
        added = 0
        with db.tx(c):
            for n in items:
                cur = c.execute("INSERT INTO news(ext_id, ts, session, symbols, headline, summary, source, url, content, triage)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ext_id) DO NOTHING",
                                ("alpaca:" + str(n["id"]), epoch(n["created_at"]), "alpaca", json.dumps(n["symbols"]), n["headline"],
                                 n.get("summary", ""), n.get("source", "alpaca"), n.get("url"), n.get("content"), "replay_only"))
                added += cur.rowcount
        for sym in syms:  # covered range per symbol, so later runs only download what's new
            old = db.kv_get(c, f"news.fetched.{sym}") or {}
            overlap = old and old["start"] <= end_ts and start_ts <= old["end"]
            db.kv_set(c, f"news.fetched.{sym}", {"start": min(start_ts, old["start"]), "end": max(end_ts, old["end"])} if overlap
                      else {"start": start_ts, "end": end_ts})
        return added

    def daily(self, c, syms: list[str], start: date, end: date) -> int:
        # Rewrite each symbol's whole stored series so every row shares today's split adjustment.
        first = db.row(c, f"SELECT MIN(day) d FROM daily WHERE symbol IN ({','.join('?' * len(syms))})", tuple(syms))["d"]
        frm = min(start, date.fromisoformat(first)) if first else start
        bars = self.api.bars(syms, "1Day", T.open_ts(frm) - 6 * 3600, T.close_ts(end) + 3600, adjustment="split")
        n = 0
        with db.tx(c):
            for sym, rows in bars.items():
                for b in rows:
                    d = T.et(epoch(b["t"])).date()
                    if d > end:
                        continue
                    c.execute("INSERT INTO daily(symbol,day,o,h,l,c,v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,day) DO UPDATE"
                              " SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,v=excluded.v",
                              (sym, d.isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"]))
                    n += 1
        missing = [s for s in syms if not bars.get(s)]
        if missing:
            raise ValueError(f"the feed returned no daily bars for {', '.join(missing)}")
        return n

    def minute(self, c, sym: str, start: date, end: date) -> int:
        n, d = 0, start
        while d <= end:
            stop = min(end, d + timedelta(days=MINUTE_CHUNK_DAYS - 1))
            bars = self.api.bars([sym], "1Min", T.open_ts(d), T.close_ts(stop), adjustment="split").get(sym, [])
            with db.tx(c):
                for b in bars:
                    ts = epoch(b["t"])
                    if T.OPEN <= T.et(ts).time() < T.CLOSE:
                        c.execute("INSERT INTO bars(symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,ts) DO UPDATE"
                                  " SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,v=excluded.v",
                                  (sym, ts, b["o"], b["h"], b["l"], b["c"], b["v"]))
                        n += 1
            d = stop + timedelta(days=1)
        return n
