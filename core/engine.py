"""Rule engine: turns bar closes into order intents and exit requests. No models here.

Shared by the live broker loop and the backtester so both make identical decisions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections import Counter

from common import timeutil as T
from common.config import RiskConfig
from dsl.evaluator import DayData, Evaluator, Position, SignalBook
from dsl.validator import Strategy

REJECT_COOLDOWN_BARS = 15


@dataclass
class Intent:
    strategy_hash: str
    sid: str
    symbol: str
    side: str
    ts: int  # bar close (decision time)
    index: int
    entry: float
    limit: float
    stop: float
    target: float
    qty: int
    risk_usd: float
    risk_pct: float
    decision: list[dict]
    universe: list[dict]
    signals: dict
    id: int | None = None


@dataclass
class ExitRequest:
    sid: str
    symbol: str
    index: int
    ts: int
    reason: str  # when_any | flatten_at
    decision: list[dict] = field(default_factory=list)


class StrategyRunner:
    def __init__(self, strat: Strategy, risk: RiskConfig):
        self.s = strat
        self.risk = risk
        self.evals: dict[str, Evaluator] = {}
        self.cooldown: dict[str, int] = {}
        self.trades_today = 0
        self.diagnostics: Counter | None = None
        self.failed_conditions: Counter | None = None

    def start_day(self, days: dict[str, DayData], signals: SignalBook, refs: dict[str, DayData] | None = None):
        """refs: reference-only symbols (read by peer() and friends, never traded); traded symbols are peers too."""
        peers = {**(refs or {}), **days}
        self.evals = {sym: Evaluator(self.s, d, signals, peers) for sym, d in days.items()}
        self.cooldown.clear()
        self.trades_today = 0

    def set_position(self, symbol: str, side: str, entry_price: float, stop: float, index: int):
        self.evals[symbol].position = Position(side, entry_price, stop, index)

    def clear_position(self, symbol: str):
        if symbol in self.evals:
            self.evals[symbol].position = None

    def reject(self, symbol: str, index: int):
        self.cooldown[symbol] = index + REJECT_COOLDOWN_BARS

    # ---------------------------------------------------------------- entries
    def check_entry(self, symbol: str, i: int, equity: float, busy: bool, max_notional: float | None = None) -> Intent | None:
        """busy = this symbol already has a position, working order or pending intent.
        max_notional = money left in this strategy's fund allocation (sizes the entry down to fit; None = no fund limit)."""
        ev = self.evals.get(symbol)
        if self.s.universe_symbols and symbol not in self.s.universe_symbols:
            return None  # not this strategy's ticker: not an evaluation at all
        def blocked(reason):
            if self.diagnostics is not None:
                self.diagnostics[reason] += 1
            return None
        if self.diagnostics is not None:
            self.diagnostics['evaluations'] += 1
        if ev is None or busy or i < self.cooldown.get(symbol, -1):
            return blocked('cooldown_or_busy')
        s, d = self.s, ev.d
        close_ts = d.close_ts(i)
        if not T.in_window(close_ts, s.entry_window):
            return blocked('outside_entry_window')
        cap = s.limits.get("max_trades_per_day")
        if cap is not None and self.trades_today >= cap:
            return blocked('strategy_trade_cap')
        ok, uni = ev.in_universe(i)
        if not ok:
            if self.failed_conditions is not None:
                self.failed_conditions.update(r['cond'] + (' (missing value)' if r.get('missing') else '') for r in uni if not r['ok'])
            return blocked('universe_filtered')
        decision = [ev.explain(c, i) for c in s.entry_when_all]
        if not all(r["ok"] for r in decision):
            if self.failed_conditions is not None:
                self.failed_conditions.update(r['cond'] + (' (missing value)' if r.get('missing') else '') for r in decision if not r['ok'])
            return blocked('entry_conditions_false')

        entry = d.c[i]
        side = s.side
        ev.position = Position(side, entry, entry, i)  # provisional, for stop/target expressions
        try:
            stop = ev.value(s.exit_stop, i) if s.exit_stop is not None else None
            if stop is None:
                return blocked('stop_unavailable')
            ev.position = Position(side, entry, stop, i)
            target = ev.value(s.exit_target, i) if s.exit_target is not None else None
        finally:
            ev.position = None
        if target is None:
            return blocked('target_unavailable')
        rps = abs(entry - stop)
        if rps <= 0 or (side == "long" and not stop < entry < target) or (side == "short" and not target < entry < stop):
            return blocked('invalid_bracket')
        qty = math.floor(equity * s.risk_per_trade / rps)
        max_pos = min(self.risk.max_position, s.limits.get("max_position", self.risk.max_position))
        qty = min(qty, math.floor(max_pos * equity / entry))
        if max_notional is not None:
            qty = min(qty, math.floor(max_notional / (entry * (1 + self.risk.max_slippage))))
        if qty < 1:
            return blocked('size_below_one_share')
        if self.diagnostics is not None:
            self.diagnostics['intents_generated'] += 1
        limit = entry * (1 + self.risk.max_slippage) if side == "long" else entry * (1 - self.risk.max_slippage)
        sigs = {name: ev._signal(name, i) for name in s.signals}
        return Intent(
            strategy_hash=s.hash, sid=s.id, symbol=symbol, side=side, ts=close_ts, index=i,
            entry=round(entry, 4), limit=round(limit, 4), stop=round(stop, 4), target=round(target, 4),
            qty=qty, risk_usd=round(qty * rps, 2), risk_pct=qty * rps / equity,
            decision=decision, universe=uni, signals=sigs,
        )

    # ---------------------------------------------------------------- exits
    def check_exit(self, symbol: str, i: int) -> ExitRequest | None:
        ev = self.evals.get(symbol)
        if ev is None or ev.position is None:
            return None
        d = ev.d
        close_ts = d.close_ts(i)
        if T.et(close_ts).time() >= self.s.flatten_at:
            return ExitRequest(self.s.id, symbol, i, close_ts, "flatten_at",
                               [{"cond": f"time >= {self.s.flatten_at:%H:%M}", "ok": True, "value": T.fmt(close_ts)}])
        if self.s.exit_when_any:
            recs = [ev.explain(c, i) for c in self.s.exit_when_any]
            if any(r["ok"] for r in recs):
                return ExitRequest(self.s.id, symbol, i, close_ts, "when_any", recs)
        return None
