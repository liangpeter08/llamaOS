"""Paths, environment and typed views of the YAML config files."""
from __future__ import annotations

import hashlib
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
    raw: str = field(repr=False, default="")
    sha256: str = ""


def load_risk(path: Path | None = None) -> RiskConfig:
    path = path or risk_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/risk.example.yaml to config/risk.yaml and review it."
        )
    raw = path.read_text()
    d = yaml.safe_load(raw)
    a, p, e, m = d["account"], d["per_trade"], d["execution"], d["market"]
    return RiskConfig(
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
        raw=raw,
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )


def load_yaml(name: str) -> dict:
    p = CONFIG_DIR / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def universe() -> list[str]:
    name = "universe.yaml" if os.environ.get("BROKER_BACKEND", "simulator") == "alpaca" else "universe.sim.yaml"
    return list(load_yaml(name).get("watchlist", []))
