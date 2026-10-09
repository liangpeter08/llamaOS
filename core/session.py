"""One trading session: bars in -> fills, exits, intents -> risk guard -> approval gate.

The live broker and the backtester both drive this class, so a strategy makes the same
decisions in replay as it does live. The only difference is the `gate` callback: the
backtester submits straight away, the live broker may hold intents for manual approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from common import timeutil as T
from common.config import RiskConfig
from dsl.evaluator import DayData, SignalBook
from dsl.validator import Strategy

from . import risk_guard as G
from .engine import Intent, StrategyRunner
from .exchange import Fill, SimExchange

SUBMIT, HOLD, REJECT = "submit", "hold", "reject"


@dataclass
class Event:
    kind: str  # intent | rejected | fill | trade | exit_request | kill | flatten | cancelled
    data: dict = field(default_factory=dict)
    obj: object = None


class TradingSession:
    def __init__(self, risk: RiskConfig, strategies: list[Strategy], days: dict[str, DayData],
                 signals: SignalBook, cash: float, slippage: float = 0.0002,
                 gate: Callable[[Strategy, Intent, list[G.Check]], str] | None = None):
        self.risk = risk
        self.days = days
        self.signals = signals
        self.strategies = {s.hash: s for s in strategies}
        self.runners = {s.hash: StrategyRunner(s, risk) for s in strategies}
        for r in self.runners.values():
            r.start_day(days, signals)
        self.exchange = SimExchange(cash=cash, slippage=slippage,
                                    cancel_after_bars=max(1, int(risk.cancel_unfilled_after // 60)))
        self.start_equity = cash
        self.gate = gate or (lambda s, it, ch: SUBMIT)
        self.owner: dict[str, str] = {}  # symbol -> strategy hash holding an order/position/pending intent
        self.pending: dict[str, Intent] = {}  # symbol -> intent awaiting approval
        self.trades_today = 0
        self.strat_trades: dict[str, int] = {}
        self.kill = False
        self.kill_reason = ""
        self.halted_entries = False
        self.feed_age_s = 0.0
        self.last_index = -1
        self.disabled: set[str] = set()  # strategies that may still exit but take no new entries
        self.labels: dict[str, str] = {s.hash: label(s) for s in strategies}
        self.realized: dict[str, float] = {}  # strategy label -> realized P&L this session

    def add_strategy(self, s: Strategy):
        self.disabled.discard(s.hash)
        if s.hash in self.runners:
            return
        r = StrategyRunner(s, self.risk)
        r.start_day(self.days, self.signals)
        self.labels[s.hash] = label(s)
        self.strategies[s.hash] = s
        self.runners[s.hash] = r

    def remove_strategy(self, h: str):
        if h in self.owner.values():
            self.disabled.add(h)  # keep managing its open position/order
            return
        self.runners.pop(h, None)
        self.strategies.pop(h, None)
        self.disabled.discard(h)

    # ---------------------------------------------------------------- state
    def account(self) -> G.AccountState:
        open_syms = set(self.exchange.positions) | {o.symbol for o in self.exchange.working() if o.kind == "entry"}
        exposure = self.exchange.gross_exposure() + sum(
            o.qty * (o.limit or 0) for o in self.exchange.working() if o.kind == "entry")
        return G.AccountState(
            equity=self.exchange.equity(), start_equity=self.start_equity, gross_exposure=exposure,
            open_symbols=open_syms, trades_today=self.trades_today,
            strategy_trades_today=dict(self.strat_trades), kill_switch=self.kill, feed_age_s=self.feed_age_s,
        )

    def pnl_by_strategy(self) -> dict[str, float]:
        """Session P&L per strategy: realized plus open positions marked to the last bar."""
        out = {lbl: 0.0 for lbl in self.labels.values()}
        for lbl, v in self.realized.items():
            out[lbl] = out.get(lbl, 0.0) + v
        for p in self.exchange.positions.values():
            ref = p.intent_ref
            if ref is not None:
                lbl = self.labels.get(ref.strategy_hash, ref.sid)
                out[lbl] = out.get(lbl, 0.0) + (p.last - p.entry_price) * p.signed
        return out

    def adv20(self, symbol: str) -> float | None:
        h = self.days[symbol].history[-20:]
        return sum(x.c * x.v for x in h) / 20 if len(h) == 20 else None

    def last_price(self, symbol: str) -> float | None:
        d = self.days.get(symbol)
        return d.c[-1] if d and len(d) else None

    # ---------------------------------------------------------------- per bar
    def step(self, i: int) -> list[Event]:
        """Process bar i (already appended to every DayData) for all symbols."""
        events: list[Event] = []
        self.last_index = i
        syms = [s for s, d in self.days.items() if len(d) > i]
        # 1. the exchange works orders against this bar
        for sym in syms:
            d = self.days[sym]
            for f in self.exchange.on_bar(sym, i, d.ts[i], d.o[i], d.h[i], d.l[i], d.c[i]):
                events += self._on_fill(f, i)
            for o in [o for o in self.exchange.orders if o.symbol == sym and o.status == "cancelled" and not o.reported]:
                o.reported = True
                self.owner.pop(sym, None)
                events.append(Event("cancelled", {"symbol": sym, "order_id": o.id, "reason": "unfilled"}, o))
            self.exchange.mark(sym, d.c[i])

        close_ts = self.days[syms[0]].close_ts(i) if syms else 0
        now_t = T.et(close_ts).time() if syms else None

        # 2. hard account rules
        st = self.account()
        if not self.kill and G.daily_loss_breached(st, self.risk):
            events += self.kill_switch(i, f"daily loss {st.day_pnl:+,.2f} breached -{self.risk.max_daily_loss:.2%}")
        if now_t and now_t >= self.risk.flatten_by:
            for sym in list(self.exchange.positions):
                f = self.exchange.flatten_now(sym, self.days[sym].c[i], close_ts, "flatten")
                if f:
                    events += self._on_fill(f, i)
            for o in self.exchange.cancel_entries():
                self.owner.pop(o.symbol, None)
            self.halted_entries = True

        # 3. strategy exits, then entries
        for h, runner in self.runners.items():
            for sym in syms:
                if self.owner.get(sym) == h and sym in self.exchange.positions:
                    req = runner.check_exit(sym, i)
                    if req and self.exchange.submit_exit(sym, i, req.reason):
                        events.append(Event("exit_request", {"symbol": sym, "sid": runner.s.id, "reason": req.reason,
                                                             "decision": req.decision}, req))
        if self.kill or self.halted_entries:
            return events
        for h, runner in list(self.runners.items()):
            if h in self.disabled:
                continue
            for sym in syms:
                if sym in self.owner:
                    continue
                intent = runner.check_entry(sym, i, self.exchange.equity(), busy=False)
                if not intent:
                    continue
                checks = G.check_intent(intent, self.account(), self.risk, runner.s.limits, self.adv20(sym))
                intent_guard = [c.as_dict() for c in checks]
                if not G.passed(checks):
                    runner.reject(sym, i)
                    events.append(Event("rejected", {"intent": intent, "guard": intent_guard, "by": "risk_guard"}, intent))
                    continue
                verdict = self.gate(runner.s, intent, checks)
                if verdict == SUBMIT:
                    self.submit(intent, i)
                    events.append(Event("intent", {"intent": intent, "guard": intent_guard, "status": "submitted"}, intent))
                elif verdict == HOLD:
                    self.owner[sym] = h
                    self.pending[sym] = intent
                    events.append(Event("intent", {"intent": intent, "guard": intent_guard, "status": "pending"}, intent))
                else:
                    runner.reject(sym, i)
                    events.append(Event("rejected", {"intent": intent, "guard": intent_guard, "by": "gate"}, intent))
        return events

    # ---------------------------------------------------------------- actions
    def submit(self, intent: Intent, i: int):
        self.pending.pop(intent.symbol, None)
        self.owner[intent.symbol] = intent.strategy_hash
        self.trades_today += 1
        self.strat_trades[intent.sid] = self.strat_trades.get(intent.sid, 0) + 1
        self.runners[intent.strategy_hash].trades_today += 1
        return self.exchange.submit_entry(intent.symbol, intent.side, intent.qty, intent.limit, intent.stop,
                                          intent.target, i, intent_ref=intent)

    def recheck(self, intent: Intent) -> list[G.Check]:
        """Re-run the risk guard plus a price-drift check before submitting an approved intent."""
        self.pending.pop(intent.symbol, None)
        self.owner.pop(intent.symbol, None)
        runner = self.runners[intent.strategy_hash]
        checks = G.check_intent(intent, self.account(), self.risk, runner.s.limits, self.adv20(intent.symbol))
        # the entry window is judged at decision time; approval latency doesn't re-test it
        checks = [c for c in checks if c.name != "entry_window"]
        px = self.last_price(intent.symbol)
        checks.append(G.drift_check(intent.entry, px, self.risk) if px else G.Check("price_drift", False, "no price"))
        if self.halted_entries:
            checks.append(G.Check("entries_open", False, "past flatten_by"))
        return checks

    def release(self, intent: Intent):
        """Drop a pending intent (rejected, expired) so the symbol can trade again."""
        self.pending.pop(intent.symbol, None)
        if self.owner.get(intent.symbol) == intent.strategy_hash and intent.symbol not in self.exchange.positions:
            self.owner.pop(intent.symbol, None)
        self.runners[intent.strategy_hash].reject(intent.symbol, self.last_index)

    def kill_switch(self, i: int, reason: str) -> list[Event]:
        """Cancel all orders, close all positions, block new entries."""
        events = [Event("kill", {"reason": reason})]
        self.kill, self.kill_reason = True, reason
        for o in self.exchange.cancel_entries():
            events.append(Event("cancelled", {"symbol": o.symbol, "order_id": o.id, "reason": "kill switch"}, o))
        for sym in list(self.exchange.positions):
            d = self.days[sym]
            idx = min(i, len(d) - 1)
            f = self.exchange.flatten_now(sym, d.c[idx], d.close_ts(idx), "kill")
            if f:
                events += self._on_fill(f, i)
        for sym, it in list(self.pending.items()):
            self.release(it)
        self.owner.clear()
        return events

    def end_of_day(self) -> list[Event]:
        events = []
        for sym in list(self.exchange.positions):
            d = self.days[sym]
            f = self.exchange.flatten_now(sym, d.c[-1], d.close_ts(len(d) - 1), "flatten")
            if f:
                events += self._on_fill(f, len(d) - 1)
        return events

    # ---------------------------------------------------------------- fills
    def _on_fill(self, f: Fill, i: int) -> list[Event]:
        events = [Event("fill", {"symbol": f.symbol, "side": f.side, "qty": f.qty, "price": f.price,
                                 "ts": f.ts, "kind": f.kind}, f)]
        intent = f.order.intent_ref if f.order is not None else None
        if f.kind == "entry" and intent is not None:
            self.runners[intent.strategy_hash].set_position(f.symbol, intent.side, f.price, intent.stop, i)
        if f.trade is not None:
            ref = f.trade.get("intent_ref")
            if ref is not None:
                if ref.strategy_hash in self.runners:
                    self.runners[ref.strategy_hash].clear_position(f.symbol)
                lbl = self.labels.get(ref.strategy_hash, ref.sid)
                self.realized[lbl] = self.realized.get(lbl, 0.0) + f.trade["pnl"]
            self.owner.pop(f.symbol, None)
            events.append(Event("trade", f.trade, f))
        return events

    def strategy_for(self, intent: Intent) -> Strategy:
        return self.strategies[intent.strategy_hash]


def label(s: Strategy) -> str:
    return f"{s.id} v{s.version}"
