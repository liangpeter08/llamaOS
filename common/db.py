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
-- Archived versions are hidden from lists by default; only draft/backtested/retired versions can be archived.
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS archived_at DOUBLE PRECISION;
-- Owner-assigned category per strategy line (sid), shared by every version.
CREATE TABLE IF NOT EXISTS strategy_categories(sid TEXT PRIMARY KEY, category TEXT NOT NULL, updated_at DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS backtest_runs(
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, strategy_hash TEXT NOT NULL,
 sid TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION,
 created_by TEXT NOT NULL, config TEXT NOT NULL, strategy_text TEXT NOT NULL,
 risk_snapshot TEXT NOT NULL, report TEXT, error TEXT, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE, heartbeat DOUBLE PRECISION,
 parent_id BIGINT);
CREATE INDEX IF NOT EXISTS backtest_runs_created ON backtest_runs(created_at DESC);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS metrics TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS sessions INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS archived_at DOUBLE PRECISION;
CREATE TABLE IF NOT EXISTS backtest_presets(
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL, created_by TEXT NOT NULL);

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

CREATE TABLE IF NOT EXISTS discovery_runs(
  id BIGSERIAL PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, created_by TEXT NOT NULL, ticker TEXT NOT NULL,
  config TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  profile TEXT, report TEXT, error TEXT, finished_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS discovery_trials(
  id BIGSERIAL PRIMARY KEY, run_id BIGINT NOT NULL, created_at DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL,
  generation INTEGER NOT NULL, parent_id BIGINT, origin TEXT NOT NULL, family TEXT, title TEXT, hypothesis TEXT,
  change TEXT, yaml TEXT NOT NULL, hash TEXT, status TEXT NOT NULL, score DOUBLE PRECISION, evaluation TEXT,
  holdout TEXT, strategy_hash TEXT);
CREATE INDEX IF NOT EXISTS discovery_trials_run ON discovery_trials(run_id, kind);

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

-- DSL 0.2 portfolio overlays: index history and quotes (from the account-configured index feed),
-- overlay/assumption events shown on the dashboard, and model assessments of monitored assumptions.
ALTER TABLE portfolio_orders ADD COLUMN IF NOT EXISTS reserve_qty INTEGER NOT NULL DEFAULT 0;
ALTER TABLE portfolio_orders ADD COLUMN IF NOT EXISTS overlay TEXT;
CREATE TABLE IF NOT EXISTS index_daily(
  instrument TEXT NOT NULL, day TEXT NOT NULL, o DOUBLE PRECISION, h DOUBLE PRECISION, l DOUBLE PRECISION,
  c DOUBLE PRECISION NOT NULL, source TEXT, PRIMARY KEY(instrument, day));
CREATE TABLE IF NOT EXISTS portfolio_events(
  id BIGSERIAL PRIMARY KEY, strategy_hash TEXT NOT NULL, sid TEXT NOT NULL, day TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL,
  kind TEXT NOT NULL, overlay TEXT, mode TEXT, payload TEXT, context TEXT);
CREATE INDEX IF NOT EXISTS portfolio_events_strategy ON portfolio_events(strategy_hash, id);
CREATE TABLE IF NOT EXISTS assumption_checks(
  id BIGSERIAL PRIMARY KEY, strategy_hash TEXT NOT NULL, assumption_id TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL,
  trigger TEXT, tier TEXT, status TEXT NOT NULL, answers TEXT, evidence TEXT, evidence_hash TEXT, context_hash TEXT,
  actions TEXT, mode TEXT, cost_usd DOUBLE PRECISION DEFAULT 0, error TEXT);
CREATE INDEX IF NOT EXISTS assumption_checks_strategy ON assumption_checks(strategy_hash, assumption_id, id);

-- Backtest data fetched on demand: the api asks, the paper broker (the only holder of Alpaca keys) downloads.
CREATE TABLE IF NOT EXISTS data_requests(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, spec TEXT NOT NULL, spec_hash TEXT NOT NULL, requested_by TEXT,
  status TEXT NOT NULL DEFAULT 'pending', progress DOUBLE PRECISION DEFAULT 0, note TEXT, result TEXT, error TEXT,
  started_at DOUBLE PRECISION, finished_at DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS data_requests_status ON data_requests(status, id);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE data_requests ADD COLUMN IF NOT EXISTS worker TEXT NOT NULL DEFAULT 'broker';
-- Model replay for backtests: one cached triage decision per story and replay mode (never written to live signals).
CREATE TABLE IF NOT EXISTS news_assessments(
  news_id BIGINT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, symbols TEXT, tier TEXT,
  answers TEXT, model TEXT, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, ts DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(news_id, mode));
CREATE TABLE IF NOT EXISTS assumption_replays(
  strategy_hash TEXT NOT NULL, assumption_id TEXT NOT NULL, day TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  tier TEXT, answers TEXT, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, error TEXT, ts DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(strategy_hash, assumption_id, day, mode));

-- Fund allocation per strategy line (sid, across versions). Set on the dashboard within risk.yaml funds.
CREATE TABLE IF NOT EXISTS strategy_funds(
  sid TEXT PRIMARY KEY, allocation DOUBLE PRECISION NOT NULL, max_drawdown DOUBLE PRECISION NOT NULL,
  daily_loss DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'active', reason TEXT, paused_day TEXT,
  base_usd DOUBLE PRECISION NOT NULL, base_ts DOUBLE PRECISION NOT NULL, peak_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
  source TEXT, updated_by TEXT, updated_at DOUBLE PRECISION);
-- Fixed-dollar allocation; NULL means `allocation` (share of equity) applies.
ALTER TABLE strategy_funds ADD COLUMN IF NOT EXISTS alloc_usd DOUBLE PRECISION;
-- Share of the allocation held for the strategy while idle; the rest is drawn from the shared pool when it trades.
ALTER TABLE strategy_funds ADD COLUMN IF NOT EXISTS reserved DOUBLE PRECISION NOT NULL DEFAULT 1;
-- The line's lifetime P&L (realized + open) when base_ts was set; P&L since set = lifetime now - base_pnl.
-- NULL on rows from before it existed: filled in from the trades and equity marks on the next read.
ALTER TABLE strategy_funds ADD COLUMN IF NOT EXISTS base_pnl DOUBLE PRECISION;

-- Owner-set risk controls (dashboard Risk page) over the factory defaults in config/risk.yaml.
CREATE TABLE IF NOT EXISTS risk_settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS risk_settings_history(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, key TEXT NOT NULL, old_value TEXT, new_value TEXT,
  actor TEXT, reason TEXT);

-- Four-agent review panel results for design-chat drafts.
CREATE TABLE IF NOT EXISTS design_reviews(
  id BIGSERIAL PRIMARY KEY, thread TEXT NOT NULL, draft_hash TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL,
  role TEXT NOT NULL, model TEXT, result TEXT NOT NULL, actor TEXT);
CREATE INDEX IF NOT EXISTS design_reviews_thread ON design_reviews(thread, draft_hash);

CREATE TABLE IF NOT EXISTS audit(
  id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL, actor TEXT,
  payload TEXT, prev_hash TEXT, hash TEXT NOT NULL);

-- Alpaca paper accounts managed on the dashboard; the secret is sealed to the broker's vault key.
CREATE TABLE IF NOT EXISTS broker_accounts(
  id BIGSERIAL PRIMARY KEY, label TEXT NOT NULL, key_id TEXT UNIQUE NOT NULL, secret_sealed TEXT NOT NULL,
  account_number TEXT, active INTEGER NOT NULL DEFAULT 0, created_by TEXT, created_at DOUBLE PRECISION,
  activated_at DOUBLE PRECISION);
"""

# Per-account data: with an Alpaca account active, these live in schema acct_<id> and every
# connection's search_path is "acct_<id>, public", so the same SQL reads that account's book.
# Everything else (market data, news, strategy files and backtests, users, audit, kv) is shared.
ACCOUNT_TABLES = ("intents", "orders", "fills", "positions", "trades", "equity_curve", "equity_history",
                  "commands", "portfolio_state", "portfolio_orders", "portfolio_days", "portfolio_events",
                  "strategy_funds", "risk_settings", "risk_settings_history")

# Shared strategy registry, per-account deployment: which versions run (paper/live), their execution
# mode and approval. The account's `strategies` view overlays strategy_state on public.strategies;
# writes are routed by INSTEAD OF triggers, so registry code is unchanged.
ACCOUNT_STRATEGIES = """
CREATE TABLE IF NOT EXISTS strategy_state(
  hash TEXT PRIMARY KEY, status TEXT, mode TEXT, approved_by TEXT, approved_at DOUBLE PRECISION,
  paper_started_at DOUBLE PRECISION, updated_at DOUBLE PRECISION);
CREATE OR REPLACE VIEW strategies AS
  SELECT p.hash, p.sid, p.version, p.path,
         COALESCE(s.status, CASE WHEN p.status IN ('paper','live') THEN 'backtested' ELSE p.status END) AS status,
         COALESCE(s.mode, REPLACE(p.mode, 'live_', 'paper_')) AS mode,
         p.created_at, GREATEST(p.updated_at, s.updated_at) AS updated_at, p.validation, p.backtest, p.back_translation,
         s.approved_by, s.approved_at, s.paper_started_at, p.display_name, p.archived_at
  FROM public.strategies p LEFT JOIN strategy_state s ON s.hash = p.hash;
CREATE OR REPLACE FUNCTION strategies_write() RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM strategy_state WHERE hash = OLD.hash;
    DELETE FROM public.strategies WHERE hash = OLD.hash;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO public.strategies(hash, sid, version, path, status, mode, created_at, updated_at, validation,
                                  backtest, back_translation, display_name, archived_at)
    VALUES (NEW.hash, NEW.sid, NEW.version, NEW.path, NEW.status, COALESCE(NEW.mode, 'paper_manual'), NEW.created_at,
            NEW.updated_at, NEW.validation, NEW.backtest, NEW.back_translation, NEW.display_name, NEW.archived_at);
    RETURN NEW;
  END IF;
  UPDATE public.strategies SET sid = NEW.sid, version = NEW.version, path = NEW.path, validation = NEW.validation,
         backtest = NEW.backtest, back_translation = NEW.back_translation, display_name = NEW.display_name,
         archived_at = NEW.archived_at, updated_at = GREATEST(updated_at, NEW.updated_at),
         -- a finished backtest is shared: every account may then promote the version
         status = CASE WHEN NEW.status = 'backtested' AND status = 'draft' THEN 'backtested' ELSE status END
   WHERE hash = OLD.hash;
  IF NEW.status IS DISTINCT FROM OLD.status OR NEW.mode IS DISTINCT FROM OLD.mode
     OR NEW.approved_by IS DISTINCT FROM OLD.approved_by OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
     OR NEW.paper_started_at IS DISTINCT FROM OLD.paper_started_at THEN
    INSERT INTO strategy_state(hash, status, mode, approved_by, approved_at, paper_started_at, updated_at)
    VALUES (NEW.hash, NEW.status, NEW.mode, NEW.approved_by, NEW.approved_at, NEW.paper_started_at, NEW.updated_at)
    ON CONFLICT(hash) DO UPDATE SET status = excluded.status, mode = excluded.mode, approved_by = excluded.approved_by,
       approved_at = excluded.approved_at, paper_started_at = excluded.paper_started_at, updated_at = excluded.updated_at;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS strategies_write ON strategies;
CREATE TRIGGER strategies_write INSTEAD OF INSERT OR UPDATE OR DELETE ON strategies
  FOR EACH ROW EXECUTE FUNCTION strategies_write();
"""

_STMT_TABLE = re.compile(r"^\s*(?:CREATE TABLE IF NOT EXISTS|CREATE INDEX IF NOT EXISTS \w+ ON|ALTER TABLE)\s+(\w+)", re.I)


def account_ddl() -> str:
    """The SCHEMA statements for ACCOUNT_TABLES (run with search_path set to the account schema)."""
    body = "\n".join(l for l in SCHEMA.splitlines() if not l.strip().startswith("--"))
    out = []
    for stmt in body.split(";"):
        m = _STMT_TABLE.match(stmt)
        if m and m.group(1) in ACCOUNT_TABLES:
            out.append(stmt.strip() + ";")
    return "\n".join(out) + ACCOUNT_STRATEGIES


def account_schema(aid: int) -> str:
    return f"acct_{int(aid)}"


def ensure_account_schema(conn: "Conn", schema: str) -> bool:
    """Create an account's tables (idempotent). A new account starts with an empty book, nothing
    deployed, and a copy of the current risk controls. Returns True when the schema was new."""
    if not re.fullmatch(r"acct_\d+", schema):
        raise ValueError("bad account schema")
    with conn.raw.transaction():
        conn.raw.execute("SELECT pg_advisory_xact_lock(42)")
        new = not conn.raw.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()
        conn.raw.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.raw.execute(f"SET LOCAL search_path TO {schema}, public")
        conn.raw.execute(account_ddl())
        if new:
            conn.raw.execute(f"INSERT INTO {schema}.risk_settings SELECT * FROM public.risk_settings")
    return new


# Search path: which account's tables unqualified SQL sees. Long-running services follow the
# active account (kv 'broker.account_schema', re-read every few seconds); the broker and watchdog
# pin the account their Alpaca keys belong to and restart when it changes.
_scope = {"pinned": False, "schema": None, "checked": 0.0}
SCOPE_TTL = 2.0


def pin(schema: str | None) -> None:
    _scope.update(pinned=True, schema=schema, checked=time.time())


def unpin() -> None:
    _scope.update(pinned=False, schema=None, checked=0.0)


def _wanted(raw) -> str | None:
    if _scope["pinned"] or time.time() - _scope["checked"] < SCOPE_TTL:
        return _scope["schema"]
    try:
        r = raw.execute("SELECT v FROM public.kv WHERE k='broker.account_schema'").fetchone()
        schema = json.loads(r["v"]) if r else None
    except psycopg.Error:  # fresh database: no kv yet
        schema = None
    _scope.update(schema=schema, checked=time.time())
    return schema


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
        self.scope: str | None = None  # schema in search_path ahead of public

    def follow(self):
        """Point search_path at the active account (outside transactions only)."""
        if self.raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            return
        want = _wanted(self.raw)
        if want != self.scope:
            self.raw.execute(f"SET search_path TO {want}, public" if want else "SET search_path TO public")
            self.scope = want

    def execute(self, sql: str, args=()):
        self.follow()
        if not args:
            return self.raw.execute(sql)  # no params: psycopg sends the text as-is
        return self.raw.execute(_translate(sql), tuple(args))

    def executemany(self, sql: str, seq):
        self.follow()
        with self.raw.cursor() as cur:
            cur.executemany(_translate(sql), [tuple(a) for a in seq])

    def insert(self, sql: str, args=()) -> int:
        return self.execute(sql.rstrip().rstrip(";") + " RETURNING id", args).fetchone()["id"]

    def script(self, sql: str):
        self.follow()
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
        conn.raw.execute("SET LOCAL search_path TO public")  # shared tables always live in public
        conn.raw.execute(SCHEMA)
        # execution modes became stage + approval (paper_/live_ x manual/auto)
        conn.raw.execute("UPDATE strategies SET mode='live_manual' WHERE status='live' AND mode IN ('manual','auto','paper')")
        conn.raw.execute("UPDATE strategies SET mode='paper_manual' WHERE mode='manual'")
        conn.raw.execute("UPDATE strategies SET mode='paper_auto' WHERE mode IN ('auto','paper')")


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
    for r in conn.raw.execute("SELECT nspname FROM pg_namespace WHERE nspname ~ '^acct_[0-9]+$'").fetchall():
        conn.raw.execute(f"DROP SCHEMA {r['nspname']} CASCADE")
    conn.raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.raw.execute("SET search_path TO public")
    conn.scope = None
    unpin()
