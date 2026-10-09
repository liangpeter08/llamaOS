"""Simulated exchange: one fill model shared by the live simulator and the backtester.

Fill rules (dsl rule 7):
  * Orders decided at bar i's close are worked from bar i+1.
  * Entry = marketable limit: fills at the next open plus slippage if that is within the
    limit, else at the limit if the bar trades through it; cancelled after N bars unfilled.
  * Stop and target are a bracket held "at the broker", active from the fill bar onward.
    If both are touched in one bar, the stop is assumed to fill first (conservative).
  * Market exits (exit.when_any, flatten, kill switch) fill at the next open minus slippage,
    or at the current close when flattening at end of day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count


@dataclass
class Order:
    id: int
    symbol: str
    side: str  # buy | sell
    qty: int
    kind: str  # entry | exit
    submitted_index: int
    limit: float | None = None
    stop: float | None = None
    target: float | None = None
    intent_ref: object = None
    reason: str = ""
    status: str = "working"  # working | filled | cancelled
    fill_price: float | None = None
    fill_ts: int | None = None
    reported: bool = False


@dataclass
class Pos:
    symbol: str
    side: str  # long | short
    qty: int
    entry_price: float
    stop: float
    target: float
    entry_ts: int
    entry_index: int
    intent_ref: object = None
    last: float = 0.0

    @property
    def signed(self) -> int:
        return self.qty if self.side == "long" else -self.qty


@dataclass
class Fill:
    symbol: str
    side: str
    qty: int
    price: float
    ts: int
    kind: str  # entry | stop | target | exit | flatten | kill
    order: Order | None = None
    trade: dict | None = None


@dataclass
class SimExchange:
    cash: float
    slippage: float = 0.0002
    cancel_after_bars: int = 2
    orders: list[Order] = field(default_factory=list)
    positions: dict[str, Pos] = field(default_factory=dict)
    _ids: count = field(default_factory=lambda: count(1))

    # ---------------------------------------------------------------- orders
    def submit_entry(self, symbol: str, side: str, qty: int, limit: float, stop: float, target: float,
                     index: int, intent_ref=None) -> Order:
        o = Order(next(self._ids), symbol, "buy" if side == "long" else "sell", qty, "entry", index,
                  limit=limit, stop=stop, target=target, intent_ref=intent_ref)
        self.orders.append(o)
        return o

    def submit_exit(self, symbol: str, index: int, reason: str) -> Order | None:
        p = self.positions.get(symbol)
        if not p or any(o.symbol == symbol and o.kind == "exit" and o.status == "working" for o in self.orders):
            return None
        o = Order(next(self._ids), symbol, "sell" if p.side == "long" else "buy", p.qty, "exit", index, reason=reason)
        self.orders.append(o)
        return o

    def cancel_entries(self, symbol: str | None = None) -> list[Order]:
        out = []
        for o in self.orders:
            if o.status == "working" and o.kind == "entry" and (symbol is None or o.symbol == symbol):
                o.status, o.reported = "cancelled", True
                out.append(o)
        return out

    def working(self, symbol: str | None = None) -> list[Order]:
        return [o for o in self.orders if o.status == "working" and (symbol is None or o.symbol == symbol)]

    # ---------------------------------------------------------------- bars
    def on_bar(self, symbol: str, index: int, ts: int, o: float, h: float, l: float, c: float) -> list[Fill]:
        """Process bar `index` for `symbol`; returns fills that happened during it."""
        fills: list[Fill] = []
        for od in [x for x in self.orders if x.symbol == symbol and x.status == "working"]:
            if od.submitted_index >= index:
                continue  # decided at this bar's close or later: works from the next bar
            if od.kind == "exit":
                px = o * (1 - self.slippage) if od.side == "sell" else o * (1 + self.slippage)
                fills.append(self._close(symbol, px, ts, od.reason or "exit", od))
                continue
            px = None
            if od.side == "buy":
                if o * (1 + self.slippage) <= od.limit:
                    px = o * (1 + self.slippage)
                elif l <= od.limit:
                    px = od.limit
            else:
                if o * (1 - self.slippage) >= od.limit:
                    px = o * (1 - self.slippage)
                elif h >= od.limit:
                    px = od.limit
            if px is None:
                if index - od.submitted_index >= self.cancel_after_bars:
                    od.status = "cancelled"
                continue
            od.status, od.fill_price, od.fill_ts = "filled", px, ts
            side = "long" if od.side == "buy" else "short"
            self.cash -= px * od.qty if side == "long" else -px * od.qty
            self.positions[symbol] = Pos(symbol, side, od.qty, px, od.stop, od.target, ts, index, od.intent_ref, c)
            fills.append(Fill(symbol, od.side, od.qty, px, ts, "entry", od))

        p = self.positions.get(symbol)
        if p:
            stop_hit = l <= p.stop if p.side == "long" else h >= p.stop
            tgt_hit = h >= p.target if p.side == "long" else l <= p.target
            fresh = p.entry_ts == ts  # opened at this bar's open
            if stop_hit:
                if p.side == "long":
                    px = min(o, p.stop) if not fresh else p.stop
                    px *= (1 - self.slippage)
                else:
                    px = max(o, p.stop) if not fresh else p.stop
                    px *= (1 + self.slippage)
                fills.append(self._close(symbol, px, ts, "stop"))
            elif tgt_hit:
                if p.side == "long":
                    px = max(o, p.target) if not fresh else p.target
                else:
                    px = min(o, p.target) if not fresh else p.target
                fills.append(self._close(symbol, px, ts, "target"))
            else:
                p.last = c
        return fills

    def flatten_now(self, symbol: str, price: float, ts: int, kind: str) -> Fill | None:
        if symbol not in self.positions:
            return None
        p = self.positions[symbol]
        px = price * (1 - self.slippage) if p.side == "long" else price * (1 + self.slippage)
        return self._close(symbol, px, ts, kind)

    def _close(self, symbol: str, px: float, ts: int, kind: str, order: Order | None = None) -> Fill:
        p = self.positions.pop(symbol)
        for od in self.orders:
            if od.symbol == symbol and od.kind == "exit" and od.status == "working":
                od.status, od.fill_price, od.fill_ts = "filled", px, ts
        self.cash += px * p.qty if p.side == "long" else -px * p.qty
        pnl = (px - p.entry_price) * p.signed
        rps = abs(p.entry_price - p.stop)
        trade = {
            "symbol": symbol, "side": p.side, "qty": p.qty, "entry_ts": p.entry_ts, "entry_price": p.entry_price,
            "exit_ts": ts, "exit_price": px, "exit_kind": kind, "pnl": pnl,
            "r_multiple": (pnl / (rps * p.qty)) if rps > 0 else None, "intent_ref": p.intent_ref,
            "stop": p.stop, "target": p.target,
        }
        return Fill(symbol, "sell" if p.side == "long" else "buy", p.qty, px, ts, kind, order, trade)

    # ---------------------------------------------------------------- account
    def mark(self, symbol: str, price: float):
        if symbol in self.positions:
            self.positions[symbol].last = price

    def equity(self) -> float:
        return self.cash + sum(p.signed * p.last for p in self.positions.values())

    def gross_exposure(self) -> float:
        return sum(abs(p.qty * p.last) for p in self.positions.values())
