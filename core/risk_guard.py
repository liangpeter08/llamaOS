"""Hard risk checks for order intents. Pure functions: no I/O, no models.

Violations are rejected, never clamped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from common import timeutil as T
from common.config import RiskConfig


@dataclass
class AccountState:
    equity: float
    start_equity: float
    gross_exposure: float  # $ notional of open positions
    open_symbols: set[str]
    trades_today: int
    strategy_trades_today: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False
    feed_age_s: float = 0.0

    @property
    def day_pnl(self) -> float:
        return self.equity - self.start_equity


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self):
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def daily_loss_breached(state: AccountState, risk: RiskConfig) -> bool:
    return state.day_pnl <= -risk.max_daily_loss * state.start_equity


def check_intent(intent, state: AccountState, risk: RiskConfig, strategy_limits: dict | None = None,
                 adv: float | None = None) -> list[Check]:
    """intent needs: sid, symbol, side, qty, entry, stop, target, ts, risk_usd."""
    lim = strategy_limits or {}
    eq = max(state.equity, 1e-9)
    notional = intent.qty * intent.entry
    risk_pct = intent.risk_usd / eq
    max_pos = min(risk.max_position, lim.get("max_position", risk.max_position))
    max_open = min(risk.max_open_positions, lim.get("max_open_positions", risk.max_open_positions))
    strat_trades = state.strategy_trades_today.get(intent.sid, 0)
    strat_cap = lim.get("max_trades_per_day")
    checks = [
        Check("kill_switch_off", not state.kill_switch, "kill switch is active" if state.kill_switch else "off"),
        Check("feed_fresh", state.feed_age_s <= risk.stale_feed_halt,
              f"last bar {state.feed_age_s:.0f}s ago (halt at {risk.stale_feed_halt:.0f}s)"),
        Check("daily_loss_ok", not daily_loss_breached(state, risk),
              f"day P&L {state.day_pnl:+,.2f} vs limit -{risk.max_daily_loss * state.start_equity:,.2f}"),
        Check("entry_window", T.in_window(intent.ts, risk.entries),
              f"{T.fmt(intent.ts)} within {risk.entries[0]:%H:%M}-{risk.entries[1]:%H:%M}"),
        Check("side_allowed", intent.side == "long" or risk.allow_short, f"side={intent.side}"),
        Check("not_already_open", intent.symbol not in state.open_symbols, f"open: {sorted(state.open_symbols) or 'none'}"),
        Check("max_open_positions", len(state.open_symbols) < max_open, f"{len(state.open_symbols)} open / max {max_open}"),
        Check("max_trades_per_day", state.trades_today < risk.max_trades_per_day,
              f"{state.trades_today} today / max {risk.max_trades_per_day}"),
        Check("strategy_trades_per_day", strat_cap is None or strat_trades < strat_cap,
              f"{strat_trades} today / max {strat_cap if strat_cap is not None else '—'}"),
        Check("qty_positive", intent.qty >= 1, f"qty={intent.qty}"),
        Check("bracket", (not risk.require_bracket) or (intent.stop is not None and intent.target is not None),
              f"stop={intent.stop} target={intent.target}"),
        Check("stop_side", _stop_ok(intent), f"entry={intent.entry:.2f} stop={intent.stop} target={intent.target}"),
        Check("max_risk", risk_pct <= risk.max_risk + 1e-12, f"{risk_pct:.3%} of equity / max {risk.max_risk:.2%}"),
        Check("max_position", notional <= max_pos * eq + 1e-6, f"${notional:,.0f} = {notional / eq:.1%} / max {max_pos:.0%}"),
        Check("gross_exposure", state.gross_exposure + notional <= risk.max_gross_exposure * eq + 1e-6,
              f"${state.gross_exposure + notional:,.0f} after / max {risk.max_gross_exposure:.2f}x equity"),
        Check("min_price", intent.entry >= risk.min_price, f"{intent.entry:.2f} / min {risk.min_price:.2f}"),
        Check("min_adv", adv is not None and adv >= risk.min_adv,
              f"ADV(20) ${adv:,.0f} / min ${risk.min_adv:,.0f}" if adv is not None else "ADV unknown (fails closed)"),
    ]
    return checks


def _stop_ok(intent) -> bool:
    if intent.stop is None or intent.target is None:
        return False
    if intent.side == "long":
        return intent.stop < intent.entry < intent.target
    return intent.target < intent.entry < intent.stop


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def drift_check(intent_price: float, now_price: float, risk: RiskConfig) -> Check:
    drift = abs(now_price / intent_price - 1)
    return Check("price_drift", drift <= risk.max_drift,
                 f"{intent_price:.2f} -> {now_price:.2f} = {drift:.2%} / max {risk.max_drift:.2%}")
