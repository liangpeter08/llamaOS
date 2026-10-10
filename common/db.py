"""PostgreSQL storage shared by broker, models and web.

Connection string: DATABASE_URL (default postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos).

Code in this repo writes SQL with `?` placeholders; the Conn wrapper translates them to
psycopg's `%s`. Connections run in autocommit mode; use `tx(conn)` for a transaction.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DEFAULT_URL = "postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos"

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_sessions(
 token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS monitoring_watchlist(
 symbol TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE,
 updated_at DOUBLE PRECISION NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_failures(
 id BIGSERIAL PRIMARY KEY, key TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS login_failures_lookup ON login_failures(key,ts);

CREATE TABLE IF NOT EXISTS bars(
  symbol TEXT NOT NULL, ts BIGINT NOT NULL,
  o DOUBLE PRECISION, h DOUBLE PRECISION, l DOUBLE PRECISION, c DOUBLE PRECISION, v BIGINT,
  PRIMARY KEY(symbol, ts));

CREATE TABLE IF NOT EXISTS daily(
  symbol TEXT NOT NULL, day TEXT NOT NULL,
  o DOUBLE PRECISION, h DOUBLE PRECISION, l DOUBLE PRECISION, c DOUBLE PRECISION, v BIGINT,
  PRIMARY KEY(symbol, day));

CREATE TABLE IF NOT EXISTS news(
  id BIGSERIAL PRIMARY KEY, ext_id TEXT UNIQUE, ts BIGINT NOT NULL, session TEXT,
  symbols TEXT, headline TEXT, summary TEXT, source TEXT,
  triage TEXT, triage_reason TEXT, triaged_at BIGINT, tier_detail TEXT);
CREATE INDEX IF NOT EXISTS news_ts ON news(ts);
ALTER TABLE news ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE news ADD COLUMN IF NOT EXISTS content TEXT;
CREATE INDEX IF NOT EXISTS news_triage ON news(triage, ts);
CREATE INDEX IF NOT EXISTS news_fts ON news USING GIN (to_tsvector('english', coalesce(headline,'') || ' ' || coalesce(summary,'')));

CREATE TABLE IF NOT EXISTS signals(
  id BIGSERIAL PRIMARY KEY, ts BIGINT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
  value TEXT NOT NULL, confidence DOUBLE PRECISION, news_id BIGINT, source TEXT,
  model TEXT, tier TEXT, rationale TEXT);
CREATE INDEX IF NOT EXISTS signals_lookup ON signals(symbol, name, ts);
CREATE INDEX IF NOT EXISTS signals_news ON signals(news_id);

CREATE TABLE IF NOT EXISTS model_calls(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, provider TEXT, model TEXT, task TEXT,
  latency_ms DOUBLE PRECISION, ok INTEGER, error TEXT, input_tokens BIGINT, output_tokens BIGINT,
  cost_usd DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS strategies(
  hash TEXT PRIMARY KEY, sid TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL,
  status TEXT NOT NULL, mode TEXT NOT NULL, created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION,
  validation TEXT, backtest TEXT, back_translation TEXT,
  approved_by TEXT, approved_at DOUBLE PRECISION, paper_started_at DOUBLE PRECISION);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS display_name TEXT;

CREATE TABLE IF NOT EXISTS intents(
  id BIGSERIAL PRIMARY KEY, ts BIGINT, strategy_hash TEXT, sid TEXT, symbol TEXT, side TEXT,
  qty INTEGER, entry DOUBLE PRECISION, limit_price DOUBLE PRECISION, stop DOUBLE PRECISION,
  target DOUBLE PRECISION, risk_usd DOUBLE PRECISION, risk_pct DOUBLE PRECISION,
  decision TEXT, signals TEXT, guard TEXT, status TEXT, mode TEXT,
  created_wall DOUBLE PRECISION, expires_wall DOUBLE PRECISION,
  decided_by TEXT, decided_wall DOUBLE PRECISION, reason TEXT, recheck TEXT);
CREATE INDEX IF NOT EXISTS intents_status ON intents(status);

CREATE TABLE IF NOT EXISTS orders(
  id BIGSERIAL PRIMARY KEY, intent_id BIGINT, ts BIGINT, symbol TEXT, side TEXT, qty INTEGER,
  kind TEXT, limit_price DOUBLE PRECISION, stop DOUBLE PRECISION, target DOUBLE PRECISION, status TEXT,
  filled_qty INTEGER DEFAULT 0, avg_price DOUBLE PRECISION, updated_ts BIGINT);

CREATE TABLE IF NOT EXISTS fills(
  id BIGSERIAL PRIMARY KEY, order_id BIGINT, intent_id BIGINT, ts BIGINT, symbol TEXT,
  side TEXT, qty INTEGER, price DOUBLE PRECISION, kind TEXT);

CREATE TABLE IF NOT EXISTS positions(
  symbol TEXT PRIMARY KEY, qty INTEGER, side TEXT, avg_price DOUBLE PRECISION, stop DOUBLE PRECISION,
  target DOUBLE PRECISION, strategy_hash TEXT, sid TEXT, intent_id BIGINT, opened_ts BIGINT,
  last_price DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS trades(
  id BIGSERIAL PRIMARY KEY, intent_id BIGINT, sid TEXT, strategy_hash TEXT, symbol TEXT,
  side TEXT, qty INTEGER, entry_ts BIGINT, entry_price DOUBLE PRECISION, exit_ts BIGINT,
  exit_price DOUBLE PRECISION, exit_kind TEXT, pnl DOUBLE PRECISION, r_multiple DOUBLE PRECISION,
  narrative TEXT);

CREATE TABLE IF NOT EXISTS equity_curve(ts BIGINT PRIMARY KEY, equity DOUBLE PRECISION, cash DOUBLE PRECISION);

-- Persistent account history across sessions (never wiped by the broker).
-- source: 'replay' (history re-run by scripts/replay) or 'live' (simulated sessions).
-- strat_pnl: JSON {strategy label: session P&L so far (realized + open)}.
CREATE TABLE IF NOT EXISTS equity_history(
  ts BIGINT NOT NULL, source TEXT NOT NULL, session TEXT NOT NULL,
  equity DOUBLE PRECISION, cash DOUBLE PRECISION, strat_pnl TEXT,
  PRIMARY KEY(source, ts));
CREATE INDEX IF NOT EXISTS equity_history_ts ON equity_history(ts);

CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, updated_at DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS commands(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, kind TEXT, payload TEXT, issued_by TEXT,
  status TEXT DEFAULT 'pending', processed_at DOUBLE PRECISION, result TEXT);

CREATE TABLE IF NOT EXISTS users(
  id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE, role TEXT, pw_hash TEXT,
  totp_secret TEXT, created_at DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS theses(
  id BIGSERIAL PRIMARY KEY, created_at DOUBLE PRECISION, text TEXT, tags TEXT,
  confidence DOUBLE PRECISION, expires TEXT, active INTEGER DEFAULT 1, created_by TEXT);

CREATE TABLE IF NOT EXISTS notes(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, symbol TEXT, text TEXT, stance TEXT, parsed_by TEXT,
  created_by TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS ideas(
  id BIGSERIAL PRIMARY KEY, created_at DOUBLE PRECISION, run_id TEXT, title TEXT, hypothesis TEXT,
  mechanism TEXT, falsify TEXT, evidence TEXT, event_ids TEXT, sample_size INTEGER,
  status TEXT, anecdotal INTEGER, thesis_ids TEXT, model TEXT, holdout TEXT,
  spec TEXT, strategy_hash TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS chats(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, thread TEXT, role TEXT, content TEXT, model TEXT);
CREATE INDEX IF NOT EXISTS chats_thread ON chats(thread, id);

-- Portfolio sleeves (kind: portfolio strategies). Held across sessions; never wiped by the broker.
CREATE TABLE IF NOT EXISTS portfolio_state(
  strategy_hash TEXT PRIMARY KEY, sid TEXT NOT NULL, state TEXT NOT NULL, updated_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS portfolio_orders(
  id BIGSERIAL PRIMARY KEY, strategy_hash TEXT NOT NULL, sid TEXT NOT NULL, day TEXT NOT NULL, ts DOUBLE PRECISION,
  symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, ref_price DOUBLE PRECISION, reason TEXT,
  decision TEXT, guard TEXT, recheck TEXT, status TEXT NOT NULL, mode TEXT, client_id TEXT UNIQUE, broker_id TEXT,
  filled_qty DOUBLE PRECISION NOT NULL DEFAULT 0, avg_price DOUBLE PRECISION, created_wall DOUBLE PRECISION,
  expires_wall DOUBLE PRECISION, decided_by TEXT, decided_wall DOUBLE PRECISION, note TEXT);
CREATE INDEX IF NOT EXISTS portfolio_orders_status ON portfolio_orders(status);
CREATE INDEX IF NOT EXISTS portfolio_orders_strategy ON portfolio_orders(strategy_hash, id);
CREATE TABLE IF NOT EXISTS portfolio_days(
  strategy_hash TEXT NOT NULL, day TEXT NOT NULL, records TEXT NOT NULL, event TEXT, PRIMARY KEY(strategy_hash, day));

-- Fund allocation per strategy line (sid, across versions). Set on the dashboard within risk.yaml funds.
CREATE TABLE IF NOT EXISTS strategy_funds(
  sid TEXT PRIMARY KEY, allocation DOUBLE PRECISION NOT NULL, max_drawdown DOUBLE PRECISION NOT NULL,
  daily_loss DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'active', reason TEXT, paused_day TEXT,
  base_usd DOUBLE PRECISION NOT NULL, base_ts DOUBLE PRECISION NOT NULL, peak_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
  source TEXT, updated_by TEXT, updated_at DOUBLE PRECISION);

-- Owner-set risk controls (dashboard Risk page) over the factory defaults in config/risk.yaml.
CREATE TABLE IF NOT EXISTS risk_settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS risk_settings_history(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, key TEXT NOT NULL, old_value TEXT, new_value TEXT,
  actor TEXT, reason TEXT);

CREATE TABLE IF NOT EXISTS audit(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL, actor TEXT,
  payload TEXT, prev_hash TEXT, hash TEXT NOT NULL);
"""

# Tables that hold one simulated session's live state. Wiped when the broker starts a day.
SESSION_TABLES = ("intents", "orders", "fills", "positions", "trades", "equity_curve", "commands")

# Serializes appends to the audit hash chain across processes.
AUDIT_LOCK_KEY = 0x6C6C616D61  # "llama"

_local = threading.local()
_QMARK = re.compile(r"\?")


def url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _translate(sql: str) -> str:
    """`?` placeholders -> `%s`; literal `%` -> `%%` (psycopg's format)."""
    return _QMARK.sub("%s", sql.replace("%", "%%"))


class Conn:
    """Thin psycopg wrapper: `?` placeholders, dict rows, `insert()` returning the new id."""

    def __init__(self, dsn: str):
        self.raw = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)
        self.dsn = dsn

    def execute(self, sql: str, args=()):
        if not args:
            return self.raw.execute(sql)  # no params: psycopg sends the text as-is
        return self.raw.execute(_translate(sql), tuple(args))

    def executemany(self, sql: str, seq):
        with self.raw.cursor() as cur:
            cur.executemany(_translate(sql), [tuple(a) for a in seq])

    def insert(self, sql: str, args=()) -> int:
        return self.execute(sql.rstrip().rstrip(";") + " RETURNING id", args).fetchone()["id"]

    def script(self, sql: str):
        self.raw.execute(sql)  # no parameters: multiple statements allowed

    def close(self):
        self.raw.close()

    @property
    def closed(self) -> bool:
        return self.raw.closed


def connect(dsn: str | None = None) -> Conn:
    return Conn(dsn or url())


def get(dsn: str | None = None) -> Conn:
    """One connection per thread per DSN (reconnects if the server dropped it)."""
    key = dsn or url()
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    c = conns.get(key)
    if c is None or c.closed:
        c = conns[key] = connect(key)
    return c


def init(conn: Conn) -> None:
    with conn.raw.transaction():
        conn.raw.execute("SELECT pg_advisory_xact_lock(42)")  # concurrent services may init at once
        conn.script(SCHEMA)


@contextmanager
def tx(conn: Conn):
    with conn.raw.transaction():
        yield conn


def kv_set(conn: Conn, k: str, v) -> None:
    conn.execute(
        "INSERT INTO kv(k, v, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (k, json.dumps(v), time.time()),
    )


def kv_get(conn: Conn, k: str, default=None):
    r = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def kv_age(conn: Conn, k: str) -> float | None:
    r = conn.execute("SELECT updated_at FROM kv WHERE k=?", (k,)).fetchone()
    return time.time() - r["updated_at"] if r else None


def rows(conn: Conn, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def row(conn: Conn, sql: str, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def reset(conn: Conn) -> None:
    """Drop every table (tests and `make reset-db`)."""
    conn.script("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
