"""Evaluates compiled strategies over completed 1-minute bars.

Semantics:
  * Every value is computed "as of" a bar index i (that bar's close). Nothing reads bar i+1.
  * Missing, stale or not-yet-computable values are None. None propagates through arithmetic
    and comparisons, and a condition that ends up None is false (fail closed), unless the
    signal declares a default.
  * Signals are looked up point-in-time: only signals stamped at or before the bar's close.
  * Daily-history functions (ret_d, vol_d, ...) read completed prior sessions only.
  * peer(SYM, x) evaluates x for SYM on SYM's latest bar that closed at or before this bar's close;
    a peer bar more than PEER_MAX_LAG seconds older than this bar is stale, and the value is missing.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date

from common import timeutil as T

from . import expr as E
from . import features as F
from .builtins import POSITION, SIGNALS
from .validator import Strategy

PEER_MAX_LAG = 120  # seconds: a peer's latest bar may be at most this much older than the bar being decided


# ---------------------------------------------------------------- volume profile
def _profile() -> list[float]:
    """Cumulative intraday volume fraction by minute (U-shaped). Shared with the simulator."""
    w = [1.0 + 2.2 * math.exp(-m / 18) + 1.4 * math.exp(-(389 - m) / 22) for m in range(T.BARS_PER_DAY)]
    total = sum(w)
    cum, out = 0.0, []
    for x in w:
        cum += x
        out.append(cum / total)
    return out


VOLUME_CUM = _profile()
VOLUME_W = [VOLUME_CUM[0]] + [VOLUME_CUM[i] - VOLUME_CUM[i - 1] for i in range(1, T.BARS_PER_DAY)]


# ---------------------------------------------------------------- market data
@dataclass
class Daily:
    date: str
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class DayData:
    """One symbol's session: intraday bars so far plus prior daily history."""
    symbol: str
    day: date
    history: list[Daily]  # prior sessions, oldest first
    ts: list[int] = field(default_factory=list)
    o: list[float] = field(default_factory=list)
    h: list[float] = field(default_factory=list)
    l: list[float] = field(default_factory=list)
    c: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)
    cum_pv: list[float] = field(default_factory=list)
    cum_v: list[float] = field(default_factory=list)
    run_h: list[float] = field(default_factory=list)
    run_l: list[float] = field(default_factory=list)

    def append(self, ts: int, o: float, h: float, l: float, c: float, v: float) -> int:
        self.ts.append(ts)
        self.o.append(o)
        self.h.append(h)
        self.l.append(l)
        self.c.append(c)
        self.v.append(v)
        tp = (h + l + c) / 3
        self.cum_pv.append((self.cum_pv[-1] if self.cum_pv else 0.0) + tp * v)
        self.cum_v.append((self.cum_v[-1] if self.cum_v else 0.0) + v)
        self.run_h.append(max(self.run_h[-1], h) if self.run_h else h)
        self.run_l.append(min(self.run_l[-1], l) if self.run_l else l)
        return len(self.ts) - 1

    def __len__(self):
        return len(self.ts)

    def close_ts(self, i: int) -> int:
        return self.ts[i] + 60

    def minute_index(self, i: int) -> int:
        return (self.ts[i] - T.open_ts(self.day)) // 60

    @property
    def prior_close(self) -> float | None:
        return self.history[-1].c if self.history else None

    def closes(self) -> list[tuple[str, float]]:
        """Completed prior sessions as (day, close), for the daily-history features."""
        if getattr(self, "_closes", None) is None or len(self._closes) != len(self.history):
            self._closes = [(h.date, h.c) for h in self.history]
        return self._closes

    def asof(self, ts: int) -> int:
        """Index of the latest bar starting at or before ts (-1 if none)."""
        return bisect.bisect_right(self.ts, ts) - 1


# ---------------------------------------------------------------- signals
class SignalBook:
    """Point-in-time signal store: (symbol, name) -> sorted [(ts, value)]."""

    def __init__(self, rows=()):
        self._d: dict[tuple[str, str], tuple[list[int], list]] = {}
        for r in sorted(rows, key=lambda r: r["ts"]):
            self.add(r["symbol"], r["name"], r["ts"], r["value"])

    def add(self, symbol: str, name: str, ts: int, value):
        tss, vals = self._d.setdefault((symbol, name), ([], []))
        k = bisect.bisect_right(tss, ts)
        tss.insert(k, ts)
        vals.insert(k, value)

    def latest(self, symbol: str, name: str, as_of: int, since: int):
        tss, vals = self._d.get((symbol, name), ((), ()))
        k = bisect.bisect_right(tss, as_of) - 1
        if k >= 0 and tss[k] >= since:
            return vals[k]
        return None

    def any_true(self, symbol: str, name: str, as_of: int, since: int) -> bool | None:
        tss, vals = self._d.get((symbol, name), ((), ()))
        lo = bisect.bisect_left(tss, since)
        hi = bisect.bisect_right(tss, as_of)
        if lo >= hi:
            return None
        return any(bool(v) for v in vals[lo:hi])

    def has_any(self, symbol: str, as_of: int, since: int) -> bool:
        for (sym, _), (tss, _) in self._d.items():
            if sym == symbol:
                lo = bisect.bisect_left(tss, since)
                if lo < len(tss) and tss[lo] <= as_of:
                    return True
        return False


@dataclass
class Position:
    side: str
    entry_price: float
    stop: float
    entry_index: int


# ---------------------------------------------------------------- evaluator
class Evaluator:
    """Evaluate one strategy over one symbol's session."""

    def __init__(self, strat: Strategy, day: DayData, signals: SignalBook, peers: dict[str, DayData] | None = None):
        self.s = strat
        self.d = day
        self._signals = signals
        self.peers = peers or {}
        self._peer_evals: dict[str, Evaluator] = {}
        self.memo: dict[tuple[int, int], object] = {}
        self.position: Position | None = None
        self._pos_nodes: set[int] = set()  # values that must be recomputed every time (position, peers)
        for node in self._all_nodes():
            self._mark_position(node)

    @property
    def signals(self) -> SignalBook:
        return self._signals

    @signals.setter
    def signals(self, book: SignalBook):
        self._signals = book
        for ev in self._peer_evals.values():
            ev.signals = book
            ev.memo.clear()

    def _all_nodes(self):
        s = self.s
        out = list(s.vars.values()) + s.universe_filters + s.entry_when_all + s.exit_when_any
        out += [n for n in (s.exit_stop, s.exit_target) if n is not None]
        return out

    def _mark_position(self, node: E.Node) -> bool:
        # A peer's bar for this moment can arrive after this bar (live polling), so peer values aren't memoized here;
        # the peer's own evaluator memoizes per peer bar, which never changes once stored.
        dep = isinstance(node, E.Call) and node.fn == "peer"
        if isinstance(node, E.Name):
            dep = node.id in POSITION or (node.id in self.s.vars and self._mark_position(self.s.vars[node.id]))
        for ch in E.children(node):
            dep = self._mark_position(ch) or dep
        if dep:
            self._pos_nodes.add(id(node))
        return dep

    # -------------------------------------------------------------- public
    def cond(self, node: E.Node, i: int) -> bool:
        return self.value(node, i) is True

    def explain(self, node: E.Node, i: int) -> dict:
        """Decision-record entry: condition text, its value, and the operands' values."""
        val = self.value(node, i)
        rec = {"cond": node.src, "ok": val is True, "value": _fmt(val)}
        if isinstance(node, E.Cmp):
            rec["detail"] = f"{node.l.src} = {_fmt(self.value(node.l, i))} {node.op} {node.r.src} = {_fmt(self.value(node.r, i))}"
        elif isinstance(node, E.In):
            rec["detail"] = f"{node.l.src} = {_fmt(self.value(node.l, i))}"
        elif isinstance(node, E.Call) and node.fn in ("crosses_above", "crosses_below"):
            a, b = node.args
            rec["detail"] = (
                f"{a.src}: {_fmt(self.value(a, i - 1) if i else None)} -> {_fmt(self.value(a, i))}; "
                f"{b.src}: {_fmt(self.value(b, i - 1) if i else None)} -> {_fmt(self.value(b, i))}"
            )
        if val is None:
            rec["missing"] = True
        return rec

    def in_universe(self, i: int) -> tuple[bool, list[dict]]:
        recs = [self.explain(f, i) for f in self.s.universe_filters]
        ok = all(r["ok"] for r in recs)
        if self.s.universe_news_today:
            has = self.signals.has_any(self.d.symbol, self.d.close_ts(i), T.prior_close_ts(self.d.day))
            recs.insert(0, {"cond": "news_today", "ok": has, "value": _fmt(has)})
            ok = ok and has
        return ok, recs

    # -------------------------------------------------------------- core
    def value(self, node: E.Node, i: int):
        if i < 0 or i >= len(self.d):
            return None
        cacheable = id(node) not in self._pos_nodes
        key = (id(node), i)
        if cacheable and key in self.memo:
            return self.memo[key]
        v = self._eval(node, i)
        if cacheable:
            self.memo[key] = v
        return v

    def _eval(self, node: E.Node, i: int):
        d = self.d
        if isinstance(node, E.Num):
            return node.value
        if isinstance(node, E.BoolLit):
            return node.value
        if isinstance(node, E.Name):
            return self._name(node.id, i)
        if isinstance(node, E.Index):
            return self.value(node.base, i - node.n)
        if isinstance(node, E.Neg):
            x = self.value(node.x, i)
            return None if x is None else -x
        if isinstance(node, E.BinOp):
            a, b = self.value(node.l, i), self.value(node.r, i)
            if a is None or b is None:
                return None
            if node.op == "+":
                return a + b
            if node.op == "-":
                return a - b
            if node.op == "*":
                return a * b
            return None if b == 0 else a / b
        if isinstance(node, E.Cmp):
            a, b = self.value(node.l, i), self.value(node.r, i)
            if a is None or b is None:
                return None
            return {
                ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "!=": a != b,
            }[node.op]
        if isinstance(node, E.In):
            v = self.value(node.l, i)
            if v is None:
                return None
            return (v in node.items) != node.negate
        if isinstance(node, E.Not):
            v = self.value(node.x, i)
            return None if v is None else not v
        if isinstance(node, E.BoolOp):
            vals = [self.value(x, i) for x in node.items]
            if node.op == "and":
                if any(v is False for v in vals):
                    return False
                return None if any(v is None for v in vals) else True
            if any(v is True for v in vals):
                return True
            return None if any(v is None for v in vals) else False
        if isinstance(node, E.Call):
            return self._call(node, i)
        raise TypeError(f"unknown node {node!r}")

    def _name(self, name: str, i: int):
        s, d = self.s, self.d
        if name in s.params:
            return s.params[name]
        if name in s.vars:
            return self.value(s.vars[name], i)
        if name in s.signals:
            return self._signal(name, i)
        if name == "open":
            return d.o[i]
        if name == "high":
            return d.h[i]
        if name == "low":
            return d.l[i]
        if name == "close":
            return d.c[i]
        if name == "volume":
            return d.v[i]
        if name == "vwap":
            return d.cum_pv[i] / d.cum_v[i] if d.cum_v[i] else None
        if name == "day_open":
            return d.o[0]
        if name == "day_high":
            return d.run_h[i]
        if name == "day_low":
            return d.run_l[i]
        if name == "prior_close":
            return d.prior_close
        if name == "gap":
            pc = d.prior_close
            return d.o[0] / pc - 1 if pc else None
        p = self.position
        if name == "entry_price":
            return p.entry_price if p else None
        if name == "risk_per_share":
            return abs(p.entry_price - p.stop) if p else None
        if name == "bars_held":
            return i - p.entry_index if p else None
        raise KeyError(name)

    def _signal(self, name: str, i: int):
        decl = self.s.signals[name]
        as_of = self.d.close_ts(i)
        if decl.max_age == "since_prior_close":
            since = T.prior_close_ts(self.d.day)
        elif decl.max_age == "session":
            since = T.open_ts(self.d.day)
        else:
            since = as_of - int(decl.max_age)
        if SIGNALS[name].sticky:
            v = self.signals.any_true(self.d.symbol, name, as_of, since)
        else:
            v = self.signals.latest(self.d.symbol, name, as_of, since)
        if v is None and decl.has_default:
            return decl.default
        return v

    def _int_arg(self, node: E.Node, i: int) -> int | None:
        v = self.value(node, i)
        return int(v) if v is not None else None

    def _window(self, x: E.Node, i: int, n: int) -> list | None:
        if n is None or n < 1 or i - n + 1 < 0:
            return None
        vals = [self.value(x, j) for j in range(i - n + 1, i + 1)]
        return None if any(v is None for v in vals) else vals

    def peer_eval(self, sym: str) -> Evaluator | None:
        d = self.peers.get(sym)
        if d is None:
            return None
        ev = self._peer_evals.get(sym)
        if ev is None or ev.d is not d:
            ev = self._peer_evals[sym] = Evaluator(self.s, d, self._signals)
        return ev

    def _peer_index(self, ev: Evaluator, i: int) -> int | None:
        j = ev.d.asof(self.d.ts[i])
        if j < 0 or self.d.ts[i] - ev.d.ts[j] > PEER_MAX_LAG:
            return None
        return j

    def _call(self, node: E.Call, i: int):
        fn, a, d = node.fn, node.args, self.d
        if fn == "peer":
            ev = self.peer_eval(a[0].id)
            j = self._peer_index(ev, i) if ev is not None else None
            return None if j is None else ev.value(a[1], j)
        if fn in F.DAILY_FUNCTIONS:
            takes_symbol = F.DAILY_FUNCTIONS[fn][1]
            other = None
            if takes_symbol:
                od = self.peers.get(a[0].id)
                if od is None:
                    return None
                other = od.closes()
            args = [self._int_arg(x, i) for x in (a[1:] if takes_symbol else a)]
            return F.compute(fn, d.closes(), args, other)
        if fn in ("or_high", "or_low"):
            n = self._int_arg(a[0], i)
            if n is None or d.minute_index(i) < n - 1 or len(d) < n:
                return None
            # bars in the first n minutes of the session
            idx = [j for j in range(min(len(d), n)) if d.minute_index(j) < n]
            if not idx:
                return None
            return max(d.h[j] for j in idx) if fn == "or_high" else min(d.l[j] for j in idx)
        if fn == "sma":
            w = self._window(a[0], i, self._int_arg(a[1], i))
            return sum(w) / len(w) if w else None
        if fn == "ema":
            return self._ema(node, i)
        if fn == "rsi":
            n = self._int_arg(a[1], i)
            w = self._window(a[0], i, n + 1) if n else None
            if not w:
                return None
            gains = sum(max(w[k] - w[k - 1], 0) for k in range(1, len(w)))
            losses = sum(max(w[k - 1] - w[k], 0) for k in range(1, len(w)))
            if gains + losses == 0:
                return 50.0
            return 100.0 * gains / (gains + losses)
        if fn == "atr":
            n = self._int_arg(a[0], i)
            if n is None or i - n + 1 < 0:
                return None
            trs = []
            for j in range(i - n + 1, i + 1):
                pc = d.c[j - 1] if j > 0 else d.o[j]
                trs.append(max(d.h[j] - d.l[j], abs(d.h[j] - pc), abs(d.l[j] - pc)))
            return sum(trs) / n
        if fn in ("highest", "lowest"):
            w = self._window(a[0], i, self._int_arg(a[1], i))
            if not w:
                return None
            return max(w) if fn == "highest" else min(w)
        if fn == "rvol":
            n = self._int_arg(a[0], i)
            hist = d.history[-n:] if n else []
            if not n or len(hist) < n:
                return None
            avg = sum(h.v for h in hist) / n
            m = d.minute_index(i)
            if not 0 <= m < len(VOLUME_CUM) or avg <= 0:
                return None
            return d.cum_v[i] / (avg * VOLUME_CUM[m])
        if fn == "adv":
            n = self._int_arg(a[0], i)
            hist = d.history[-n:] if n else []
            if not n or len(hist) < n:
                return None
            return sum(h.c * h.v for h in hist) / n
        if fn == "atr_d":
            n = self._int_arg(a[0], i)
            hist = d.history[-(n + 1):] if n else []
            if not n or len(hist) < n + 1:
                return None
            trs = [
                max(hist[k].h - hist[k].l, abs(hist[k].h - hist[k - 1].c), abs(hist[k].l - hist[k - 1].c))
                for k in range(1, len(hist))
            ]
            return sum(trs) / len(trs)
        if fn in ("high_d", "low_d"):
            n = self._int_arg(a[0], i)
            hist = d.history[-n:] if n else []
            if not n or len(hist) < n:
                return None
            return max(h.h for h in hist) if fn == "high_d" else min(h.l for h in hist)
        if fn in ("fvg_bull_top", "fvg_bull_bottom", "fvg_bear_top", "fvg_bear_bottom"):
            gap = self._fvg(fn.startswith("fvg_bull"), i, self._int_arg(a[0], i))
            if gap is None:
                return None
            return gap[1] if fn.endswith("_top") else gap[0]
        if fn in ("crosses_above", "crosses_below"):
            if i < 1:
                return None
            a0, b0 = self.value(a[0], i - 1), self.value(a[1], i - 1)
            a1, b1 = self.value(a[0], i), self.value(a[1], i)
            if None in (a0, b0, a1, b1):
                return None
            if fn == "crosses_above":
                return a1 > b1 and a0 <= b0
            return a1 < b1 and a0 >= b0
        if fn == "streak":
            n = 0
            j = i
            while j >= 0 and self.value(a[0], j) is True:
                n += 1
                j -= 1
            return float(n)
        raise KeyError(fn)

    def _fvg(self, bull: bool, i: int, n: int | None) -> tuple[float, float] | None:
        """(bottom, top) of the newest fair value gap still open as of bar i.

        A bullish gap is a three-bar pattern with low[k] > high[k-2]; a bearish one has high[k] < low[k-2].
        Only gaps completed on bars before i, within the last n bars, are seen, so bar i can be the retest.
        A gap closes once a later bar (before i) closes through its far side (below a bullish gap's
        bottom, above a bearish gap's top); an older open gap is used if the newest is closed.
        """
        d = self.d
        if not n or n < 1:
            return None
        worst = None  # lowest (bull) / highest (bear) close of the bars after k, up to i-1
        for k in range(i - 1, max(i - 1 - n, 1), -1):
            if bull and d.l[k] > d.h[k - 2] and (worst is None or worst >= d.h[k - 2]):
                return d.h[k - 2], d.l[k]
            if not bull and d.h[k] < d.l[k - 2] and (worst is None or worst <= d.l[k - 2]):
                return d.h[k], d.l[k - 2]
            worst = d.c[k] if worst is None else (min(worst, d.c[k]) if bull else max(worst, d.c[k]))
        return None

    def _ema(self, node: E.Call, i: int):
        n = self._int_arg(node.args[1], i)
        if n is None or i < n - 1:
            return None
        alpha = 2 / (n + 1)
        # find the latest memoized ema at or before i, then roll forward
        start = n - 1
        prev = None
        for j in range(i, n - 2, -1):
            if (id(node), j) in self.memo:
                start, prev = j, self.memo[(id(node), j)]
                break
        if prev is None:
            w = self._window(node.args[0], n - 1, n)
            if w is None:
                return None
            prev = sum(w) / n
            self.memo[(id(node), n - 1)] = prev
            start = n - 1
        for j in range(start + 1, i + 1):
            x = self.value(node.args[0], j)
            if x is None:
                return None
            prev = alpha * x + (1 - alpha) * prev
            self.memo[(id(node), j)] = prev
        return prev


def _fmt(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return round(v, 6) if abs(v) < 1e6 else round(v)
    return v
