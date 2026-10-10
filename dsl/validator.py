"""Strategy validator: schema, declared names, params-only thresholds, signal fencing,
fail-closed defaults and limits that may only tighten the risk controls.

`validate(text, risk)` returns a Validation with errors, warnings and, when it passes,
a compiled Strategy ready for the evaluator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time

from common.config import RiskConfig
from common.units import is_pct, parse_duration, parse_hhmm, parse_number, parse_pct, parse_window

from . import expr as E
from .builtins import FUNCTIONS, MAX_AGES, POSITION, SERIES, SESSION, SIGNAL_SOURCES, SIGNALS, is_signal
from .loader import DSLError, file_hash, load_text

TOP_KEYS = {
    "dsl", "kind", "id", "version", "source", "assumptions", "suggestions", "universe", "timeframe",
    "params", "signals", "vars", "entry", "sizing", "exit", "execution", "limits",
}
REQUIRED = {"dsl", "id", "version", "universe", "timeframe", "params", "entry", "sizing", "exit", "execution"}
LIMIT_KEYS = {"max_trades_per_day", "max_open_positions", "max_position"}
MODES = {"manual", "auto", "paper"}


@dataclass
class SignalDecl:
    name: str
    source: str
    max_age: str | float  # "since_prior_close" | "session" | seconds
    default: object = None
    has_default: bool = False


@dataclass
class Strategy:
    text: str
    hash: str
    raw: dict
    id: str
    version: int
    side: str
    params: dict[str, float]
    param_ranges: dict[str, tuple[float, float]]
    signals: dict[str, SignalDecl]
    vars: dict[str, E.Node]
    universe_news_today: bool
    universe_filters: list[E.Node]
    entry_window: tuple[time, time]
    entry_when_all: list[E.Node]
    risk_per_trade: float
    exit_stop: E.Node | None
    exit_target: E.Node | None
    exit_when_any: list[E.Node]
    flatten_at: time
    mode: str
    limits: dict[str, float]
    assumptions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    kind: str = "intraday"

    @property
    def uses_signals(self) -> bool:
        return bool(self.signals)


@dataclass
class Validation:
    ok: bool
    errors: list[str]
    warnings: list[str]
    strategy: Strategy | None = None
    hash: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "hash": self.hash}


class _V:
    def __init__(self, text: str, risk: RiskConfig):
        self.text = text
        self.risk = risk
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str):
        self.errors.append(msg)

    # ---------------------------------------------------------------- helpers
    def parse_expr(self, where: str, src) -> E.Node | None:
        try:
            return E.parse(src)
        except DSLError as e:
            self.err(f"{where}: {e}")
            return None

    def run(self) -> Validation:
        h = file_hash(self.text)
        try:
            d = load_text(self.text)
        except DSLError as e:
            return Validation(False, [str(e)], [], None, h)
        self._find_leverage(d, "")
        kind = d.get("kind", "intraday")
        if kind == "portfolio":
            from .portfolio import validate_portfolio
            return validate_portfolio(self, d)
        if kind != "intraday":
            return Validation(False, [f"kind must be 'intraday' (the default) or 'portfolio', got {kind!r}"], self.warnings, None, h)
        for k in d:
            if k not in TOP_KEYS and k != "leverage":
                self.err(f"unknown top-level key {k!r}")
        for k in sorted(REQUIRED - set(d)):
            self.err(f"missing required key {k!r}")
        if self.errors:
            return Validation(False, self.errors, self.warnings, None, h)

        if str(d["dsl"]) != "0.1":
            self.err(f"unsupported dsl version {d['dsl']!r} (expected 0.1)")
        sid = d["id"]
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", sid):
            self.err("id must be lowercase snake_case")
        version = d["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            self.err("version must be a positive integer")
        if d["timeframe"] != "1min":
            self.err("timeframe must be '1min' (the only timeframe in dsl 0.1)")

        params, ranges = self.check_params(d.get("params") or {})
        signals = self.check_signals(d.get("signals") or {})
        self.params, self.signals = params, signals

        # vars: parse, then compute which are signal- or position-tainted
        raw_vars = d.get("vars") or {}
        if not isinstance(raw_vars, dict):
            self.err("vars must be a mapping")
            raw_vars = {}
        self.vars: dict[str, E.Node] = {}
        for name, src in raw_vars.items():
            self.check_new_name("var", name)
            node = self.parse_expr(f"vars.{name}", src)
            if node is not None:
                self.vars[name] = node
        self.check_var_cycles()
        self.sig_taint = {v: self.taint(v, is_signal) for v in self.vars}
        self.pos_taint = {v: self.taint(v, lambda n: n in POSITION) for v in self.vars}
        for name, node in self.vars.items():
            # fencing for vars is enforced where the var is used
            self.check_expr(f"vars.{name}", node, want=None, signals_ok=True, position_ok=True)

        # universe
        uni = d["universe"] or {}
        news_today = False
        filters: list[E.Node] = []
        if not isinstance(uni, dict):
            self.err("universe must be a mapping")
        else:
            frm = uni.get("from") or {}
            if not isinstance(frm, dict) or set(frm) - {"news_today"}:
                self.err("universe.from supports only {news_today: true|false}")
            else:
                news_today = bool(frm.get("news_today", False))
            for i, src in enumerate(uni.get("filters") or []):
                node = self.parse_expr(f"universe.filters[{i}]", src)
                if node is not None:
                    self.check_expr(f"universe.filters[{i}]", node, want="bool", signals_ok=False)
                    filters.append(node)

        # entry
        entry = d["entry"] or {}
        side = entry.get("side")
        if side not in ("long", "short"):
            self.err("entry.side must be 'long' or 'short'")
        elif side == "short" and not self.risk.allow_short:
            self.err("entry.side 'short' is not allowed: allow_short is off on the Risk page")
        window = self.risk.entries
        if "window" in entry:
            try:
                window = parse_window(entry["window"])
                if window[0] < self.risk.entries[0] or window[1] > self.risk.entries[1]:
                    self.err(
                        f"entry.window {entry['window']} is outside the risk control market.entries "
                        f"{self.risk.entries[0]:%H:%M}-{self.risk.entries[1]:%H:%M}; strategies can only narrow it"
                    )
            except ValueError as e:
                self.err(f"entry.window: {e}")
        when_all = []
        conds = entry.get("when_all")
        if not isinstance(conds, list) or not conds:
            self.err("entry.when_all must be a non-empty list of conditions")
            conds = []
        for i, src in enumerate(conds):
            node = self.parse_expr(f"entry.when_all[{i}]", src)
            if node is not None:
                self.check_expr(f"entry.when_all[{i}]", node, want="bool", signals_ok=True)
                when_all.append(node)
        for k in set(entry) - {"side", "window", "when_all"}:
            self.err(f"unknown key entry.{k}")

        # sizing
        sizing = d["sizing"] or {}
        rpt = self.risk.default_risk
        if set(sizing) - {"risk_per_trade"}:
            self.err("sizing supports only risk_per_trade")
        if "risk_per_trade" in sizing:
            try:
                rpt = parse_pct(sizing["risk_per_trade"])
            except ValueError as e:
                self.err(f"sizing.risk_per_trade: {e}")
        if not 0 < rpt <= self.risk.max_risk:
            self.err(
                f"sizing.risk_per_trade {rpt:.2%} must be > 0 and <= the risk control per_trade.max_risk "
                f"{self.risk.max_risk:.2%}"
            )

        # exit
        ex = d["exit"] or {}
        stop = target = None
        if "stop" in ex:
            stop = self.parse_expr("exit.stop", ex["stop"])
            if stop is not None:
                self.check_expr("exit.stop", stop, want="num", signals_ok=False, position_ok=True)
                if "risk_per_share" in self.closure_names(stop):
                    self.err("exit.stop cannot use risk_per_share (it is defined by the stop)")
        if "target" in ex:
            target = self.parse_expr("exit.target", ex["target"])
            if target is not None:
                self.check_expr("exit.target", target, want="num", signals_ok=False, position_ok=True)
        if self.risk.require_bracket and (stop is None or target is None):
            self.err("exit.stop and exit.target are required (the risk controls require a bracket)")
        flatten = self.risk.flatten_by
        if "flatten_at" in ex:
            try:
                flatten = parse_hhmm(ex["flatten_at"])
                if flatten > self.risk.flatten_by:
                    self.err(
                        f"exit.flatten_at {ex['flatten_at']} is later than the risk control flatten_by "
                        f"{self.risk.flatten_by:%H:%M}"
                    )
            except ValueError as e:
                self.err(f"exit.flatten_at: {e}")
        when_any = []
        for i, src in enumerate(ex.get("when_any") or []):
            node = self.parse_expr(f"exit.when_any[{i}]", src)
            if node is not None:
                self.check_expr(f"exit.when_any[{i}]", node, want="bool", signals_ok=True, position_ok=True)
                when_any.append(node)
        for k in set(ex) - {"stop", "target", "flatten_at", "when_any"}:
            self.err(f"unknown key exit.{k}")

        # execution
        exe = d["execution"] or {}
        mode_ = exe.get("mode", "manual")
        if mode_ not in MODES:
            self.err(f"execution.mode must be one of {sorted(MODES)}")
        if mode_ == "auto":
            self.warnings.append("execution.mode 'auto' in the file is ignored: the registry starts at manual and the owner switches modes on the dashboard")
            mode_ = "manual"

        # limits: only tighten
        limits = {}
        for k, v in (d.get("limits") or {}).items():
            if k not in LIMIT_KEYS:
                self.err(f"unknown limit {k!r} (allowed: {sorted(LIMIT_KEYS)})")
                continue
            try:
                val = parse_pct(v) if k == "max_position" else parse_number(v)
            except ValueError as e:
                self.err(f"limits.{k}: {e}")
                continue
            cap = {
                "max_trades_per_day": self.risk.max_trades_per_day,
                "max_open_positions": self.risk.max_open_positions,
                "max_position": self.risk.max_position,
            }[k]
            if val > cap:
                self.err(f"limits.{k} = {v} loosens the risk control ({cap}); strategies can only tighten limits")
            limits[k] = val

        # unused declarations are a smell, not an error
        used = set()
        for node in [*self.vars.values(), *filters, *when_all, *when_any, stop, target]:
            if node is not None:
                used |= E.names(node)
        for p in params:
            if p not in used and not any(p in E.names(n) for n in self.vars.values()):
                self.warnings.append(f"param {p!r} is declared but never used")
        for s in signals:
            if s not in used:
                self.warnings.append(f"signal {s!r} is declared but never used")

        ok = not self.errors
        strat = None
        if ok:
            strat = Strategy(
                text=self.text, hash=h, raw=d, id=sid, version=version, side=side,
                params=params, param_ranges=ranges, signals=signals, vars=self.vars,
                universe_news_today=news_today, universe_filters=filters,
                entry_window=window, entry_when_all=when_all, risk_per_trade=rpt,
                exit_stop=stop, exit_target=target, exit_when_any=when_any,
                flatten_at=flatten, mode=mode_, limits=limits,
                assumptions=list(d.get("assumptions") or []),
                suggestions=list(d.get("suggestions") or []),
                source=dict(d.get("source") or {}),
            )
        return Validation(ok, self.errors, self.warnings, strat, h)

    def _find_leverage(self, node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "leverage":
                    self.warnings.append(
                        f"'{path}{k}' ignored: leverage is not part of the language "
                        "(the risk controls cap gross exposure at the account level)"
                    )
                self._find_leverage(v, f"{path}{k}.")

    def check_new_name(self, kind: str, name: str):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", str(name)):
            self.err(f"{kind} name {name!r} must be lowercase snake_case")
        if name in SERIES | SESSION | POSITION | set(FUNCTIONS) | E.KEYWORDS:
            self.err(f"{kind} name {name!r} shadows a builtin")
        if kind == "var" and name in getattr(self, "params", {}):
            self.err(f"var {name!r} shadows a param")

    def check_params(self, raw) -> tuple[dict, dict]:
        params, ranges = {}, {}
        if not isinstance(raw, dict):
            self.err("params must be a mapping")
            return params, ranges
        for name, spec in raw.items():
            self.check_new_name("param", name)
            if not isinstance(spec, dict) or "value" not in spec or "range" not in spec:
                self.err(f"params.{name} must be {{value: ..., range: [lo, hi]}}")
                continue
            try:
                val = parse_number(spec["value"])
                rng = spec["range"]
                if not isinstance(rng, list) or len(rng) != 2:
                    raise ValueError("range must be [lo, hi]")
                lo, hi = parse_number(rng[0]), parse_number(rng[1])
            except ValueError as e:
                self.err(f"params.{name}: {e}")
                continue
            if is_pct(spec["value"]) != is_pct(rng[0]) or is_pct(rng[0]) != is_pct(rng[1]):
                self.err(f"params.{name}: value and range must use the same unit")
            if lo > hi:
                self.err(f"params.{name}: range lower bound exceeds upper bound")
            elif not lo <= val <= hi:
                self.err(f"params.{name}: value {spec['value']} is outside its range {rng}")
            params[name], ranges[name] = val, (lo, hi)
        return params, ranges

    def check_signals(self, raw) -> dict[str, SignalDecl]:
        out = {}
        if not isinstance(raw, dict):
            self.err("signals must be a mapping")
            return out
        for name, spec in raw.items():
            if name not in SIGNALS:
                self.err(f"unknown signal {name!r} (known: {sorted(SIGNALS)})")
                continue
            if not isinstance(spec, dict) or "from" not in spec:
                self.err(f"signals.{name} must declare from: <source@version>")
                continue
            src = spec["from"]
            if src not in SIGNAL_SOURCES or SIGNALS[name].source != src:
                self.err(f"signals.{name}: source must be {SIGNALS[name].source!r}")
            age = spec.get("max_age", "since_prior_close")
            if age not in MAX_AGES:
                try:
                    age = parse_duration(age)
                except ValueError:
                    self.err(f"signals.{name}.max_age must be since_prior_close, session, or a duration")
            decl = SignalDecl(name, src, age)
            if "default" in spec:
                decl.default, decl.has_default = spec["default"], True
                if not self._valid_signal_value(name, spec["default"]):
                    self.err(f"signals.{name}.default {spec['default']!r} is not a valid value")
            for k in set(spec) - {"from", "max_age", "default"}:
                self.err(f"unknown key signals.{name}.{k}")
            out[name] = decl
        return out

    @staticmethod
    def _valid_signal_value(name: str, v) -> bool:
        spec = SIGNALS[name]
        if spec.type == "bool":
            return isinstance(v, bool)
        if spec.type == "enum":
            return v in spec.values
        return isinstance(v, (int, float)) and not isinstance(v, bool) and spec.lo <= v <= spec.hi

    def check_var_cycles(self):
        state: dict[str, int] = {}

        def visit(v, stack):
            if state.get(v) == 2:
                return
            if state.get(v) == 1:
                self.err(f"vars form a cycle: {' -> '.join(stack + [v])}")
                return
            state[v] = 1
            for n in E.names(self.vars[v]):
                if n in self.vars:
                    visit(n, stack + [v])
            state[v] = 2

        for v in list(self.vars):
            visit(v, [])

    def closure_names(self, node: E.Node, seen=None) -> set[str]:
        """Names used by node, following vars."""
        seen = seen if seen is not None else set()
        out = set()
        for n in E.names(node):
            out.add(n)
            if n in self.vars and n not in seen:
                seen.add(n)
                out |= self.closure_names(self.vars[n], seen)
        return out

    def taint(self, var: str, pred, seen=None) -> bool:
        seen = seen or set()
        if var in seen:
            return False
        seen.add(var)
        for n in E.names(self.vars[var]):
            if pred(n) or (n in self.vars and self.taint(n, pred, seen)):
                return True
        return False

    def kind_of(self, node: E.Node) -> str:
        """Best-effort static type: num | bool | enum | ?"""
        if isinstance(node, (E.Cmp, E.In, E.BoolOp, E.Not, E.BoolLit)):
            return "bool"
        if isinstance(node, E.Call):
            sig = FUNCTIONS.get(node.fn)
            return sig.returns if sig else "?"
        if isinstance(node, E.Name):
            if node.id in SIGNALS:
                return SIGNALS[node.id].type
            if node.id in self.vars:
                return self.kind_of(self.vars[node.id])
            return "num"
        if isinstance(node, E.Index):
            return self.kind_of(node.base)
        return "num"

    def check_expr(self, where: str, node: E.Node, want: str | None,
                   signals_ok: bool = False, position_ok: bool = False):
        if want and self.kind_of(node) not in (want, "?"):
            self.err(f"{where}: expected a {want} expression, got {node.src!r}")
        for n in E.walk(node):
            if isinstance(n, E.Name):
                self._check_name(where, n.id, signals_ok, position_ok)
            elif isinstance(n, E.Call):
                sig = FUNCTIONS.get(n.fn)
                if not sig:
                    self.err(f"{where}: unknown function {n.fn!r}")
                    continue
                if len(n.args) != len(sig.args):
                    self.err(f"{where}: {n.fn}() takes {len(sig.args)} argument(s), got {len(n.args)}")
                    continue
                for a, kind in zip(n.args, sig.args):
                    if kind == "const" and not self._is_const(a):
                        self.err(f"{where}: {n.fn}() needs a whole-number literal or param, got {a.src!r}")
            elif isinstance(n, E.Cmp):
                for side in (n.l, n.r):
                    if self._bare_number(side):
                        self.err(
                            f"{where}: comparison {n.src!r} uses a bare number; thresholds must be params with declared ranges"
                        )
                for side in (n.l, n.r):
                    if self.kind_of(side) == "enum":
                        self.err(f"{where}: compare enum signals with 'in [...]', not {n.op!r}")
            elif isinstance(n, E.In):
                if not (isinstance(n.l, E.Name) and n.l.id in SIGNALS and SIGNALS[n.l.id].type == "enum"):
                    self.err(f"{where}: 'in [...]' needs an enum signal on the left, got {n.l.src!r}")
                else:
                    bad = [v for v in n.items if v not in SIGNALS[n.l.id].values]
                    if bad:
                        self.err(f"{where}: {bad} are not values of {n.l.id} ({list(SIGNALS[n.l.id].values)})")
            elif isinstance(n, E.Index):
                if not (isinstance(n.base, E.Name) and (n.base.id in SERIES or n.base.id in self.vars)):
                    self.err(f"{where}: x[n] works on bar series and vars only, got {n.base.src!r}")
                if n.n > 389:
                    self.err(f"{where}: bar offset {n.n} exceeds one session")

    def _check_name(self, where, name, signals_ok, position_ok):
        if name in self.params or name in SERIES or name in SESSION:
            return
        if name in POSITION:
            if not position_ok:
                self.err(f"{where}: {name} is a position builtin and may only be used in exit.*")
            return
        if name in self.vars:
            if not signals_ok and self.sig_taint.get(name):
                self.err(f"{where}: var {name!r} depends on a signal; signals may appear only in entry.when_all and exit.when_any")
            if not position_ok and self.pos_taint.get(name):
                self.err(f"{where}: var {name!r} depends on a position builtin; allowed in exit.* only")
            return
        if is_signal(name):
            if name not in self.signals:
                self.err(f"{where}: signal {name!r} is used but not declared under signals:")
            elif not signals_ok:
                self.err(f"{where}: signal {name!r} may appear only in entry.when_all and exit.when_any")
            return
        if name in FUNCTIONS:
            self.err(f"{where}: {name} is a function; call it as {name}(...)")
            return
        self.err(f"{where}: undeclared name {name!r}")

    def _is_const(self, node: E.Node) -> bool:
        if isinstance(node, E.Num):
            return float(node.value).is_integer() and node.value > 0 and "%" not in node.src
        return isinstance(node, E.Name) and node.id in self.params

    @staticmethod
    def _bare_number(node: E.Node) -> bool:
        if isinstance(node, E.Neg):
            node = node.x
        return isinstance(node, E.Num)


def validate(text: str, risk: RiskConfig) -> Validation:
    return _V(text, risk).run()
