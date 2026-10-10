"""Risk controls as Postgres values the owner edits on the dashboard (Risk page).

Each control has a catalog entry: section, type, sanity bounds, default and a one-line
explanation. The effective value is the owner's setting from `risk_settings` when there is
one, otherwise the factory default (config/risk.yaml for the original sections, the catalog
for portfolio and funds). Every change is validated as a whole config, written to
`risk_settings_history` and the audit log, and the broker reloads it within seconds.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .units import is_pct, parse_duration, parse_hhmm, parse_number, parse_pct, parse_window


@dataclass(frozen=True)
class Control:
    key: str  # section.name
    type: str  # pct | number | int | bool | hhmm | window | duration | choice
    lo: float | None
    hi: float | None
    default: object  # None = take it from risk.yaml
    label: str
    help: str
    choices: tuple = ()

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.key.split(".", 1)[1]


C = Control
CATALOG: list[Control] = [
    C("account.max_gross_exposure", "number", 0, 2, None, "Max gross exposure", "x equity across all positions; 1.0 = no leverage"),
    C("account.allow_short", "bool", None, None, None, "Allow shorting", "intraday strategies may sell short"),
    C("account.max_open_positions", "int", 0, 50, None, "Max open intraday positions", "portfolio sleeves don't count"),
    C("account.max_trades_per_day", "int", 0, 500, None, "Max intraday trades per day", "account-wide"),
    C("account.max_daily_loss", "pct", 0, 0.5, None, "Max daily loss (intraday)", "of equity -> kill switch; sleeve P&L excluded"),
    C("per_trade.default_risk", "pct", 0, 0.05, None, "Default risk per trade", "of equity, when a strategy doesn't set one"),
    C("per_trade.max_risk", "pct", 0, 0.05, None, "Max risk per trade", "of equity between entry and stop"),
    C("per_trade.max_position", "pct", 0, 1, None, "Max intraday position", "notional, of equity"),
    C("per_trade.require_bracket", "bool", None, None, None, "Require bracket", "stop and target held at the broker"),
    C("execution.entry_order", "choice", None, None, None, "Entry order type", "", ("marketable_limit",)),
    C("execution.max_slippage", "pct", 0, 0.02, None, "Max slippage", "limit offset for marketable entries"),
    C("execution.cancel_unfilled_after", "duration", 10, 3600, None, "Cancel unfilled after", "e.g. 2min"),
    C("execution.approval_expiry", "duration", 10, 3600, None, "Approval expiry", "manual intents expire after this, e.g. 90s"),
    C("execution.max_drift", "pct", 0, 0.05, None, "Max drift at approval", "price move allowed between intent and approval"),
    C("market.entries", "window", None, None, None, "Intraday entry window", "ET, e.g. 09:35-15:30"),
    C("market.flatten_by", "hhmm", None, None, None, "Flatten intraday by", "ET; clamped on early closes"),
    C("market.min_price", "number", 0, 10_000, None, "Min price", "$"),
    C("market.min_adv", "number", 0, 1e12, None, "Min 20-day $ volume", "$ average daily dollar volume"),
    C("market.stale_feed_halt", "duration", 30, 3600, None, "Stale feed halt", "no fresh bar for this long -> halt"),
    C("market.reconcile_every", "duration", 10, 3600, None, "Reconcile every", "broker vs local positions"),
    C("portfolio.enabled", "bool", None, None, True, "Portfolio strategies enabled", "multi-day sleeves held overnight"),
    C("portfolio.max_allocation", "pct", 0, 1, "90%", "Max in all sleeves", "market value, of equity"),
    C("portfolio.max_position", "pct", 0, 1, "15%", "Max sleeve position", "one symbol, of equity"),
    C("portfolio.max_symbols", "int", 1, 50, 12, "Max symbols per sleeve", ""),
    C("portfolio.max_orders_per_day", "int", 0, 500, 30, "Max sleeve orders per day", "all sleeves together"),
    C("portfolio.max_drawdown", "pct", 0.01, 1, "15%", "Sleeve drawdown stop", "vs the sleeve's peak value -> sold and stopped"),
    C("portfolio.max_hold_sessions", "int", 1, 520, 130, "Max holding period", "sessions"),
    C("portfolio.decide_at", "hhmm", None, None, "15:45", "Sleeve decision time", "ET, latest allowed; orders go out before the close"),
    C("portfolio.min_price", "number", 0, 10_000, 5, "Sleeve min price", "$"),
    C("portfolio.min_adv", "number", 0, 1e12, 100_000_000, "Sleeve min 20-day $ volume", "$"),
    C("funds.cash_reserve", "pct", 0, 1, "10%", "Cash reserve", "never deployed: buys that would leave less cash are refused"),
    C("funds.max_deployed", "pct", 0, 1, "90%", "Max deployed capital", "positions + working buys, of equity; allocations fit inside"),
    C("funds.intraday_budget", "pct", 0, 1, "40%", "Intraday budget", "all intraday strategies together, of equity"),
    C("funds.max_strategy_allocation", "pct", 0, 1, "90%", "Max allocation per strategy", "of equity"),
    C("funds.default_allocation", "pct", 0, 1, "5%", "Default allocation", "intraday strategies with none set; portfolio: its file's peak"),
    C("funds.strategy_max_drawdown", "pct", 0.01, 1, "25%", "Strategy drawdown stop (cap)", "loss from peak P&L, share of its allocation -> paused"),
    C("funds.strategy_daily_loss", "pct", 0.01, 1, "10%", "Strategy daily loss stop (cap)", "intraday, share of its allocation -> no entries today"),
    C("funds.account_max_drawdown", "pct", 0.01, 1, "20%", "Account drawdown stop", "equity vs its peak -> kill switch"),
]
BY_KEY = {c.key: c for c in CATALOG}
SECTIONS = ("account", "per_trade", "execution", "market", "portfolio", "funds")


def check_value(c: Control, v) -> object:
    """Normalize one value to its stored form; raises ValueError with a readable message."""
    if c.type == "bool":
        if isinstance(v, bool):
            return v
        if str(v).lower() in ("true", "false"):
            return str(v).lower() == "true"
        raise ValueError(f"{c.label}: expected true or false")
    if c.type == "choice":
        if v not in c.choices:
            raise ValueError(f"{c.label}: must be one of {', '.join(c.choices)}")
        return v
    if c.type == "hhmm":
        parse_hhmm(v)
        return str(v).strip()
    if c.type == "window":
        parse_window(v)
        return str(v).strip()
    if c.type == "duration":
        x = parse_duration(v)
    elif c.type == "pct":
        if not is_pct(v):
            raise ValueError(f"{c.label}: write a percent like '10%'")
        x = parse_pct(v)
    elif c.type == "int":
        x = parse_number(v)
        if not float(x).is_integer():
            raise ValueError(f"{c.label}: expected a whole number")
        v = int(x)
    else:
        x = parse_number(v)
        v = x
    if (c.lo is not None and x < c.lo) or (c.hi is not None and x > c.hi):
        lo = f"{c.lo:.0%}" if c.type == "pct" else f"{c.lo:g}"
        hi = f"{c.hi:.0%}" if c.type == "pct" else f"{c.hi:g}"
        raise ValueError(f"{c.label}: must be between {lo} and {hi}")
    return v if c.type in ("pct", "duration") else v


def overrides(conn) -> dict[str, dict]:
    """key -> {value, updated_by, updated_at}; empty if the table doesn't exist yet."""
    import psycopg

    from . import db
    try:
        rows = db.rows(conn, "SELECT key, value, updated_by, updated_at FROM risk_settings")
    except psycopg.errors.UndefinedTable:
        return {}
    return {r["key"]: {**r, "value": json.loads(r["value"])} for r in rows}


def merged(base: dict, over: dict[str, object]) -> dict:
    """risk.yaml mapping + catalog defaults + owner overrides -> one mapping by section."""
    out = {s: dict(base.get(s) or {}) for s in SECTIONS}
    for c in CATALOG:
        if c.key in over:
            out[c.section][c.name] = over[c.key]
        elif c.name not in out[c.section] and c.default is not None:
            out[c.section][c.name] = c.default
    return out


def save(conn, changes: dict[str, object], actor: str, reason: str, base: dict) -> dict:
    """Validate and store owner changes. A value equal to 'default' removes the override."""
    from . import audit, db
    from .config import build_risk

    over = {k: v["value"] for k, v in overrides(conn).items()}
    errors, normalized = [], {}
    for k, v in changes.items():
        c = BY_KEY.get(k)
        if c is None:
            errors.append(f"unknown control {k!r}")
            continue
        if v == "default":
            normalized[k] = None
            continue
        try:
            normalized[k] = check_value(c, v)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return {"ok": False, "errors": errors}
    cand = dict(over)
    for k, v in normalized.items():
        if v is None:
            cand.pop(k, None)
        else:
            cand[k] = v
    try:
        build_risk(merged(base, cand))
    except (ValueError, KeyError) as e:
        return {"ok": False, "errors": [str(e)]}
    now = time.time()
    with db.tx(conn):
        for k, v in normalized.items():
            old = over.get(k)
            if v is None:
                conn.execute("DELETE FROM risk_settings WHERE key=?", (k,))
            else:
                conn.execute("INSERT INTO risk_settings(key, value, updated_by, updated_at) VALUES (?,?,?,?)"
                             " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                             (k, json.dumps(v), actor, now))
            conn.execute("INSERT INTO risk_settings_history(ts, key, old_value, new_value, actor, reason) VALUES (?,?,?,?,?,?)",
                         (now, k, json.dumps(old), json.dumps(v), actor, reason))
        rev = int(db.kv_get(conn, "risk.rev") or 0) + 1
        db.kv_set(conn, "risk.rev", rev)
    audit.log(conn, "risk.changed", actor, {"changes": {k: v for k, v in normalized.items()}, "reason": reason, "rev": rev})
    return {"ok": True, "rev": rev}
