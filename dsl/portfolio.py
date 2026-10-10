"""`kind: portfolio` strategy files: multi-day holdings with daily rules.

A portfolio strategy buys a fixed basket once (equal weight inside each group), holds it
overnight for a set number of sessions, and once a day, at `decide_at`, applies a short list
of rules to each symbol's move so far that day:

    holdings:  capital (of equity), max_holding (of the original allocation), groups {weight, symbols}
    schedule:  hold_sessions, optional start_date / end_date, optional decide_at
    rules:     first match wins; when: <condition>, do: hold | exit | exit_and_wait | {trim: p} | {add: p}
    reentry:   when: <condition>, step: p   (after exit_and_wait, rebuild p of the allocation per session)

Conditions use the same expression language as intraday files, over params and the daily
builtins below. Static limits are checked against the portfolio risk controls and may only tighten it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time

from common.config import RiskConfig
from common.units import is_pct, parse_hhmm, parse_pct

from . import expr as E
from .loader import file_hash

# Values a rule can read, computed per symbol at the daily decision time.
DAILY = {
    "day_change": "today's move: price at the decision time vs the prior close",
    "holding": "market value held now as a fraction of the symbol's original allocation",
    "sessions_held": "sessions since the strategy's first session (0 on the first day)",
    "gain": "price vs the average cost of the shares held (missing when nothing is held)",
}
ACTIONS = {"hold", "exit", "exit_and_wait"}  # bare actions
SIZED = {"trim", "add"}  # {action: param}
TOP_KEYS = {
    "dsl", "kind", "id", "version", "source", "assumptions", "suggestions", "holdings", "schedule",
    "params", "rules", "reentry", "execution", "limits",
}
REQUIRED = {"dsl", "kind", "id", "version", "holdings", "schedule", "params", "rules", "execution"}
SYMBOL = re.compile(r"[A-Z][A-Z.]{0,5}")


@dataclass
class Rule:
    when: E.Node
    action: str  # hold | exit | exit_and_wait | trim | add
    size: str | None = None  # param name for trim/add


@dataclass
class PortfolioStrategy:
    text: str
    hash: str
    raw: dict
    id: str
    version: int
    capital: float
    max_holding: float
    groups: dict[str, tuple[float, list[str]]]  # name -> (weight, symbols)
    weights: dict[str, float]  # symbol -> share of capital
    hold_sessions: int
    start_date: date | None
    end_date: date | None
    decide_at: time
    params: dict[str, float]
    param_ranges: dict[str, tuple[float, float]]
    rules: list[Rule]
    reentry_when: E.Node | None
    reentry_step: str | None
    mode: str
    limits: dict[str, float]
    assumptions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    kind: str = "portfolio"
    side: str = "long"
    signals: dict = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        return list(self.weights)

    @property
    def uses_signals(self) -> bool:
        return False

    def max_drawdown(self, risk: RiskConfig) -> float:
        cap = risk.portfolio.max_drawdown if risk.portfolio else 0.0
        return min(cap, self.limits.get("max_drawdown", cap))


def validate_portfolio(v, d: dict):
    """Fill validator `v` (a dsl.validator._V) from the parsed mapping `d`; return the Validation."""
    from .validator import MODES, Validation

    risk: RiskConfig = v.risk
    h = file_hash(v.text)
    for k in d:
        if k not in TOP_KEYS and k != "leverage":
            v.err(f"unknown top-level key {k!r}")
    for k in sorted(REQUIRED - set(d)):
        v.err(f"missing required key {k!r}")
    if v.errors:
        return Validation(False, v.errors, v.warnings, None, h)
    pr = risk.portfolio
    if pr is None:
        v.err("kind: portfolio is disabled: turn on 'Portfolio strategies enabled' on the dashboard Risk page")
        return Validation(False, v.errors, v.warnings, None, h)

    if str(d["dsl"]) != "0.1":
        v.err(f"unsupported dsl version {d['dsl']!r} (expected 0.1)")
    sid, version = d["id"], d["version"]
    if not isinstance(sid, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", sid):
        v.err("id must be lowercase snake_case")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        v.err("version must be a positive integer")

    v.raw_params = d.get("params") if isinstance(d.get("params"), dict) else {}
    params, ranges = v.check_params(d.get("params") or {})
    v.params, v.ranges, v.signals, v.vars = params, ranges, {}, {}
    v.sig_taint, v.pos_taint = {}, {}

    # holdings
    hold = d["holdings"] if isinstance(d["holdings"], dict) else {}
    if not hold:
        v.err("holdings must be a mapping with capital, max_holding and groups")
    capital = _pct(v, "holdings.capital", hold.get("capital"))
    max_holding = _pct(v, "holdings.max_holding", hold.get("max_holding", "100%"))
    for k in set(hold) - {"capital", "max_holding", "groups"}:
        v.err(f"unknown key holdings.{k}")
    groups: dict[str, tuple[float, list[str]]] = {}
    weights: dict[str, float] = {}
    raw_groups = hold.get("groups")
    if not isinstance(raw_groups, dict) or not raw_groups:
        v.err("holdings.groups must map a group name to {weight, symbols}")
        raw_groups = {}
    for name, g in raw_groups.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
            v.err(f"group name {name!r} must be lowercase snake_case")
        if not isinstance(g, dict) or set(g) != {"weight", "symbols"}:
            v.err(f"holdings.groups.{name} must be {{weight: <pct>, symbols: [...]}}")
            continue
        w = _pct(v, f"holdings.groups.{name}.weight", g["weight"])
        syms = g["symbols"]
        if not isinstance(syms, list) or not syms:
            v.err(f"holdings.groups.{name}.symbols must be a non-empty list")
            continue
        for s in syms:
            if not isinstance(s, str) or not SYMBOL.fullmatch(s):
                v.err(f"holdings.groups.{name}: {s!r} is not a ticker symbol")
            elif s in weights:
                v.err(f"symbol {s} appears more than once in holdings")
            elif w is not None:
                weights[s] = w / len(syms)
        groups[name] = (w or 0.0, list(syms))
    total = sum(w for w, _ in groups.values())
    if groups and abs(total - 1.0) > 1e-6:
        v.err(f"holdings group weights add up to {total:.2%}; they must add up to 100%")
    if len(weights) > pr.max_symbols:
        v.err(f"{len(weights)} symbols exceeds risk control portfolio.max_symbols ({pr.max_symbols})")
    if capital is not None and not 0 < capital <= pr.max_allocation:
        v.err(f"holdings.capital {capital:.0%} must be > 0 and <= risk control portfolio.max_allocation {pr.max_allocation:.0%}")
    if max_holding is not None and not 1.0 <= max_holding <= 3.0:
        v.err("holdings.max_holding must be between 100% and 300% of the original allocation")
    if capital and max_holding and capital * max_holding > pr.max_allocation + 1e-9:
        v.err(f"holdings.capital {capital:.0%} x max_holding {max_holding:.0%} = {capital * max_holding:.0%} of equity at the peak, "
              f"above risk control portfolio.max_allocation {pr.max_allocation:.0%}; lower capital or max_holding")
    if capital and max_holding and weights:
        top = max(weights, key=weights.get)
        peak = capital * weights[top] * max_holding
        if peak > pr.max_position + 1e-9:
            v.err(f"{top} could reach {peak:.1%} of equity (capital {capital:.0%} x weight {weights[top]:.1%} x max_holding "
                  f"{max_holding:.0%}), above risk control portfolio.max_position {pr.max_position:.0%}")

    # schedule
    sch = d["schedule"] if isinstance(d["schedule"], dict) else {}
    for k in set(sch) - {"hold_sessions", "start_date", "end_date", "decide_at"}:
        v.err(f"unknown key schedule.{k}")
    hold_sessions = sch.get("hold_sessions")
    if not isinstance(hold_sessions, int) or isinstance(hold_sessions, bool) or not 1 <= hold_sessions <= pr.max_hold_sessions:
        v.err(f"schedule.hold_sessions must be a whole number from 1 to risk control portfolio.max_hold_sessions ({pr.max_hold_sessions})")
        hold_sessions = 1
    start_date, end_date = _date(v, "schedule.start_date", sch.get("start_date")), _date(v, "schedule.end_date", sch.get("end_date"))
    if start_date and end_date and end_date <= start_date:
        v.err("schedule.end_date must be after schedule.start_date")
    decide_at = pr.decide_at
    if "decide_at" in sch:
        try:
            decide_at = parse_hhmm(sch["decide_at"])
            if not risk.entries[0] <= decide_at <= pr.decide_at:
                v.err(f"schedule.decide_at {sch['decide_at']} must be between {risk.entries[0]:%H:%M} and "
                      f"risk control portfolio.decide_at {pr.decide_at:%H:%M}")
        except ValueError as e:
            v.err(f"schedule.decide_at: {e}")

    # rules
    rules: list[Rule] = []
    raw_rules = d["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        v.err("rules must be a non-empty list of {when, do}")
        raw_rules = []
    for i, r in enumerate(raw_rules):
        where = f"rules[{i}]"
        if not isinstance(r, dict) or set(r) != {"when", "do"}:
            v.err(f"{where} must be {{when: <condition>, do: <action>}}")
            continue
        node = _cond(v, f"{where}.when", r["when"])
        action, size = _action(v, f"{where}.do", r["do"], params)
        if node is not None and action:
            rules.append(Rule(node, action, size))
    if any(r.action == "add" for r in rules) and max_holding is not None and max_holding <= 1.0:
        v.warnings.append("an add rule can never buy: holdings.max_holding is 100%")

    # reentry
    reentry_when = reentry_step = None
    re_ = d.get("reentry")
    waits = any(r.action == "exit_and_wait" for r in rules)
    if re_ is not None:
        if not isinstance(re_, dict) or set(re_) != {"when", "step"}:
            v.err("reentry must be {when: <condition>, step: <param>}")
        else:
            reentry_when = _cond(v, "reentry.when", re_["when"])
            reentry_step = _size(v, "reentry.step", re_["step"], params)
        if not waits:
            v.warnings.append("reentry is never used: no rule does exit_and_wait")
    elif waits:
        v.err("a rule uses exit_and_wait, so reentry {when, step} is required")

    # execution
    exe = d["execution"] or {}
    mode_ = exe.get("mode", "manual") if isinstance(exe, dict) else None
    if mode_ not in MODES:
        v.err(f"execution.mode must be one of {sorted(MODES)}")
    if mode_ == "auto":
        v.warnings.append("execution.mode 'auto' in the file is ignored: the registry starts at manual and the owner switches modes on the dashboard")
        mode_ = "manual"

    # limits: only tighten
    limits = {}
    for k, val in (d.get("limits") or {}).items():
        if k != "max_drawdown":
            v.err(f"unknown limit {k!r} (allowed for portfolio strategies: ['max_drawdown'])")
            continue
        x = _pct(v, f"limits.{k}", val)
        if x is not None:
            if not 0 < x <= pr.max_drawdown:
                v.err(f"limits.max_drawdown = {val} loosens risk control portfolio.max_drawdown ({pr.max_drawdown:.0%})")
            limits[k] = x

    used = set()
    for r in rules:
        used |= E.names(r.when) | ({r.size} if r.size else set())
    if reentry_when is not None:
        used |= E.names(reentry_when)
    if reentry_step:
        used.add(reentry_step)
    for p in params:
        if p not in used:
            v.warnings.append(f"param {p!r} is declared but never used")

    ok = not v.errors
    strat = None
    if ok:
        strat = PortfolioStrategy(
            text=v.text, hash=h, raw=d, id=sid, version=version, capital=capital, max_holding=max_holding,
            groups=groups, weights=weights, hold_sessions=hold_sessions, start_date=start_date, end_date=end_date,
            decide_at=decide_at, params=params, param_ranges=ranges, rules=rules, reentry_when=reentry_when,
            reentry_step=reentry_step, mode=mode_, limits=limits,
            assumptions=list(d.get("assumptions") or []), suggestions=list(d.get("suggestions") or []),
            source=dict(d.get("source") or {}),
        )
    return Validation(ok, v.errors, v.warnings, strat, h)


def _pct(v, where, raw) -> float | None:
    if raw is None:
        v.err(f"{where} is required")
        return None
    if not is_pct(raw):
        v.err(f"{where} must be a percent like '60%', got {raw!r}")
        return None
    return parse_pct(raw)


def _date(v, where, raw) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        v.err(f"{where} must be a quoted date like \"2026-12-11\"")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        v.err(f"{where}: {raw!r} is not a YYYY-MM-DD date")
        return None


def _cond(v, where, src) -> E.Node | None:
    node = v.parse_expr(where, src)
    if node is None:
        return None
    if v.kind_of(node) != "bool":
        v.err(f"{where}: expected a condition, got {node.src!r}")
    for n in E.walk(node):
        if isinstance(n, E.Name):
            if n.id not in v.params and n.id not in DAILY:
                v.err(f"{where}: unknown name {n.id!r} (portfolio rules can use params and {sorted(DAILY)})")
        elif isinstance(n, (E.Call, E.Index, E.In)):
            v.err(f"{where}: {n.src!r} is not available in portfolio rules (use params and {sorted(DAILY)})")
        elif isinstance(n, E.Cmp):
            for side in (n.l, n.r):
                if v._bare_number(side):
                    v.err(f"{where}: comparison {n.src!r} uses a bare number; thresholds must be params with declared ranges")
    return node


def _size(v, where, name, params) -> str | None:
    """Trim/add/step sizes are percent params whose whole range lies in (0%, 100%]."""
    if not isinstance(name, str) or name not in params:
        v.err(f"{where} must name a percent param, got {name!r}")
        return None
    spec = (v.raw_params or {}).get(name) or {}
    lo, hi = v.ranges.get(name, (params[name], params[name]))
    if not is_pct(spec.get("value")) or not 0 < lo <= hi <= 1.0:
        v.err(f"{where}: param {name!r} must be a percent with its range between 0% and 100%")
    return name


def _action(v, where, raw, params) -> tuple[str | None, str | None]:
    if isinstance(raw, str):
        if raw in ACTIONS:
            return raw, None
        if raw in SIZED:
            v.err(f"{where}: {raw} needs a size, e.g. {{{raw}: {raw}_size}}")
            return None, None
        v.err(f"{where}: unknown action {raw!r} (use hold, exit, exit_and_wait, {{trim: <param>}} or {{add: <param>}})")
        return None, None
    if isinstance(raw, dict) and len(raw) == 1:
        (k, val), = raw.items()
        if k in SIZED:
            return k, _size(v, f"{where}.{k}", val, params)
    v.err(f"{where}: unknown action {raw!r} (use hold, exit, exit_and_wait, {{trim: <param>}} or {{add: <param>}})")
    return None, None
