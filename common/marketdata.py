"""Read/write market data and recorded signals in SQLite."""
from __future__ import annotations

import json
from datetime import date

from dsl.evaluator import Daily, DayData, SignalBook
from dsl.features import HISTORY_SESSIONS

from . import db
from . import timeutil as T
from .sim import SimDay


def store_daily(conn, sd: SimDay):
    conn.executemany(
        "INSERT INTO daily(symbol, day, o, h, l, c, v) VALUES (?,?,?,?,?,?,?) ON CONFLICT (symbol, day) DO NOTHING",
        [(sym, sd.day.isoformat(), *v) for sym, v in sd.daily.items()],
    )


def store_bars(conn, sd: SimDay):
    conn.executemany(
        "INSERT INTO bars(symbol, ts, o, h, l, c, v) VALUES (?,?,?,?,?,?,?) ON CONFLICT (symbol, ts) DO NOTHING",
        [(sym, *b) for sym, bars in sd.bars.items() for b in bars],
    )


def store_news(conn, sd: SimDay, session: str):
    conn.executemany(
        "INSERT INTO news(ext_id, ts, session, symbols, headline, summary, source) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT (ext_id) DO NOTHING",
        [(n.ext_id, n.ts, session, json.dumps(n.symbols), n.headline, n.summary, n.source) for n in sd.news],
    )


def history(conn, symbol: str, before: date, n: int = HISTORY_SESSIONS) -> list[Daily]:
    rs = db.rows(conn, "SELECT * FROM daily WHERE symbol=? AND day<? ORDER BY day DESC LIMIT ?",
                 (symbol, before.isoformat(), n))
    return [Daily(r["day"], r["o"], r["h"], r["l"], r["c"], r["v"]) for r in reversed(rs)]


def load_day(conn, symbol: str, day: date) -> DayData:
    d = DayData(symbol, day, history(conn, symbol, day))
    for r in conn.execute("SELECT * FROM bars WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts",
                          (symbol, T.open_ts(day), T.close_ts(day))):
        d.append(r["ts"], r["o"], r["h"], r["l"], r["c"], r["v"])
    return d


def bar_days(conn) -> list[date]:
    """Sessions that have intraday bars stored, oldest first."""
    return [date.fromisoformat(r['day']) for r in conn.execute(
        "SELECT DISTINCT (to_timestamp(ts) AT TIME ZONE 'America/New_York')::date::text AS day FROM bars ORDER BY day")]


def signal_book(conn, start_ts: int, end_ts: int) -> SignalBook:
    rs = db.rows(conn, "SELECT ts, symbol, name, value FROM signals WHERE ts>=? AND ts<=?", (start_ts, end_ts))
    for r in rs:
        r["value"] = json.loads(r["value"])
    return SignalBook(rs)


def symbols(conn) -> list[str]:
    return [r["symbol"] for r in conn.execute("SELECT DISTINCT symbol FROM daily ORDER BY symbol")]
