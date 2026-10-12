"""Fund controls: cash reserve, deployed capital and per-strategy allocations. Pure functions.

Every strategy line (its `id`, across versions) has an allocation: a ceiling as a share of equity, or a
fixed dollar amount (its share then moves with equity). `reserved` is the part of that ceiling held for it
even while it is idle (1 = the whole ceiling, 0 = nothing). Reserves add up to at most `funds.max_deployed`,
so a strategy can always spend its reserve. Above its reserve, it draws on a shared pool: whatever of
max deployed is not reserved by, or in use by, other strategies. So a strategy that rarely trades can get a
large ceiling without parking cash, and when it does trade it can never eat into another's reserve.
Each allocation has its own drawdown stop (pauses the strategy) and, for intraday strategies, a daily loss
stop (no new entries until tomorrow), both measured on the ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from common.config import FundsRisk

from .risk_guard import Check


@dataclass
class StrategyFund:
    sid: str
    kind: str  # intraday | portfolio
    allocation: float  # share of equity (for a fixed-dollar allocation: its current share)
    source: str  # set | default
    max_drawdown: float  # share of the allocation
    daily_loss: float | None  # share of the allocation (intraday only)
    status: str = "active"  # active | paused | paused_today
    reason: str = ""
    base_usd: float = 0.0  # allocation in $ when it was last set (drawdown and loss are measured on it)
    base_ts: float = 0.0
    peak_pnl: float = 0.0
    deployed: float = 0.0  # $ in positions + working buys
    pnl: float = 0.0  # since base_ts: realized + open
    pnl_today: float = 0.0
    fixed_usd: float | None = None  # fixed-dollar allocation; None = `allocation` of equity
    reserved: float = 1.0  # share of the allocation held for it while idle; the rest comes from the shared pool

    @property
    def drawdown(self) -> float:
        """Loss from the peak, as a share of the allocation."""
        return max(0.0, self.peak_pnl - self.pnl) / self.base_usd if self.base_usd else 0.0

    def cap_usd(self, equity: float) -> float:
        return self.fixed_usd if self.fixed_usd is not None else self.allocation * equity

    def reserved_usd(self, equity: float) -> float:
        return self.reserved * self.cap_usd(equity)

    def committed(self, equity: float) -> float:
        """Money of max deployed this strategy holds: its reserve, or what it has in use when above it."""
        return max(self.reserved_usd(equity), self.deployed)


@dataclass
class Account:
    equity: float
    cash: float
    deployed: float  # $ in all positions + working buys
    intraday_deployed: float
    working_buys: float = 0.0
    peak_equity: float = 0.0
    strategies: dict[str, StrategyFund] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        return max(0.0, 1 - self.equity / self.peak_equity) if self.peak_equity else 0.0

    def committed(self) -> float:
        """Reserves plus use above them, plus positions no strategy owns."""
        unmanaged = max(0.0, self.deployed - sum(f.deployed for f in self.strategies.values()))
        return sum(f.committed(self.equity) for f in self.strategies.values()) + unmanaged

    def pool_room(self, sid: str, cfg: FundsRisk) -> float:
        """Largest buy for `sid` that keeps every other strategy's reserve intact."""
        f = self.strategies[sid]
        others = self.committed() - f.committed(self.equity)
        return max(0.0, cfg.max_deployed * self.equity - others - f.deployed)


def check_buy(sid: str, kind: str, notional: float, acct: Account, cfg: FundsRisk | None) -> list[Check]:
    """Fund checks for one buy (entry or add). Sells always pass: they only free money."""
    if cfg is None:
        return []
    eq = max(acct.equity, 1e-9)
    f = acct.strategies.get(sid)
    if f is None:
        return [Check("strategy_allocated", False, f"{sid} has no fund allocation")]
    cash_after = acct.cash - acct.working_buys - notional
    checks = [
        Check("strategy_active", f.status == "active", f"fund status {f.status}" + (f": {f.reason}" if f.reason else "")),
        Check("strategy_allocation", f.deployed + notional <= f.cap_usd(eq) + 1e-6,
              f"${f.deployed + notional:,.0f} after / allocation ${f.cap_usd(eq):,.0f} ({f.allocation:.1%})"),
        Check("shared_pool", notional <= acct.pool_room(sid, cfg) + 1e-6,
              f"${notional:,.0f} buy / ${acct.pool_room(sid, cfg):,.0f} free without touching other strategies' reserves "
              f"(this one reserves ${f.reserved_usd(eq):,.0f}, {f.reserved:.0%} of its allocation)"),
        Check("cash_reserve", cash_after >= cfg.cash_reserve * eq - 1e-6,
              f"cash ${cash_after:,.0f} after / reserve ${cfg.cash_reserve * eq:,.0f} ({cfg.cash_reserve:.0%})"),
        Check("max_deployed", acct.deployed + notional <= cfg.max_deployed * eq + 1e-6,
              f"${acct.deployed + notional:,.0f} deployed after / max ${cfg.max_deployed * eq:,.0f} ({cfg.max_deployed:.0%})"),
    ]
    if kind == "intraday":
        checks.append(Check("intraday_budget", acct.intraday_deployed + notional <= cfg.intraday_budget * eq + 1e-6,
                            f"${acct.intraday_deployed + notional:,.0f} intraday after / max ${cfg.intraday_budget * eq:,.0f}"))
    return checks


def room_usd(sid: str, kind: str, acct: Account, cfg: FundsRisk | None) -> float | None:
    """Largest buy that passes check_buy's money limits (used to size intraday entries). None = no limit."""
    if cfg is None:
        return None
    f = acct.strategies.get(sid)
    if f is None or f.status != "active":
        return 0.0
    eq = acct.equity
    room = min(f.cap_usd(eq) - f.deployed, acct.cash - acct.working_buys - cfg.cash_reserve * eq,
               cfg.max_deployed * eq - acct.deployed, acct.pool_room(sid, cfg))
    if kind == "intraday":
        room = min(room, cfg.intraday_budget * eq - acct.intraday_deployed)
    return max(0.0, room)


def breach(f: StrategyFund) -> str:
    """'' or why the strategy must stop: drawdown pauses it, a daily loss pauses entries for the day."""
    if f.base_usd <= 0:
        return ""
    if f.drawdown >= f.max_drawdown - 1e-12:
        return f"drawdown {f.drawdown:.1%} of its allocation reached the {f.max_drawdown:.0%} limit"
    if f.kind == "intraday" and f.daily_loss and f.pnl_today <= -f.daily_loss * f.base_usd:
        return f"daily loss ${-f.pnl_today:,.0f} reached {f.daily_loss:.0%} of its allocation"
    return ""


def validate_allocation(sid: str, allocation: float, max_drawdown: float, daily_loss: float | None,
                        others: dict[str, float], cfg: FundsRisk, reserved: float = 1.0) -> list[str]:
    """Errors for a proposed allocation; `others` = reserved shares of equity of every other running strategy."""
    errs = []
    if not 0 < allocation <= cfg.max_strategy_allocation + 1e-12:
        errs.append(f"allocation must be > 0 and at most {cfg.max_strategy_allocation:.0%} (Risk page: max allocation per strategy)")
    if not 0 <= reserved <= 1 + 1e-12:
        errs.append("reserved must be between 0% and 100% of the allocation")
    total = sum(others.values()) + allocation * reserved
    if total > cfg.max_deployed + 1e-9:
        free = max(0.0, cfg.max_deployed - sum(others.values()))
        errs.append(f"reserves would add up to {total:.1%}, above max deployed capital {cfg.max_deployed:.0%}; "
                    f"{free:.1%} is unreserved (reserve at most {min(1.0, free / allocation) if allocation > 0 else 0:.0%} of this allocation "
                    f"and let the rest come from the shared pool)")
    if not 0 < max_drawdown <= cfg.strategy_max_drawdown + 1e-12:
        errs.append(f"max drawdown must be > 0 and at most {cfg.strategy_max_drawdown:.0%} (Risk page: strategy drawdown stop)")
    if daily_loss is not None and not 0 < daily_loss <= cfg.strategy_daily_loss + 1e-12:
        errs.append(f"daily loss must be > 0 and at most {cfg.strategy_daily_loss:.0%} (Risk page: strategy daily loss stop)")
    return errs


def without(acct: Account, sid: str, kind: str, notional: float) -> Account:
    """The account as if one working buy (the order being re-checked) weren't there yet."""
    from dataclasses import replace
    strategies = dict(acct.strategies)
    if sid in strategies:
        strategies[sid] = replace(strategies[sid], deployed=max(0.0, strategies[sid].deployed - notional))
    return replace(acct, deployed=max(0.0, acct.deployed - notional), working_buys=max(0.0, acct.working_buys - notional),
                   intraday_deployed=max(0.0, acct.intraday_deployed - notional) if kind == "intraday" else acct.intraday_deployed,
                   strategies=strategies)
