"""Paths, environment and typed views of the YAML config files."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

from .units import parse_duration, parse_number, parse_pct, parse_window, parse_hhmm

ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    v = os.environ.get(env)
    return Path(v) if v else default


CONFIG_DIR = _path("CONFIG_DIR", ROOT / "config")
DATA_DIR = _path("DATA_DIR", ROOT / "data")
STRATEGIES_DIR = _path("STRATEGIES_DIR", ROOT / "strategies")
PROMPTS_DIR = ROOT / "prompts"


def risk_path() -> Path:
    return _path("RISK_FILE", CONFIG_DIR / "risk.yaml")


def mode() -> str:
    """record_only -> paper -> live. The prototype only ever trades the simulator."""
    value = os.environ.get("MODE", "paper")
    if value not in ("paper", "record_only"):
        raise ValueError("MODE must be paper or record_only; real-money execution is disabled")
    return value


@dataclass(frozen=True)
class PortfolioRisk:
    """Limits for `kind: portfolio` strategies (multi-day sleeves). Absent -> those strategies are refused."""
    max_allocation: float
    max_position: float
    max_symbols: int
    max_orders_per_day: int
    max_drawdown: float
    max_hold_sessions: int
    decide_at: time
    min_price: float
    min_adv: float


@dataclass(frozen=True)
class FundsRisk:
    """Account-wide money controls. Per-strategy allocations (set on the dashboard) must fit inside these."""
    cash_reserve: float
    max_deployed: float
    intraday_budget: float
    max_strategy_allocation: float
    default_allocation: float
    strategy_max_drawdown: float
    strategy_daily_loss: float
    account_max_drawdown: float


@dataclass(frozen=True)
class RiskConfig:
    # account
    max_gross_exposure: float
    allow_short: bool
    max_open_positions: int
    max_trades_per_day: int
    max_daily_loss: float
    # per trade
    default_risk: float
    max_risk: float
    max_position: float
    require_bracket: bool
    # execution
    entry_order: str
    max_slippage: float
    cancel_unfilled_after: float
    approval_expiry: float
    max_drift: float
    # market
    entries: tuple[time, time]
    flatten_by: time
    min_price: float
    min_adv: float
    stale_feed_halt: float
    reconcile_every: float
    portfolio: PortfolioRisk | None = None
    funds: FundsRisk | None = None
    raw: str = field(repr=False, default="")
    sha256: str = ""


def risk_defaults(path: Path | None = None) -> tuple[dict, str]:
    """Factory defaults: config/risk.yaml (mapping and raw text)."""
    path = path or risk_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/risk.example.yaml to config/risk.yaml and review it."
        )
    raw = path.read_text()
    return yaml.safe_load(raw) or {}, raw


def load_risk(path: Path | None = None, conn=None) -> RiskConfig:
    """Effective risk controls: the owner's values in Postgres (risk_settings) over the factory defaults."""
    from . import db, risk_settings as RS
    base, raw = risk_defaults(path)
    over = {k: v["value"] for k, v in RS.overrides(conn or db.get()).items()}
    return build_risk(RS.merged(base, over), raw)


def build_risk(d: dict, raw: str = "") -> RiskConfig:
    a, p, e, m = d["account"], d["per_trade"], d["execution"], d["market"]
    cfg = RiskConfig(
        max_gross_exposure=parse_number(a["max_gross_exposure"]),
        allow_short=bool(a["allow_short"]),
        max_open_positions=int(a["max_open_positions"]),
        max_trades_per_day=int(a["max_trades_per_day"]),
        max_daily_loss=parse_pct(a["max_daily_loss"]),
        default_risk=parse_pct(p["default_risk"]),
        max_risk=parse_pct(p["max_risk"]),
        max_position=parse_pct(p["max_position"]),
        require_bracket=bool(p["require_bracket"]),
        entry_order=str(e["entry_order"]),
        max_slippage=parse_pct(e["max_slippage"]),
        cancel_unfilled_after=parse_duration(e["cancel_unfilled_after"]),
        approval_expiry=parse_duration(e["approval_expiry"]),
        max_drift=parse_pct(e["max_drift"]),
        entries=parse_window(m["entries"]),
        flatten_by=parse_hhmm(m["flatten_by"]),
        min_price=parse_number(m["min_price"]),
        min_adv=parse_number(m["min_adv"]),
        stale_feed_halt=parse_duration(m["stale_feed_halt"]),
        reconcile_every=parse_duration(m["reconcile_every"]),
        portfolio=_portfolio(d.get("portfolio")),
        funds=_funds(d.get("funds")),
        raw=raw,
        sha256=hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest(),
    )
    if cfg.default_risk > cfg.max_risk:
        raise ValueError("default risk per trade can't exceed max risk per trade")
    if cfg.flatten_by <= cfg.entries[1]:
        raise ValueError("flatten_by must be after the end of the entry window")
    if cfg.portfolio and not cfg.entries[0] <= cfg.portfolio.decide_at < cfg.flatten_by:
        raise ValueError("portfolio decide_at must be inside the trading day, before flatten_by")
    return cfg


def _portfolio(p: dict | None) -> PortfolioRisk | None:
    if not p or not p.get("enabled", True):
        return None
    return PortfolioRisk(
        max_allocation=parse_pct(p["max_allocation"]),
        max_position=parse_pct(p["max_position"]),
        max_symbols=int(p["max_symbols"]),
        max_orders_per_day=int(p["max_orders_per_day"]),
        max_drawdown=parse_pct(p["max_drawdown"]),
        max_hold_sessions=int(p["max_hold_sessions"]),
        decide_at=parse_hhmm(p["decide_at"]),
        min_price=parse_number(p["min_price"]),
        min_adv=parse_number(p["min_adv"]),
    )


def _funds(f: dict | None) -> FundsRisk | None:
    if not f:
        return None
    out = FundsRisk(**{k: parse_pct(f[k]) for k in FundsRisk.__dataclass_fields__})
    if out.intraday_budget > out.max_deployed:
        raise ValueError("intraday budget can't exceed max deployed capital")
    if out.cash_reserve + out.max_deployed > 1.0 + 1e-9:
        raise ValueError("cash reserve + max deployed capital must not exceed 100% of equity")
    if out.default_allocation > out.max_strategy_allocation:
        raise ValueError("default allocation can't exceed the max allocation per strategy")
    return out


def load_yaml(name: str) -> dict:
    p = CONFIG_DIR / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def universe() -> list[str]:
    if os.environ.get("BROKER_BACKEND", "simulator") == "alpaca":
        from common import db, monitoring
        c = db.get()
        return sorted(set(monitoring.enabled(c)) | set(monitoring.held(c)))
    name = "universe.yaml" if os.environ.get("BROKER_BACKEND", "simulator") == "alpaca" else "universe.sim.yaml"
    return list(load_yaml(name).get("watchlist", []))
