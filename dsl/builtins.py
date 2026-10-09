"""Versioned catalog of DSL builtins and signals.

The validator uses this to check names and call shapes; the evaluator implements them.
"""
from __future__ import annotations

from dataclasses import dataclass

BUILTINS_VERSION = "builtins@v1"

# Bar series: indexable with x[n] (n bars back).
SERIES = {"open", "high", "low", "close", "volume"}
# Session values (scalars for the current bar).
SESSION = {"vwap", "day_open", "day_high", "day_low", "prior_close", "gap"}
# Only meaningful once a position exists: allowed in exit.* only.
POSITION = {"entry_price", "risk_per_share", "bars_held"}


@dataclass(frozen=True)
class FnSig:
    args: tuple[str, ...]  # "expr" = any numeric/bool expression, "const" = int literal or param
    returns: str  # "num" | "bool"
    doc: str


FUNCTIONS: dict[str, FnSig] = {
    "or_high": FnSig(("const",), "num", "High of the first N minutes; missing until the range completes"),
    "or_low": FnSig(("const",), "num", "Low of the first N minutes; missing until the range completes"),
    "sma": FnSig(("expr", "const"), "num", "Simple moving average of x over N bars"),
    "ema": FnSig(("expr", "const"), "num", "Exponential moving average of x over N bars"),
    "rsi": FnSig(("expr", "const"), "num", "RSI of x over N bars (SMA-smoothed, 0-100)"),
    "atr": FnSig(("const",), "num", "Average true range over N 1-min bars"),
    "highest": FnSig(("expr", "const"), "num", "Highest x over the last N bars"),
    "lowest": FnSig(("expr", "const"), "num", "Lowest x over the last N bars"),
    "rvol": FnSig(("const",), "num", "Cumulative volume vs the N-day average at the same time of day"),
    "adv": FnSig(("const",), "num", "Average daily dollar volume over the prior N days"),
    "atr_d": FnSig(("const",), "num", "Daily ATR over the prior N days"),
    "crosses_above": FnSig(("expr", "expr"), "bool", "a > b on this bar and a <= b on the previous bar"),
    "crosses_below": FnSig(("expr", "expr"), "bool", "a < b on this bar and a >= b on the previous bar"),
    "streak": FnSig(("expr",), "num", "Number of consecutive bars, ending now, on which cond is true"),
}


@dataclass(frozen=True)
class SignalSpec:
    type: str  # "num" | "enum" | "bool"
    values: tuple[str, ...] = ()
    sticky: bool = False  # bool signals that stay true for the session once true
    source: str = "news_agent@v1"
    lo: float | None = None
    hi: float | None = None


CATALYSTS = (
    "earnings", "guidance", "analyst_upgrade", "analyst_downgrade", "product",
    "contract", "m_and_a", "legal", "macro", "other",
)

SIGNALS: dict[str, SignalSpec] = {
    "news.score": SignalSpec("num", lo=-1.0, hi=1.0),
    "news.catalyst": SignalSpec("enum", CATALYSTS),
    "news.materiality": SignalSpec("enum", ("low", "medium", "high")),
    "news.fundamental": SignalSpec("bool", sticky=True),
    "news.red_flag": SignalSpec("bool", sticky=True),
    "note.stance": SignalSpec("enum", ("bullish", "bearish", "neutral", "avoid"), source="notes@v1"),
}

SIGNAL_SOURCES = {"news_agent@v1", "notes@v1"}
MAX_AGES = {"since_prior_close", "session"}


def is_signal(name: str) -> bool:
    return name.startswith("news.") or name.startswith("note.")
