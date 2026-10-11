"""DSL 0.2 sections for `kind: portfolio` files.

0.2 keeps every 0.1 section and adds:

    authoring           review status of the defaults a compiler or designer proposed
    market_inputs       typed returns from outside the held basket: an index, or an equal-weight ticker basket
    assumptions         plain strings, or structured assumptions that the models service monitors (context only)
    portfolio_overlays  catalog scenarios bound to local inputs, params and groups, with deterministic responses
    monitoring          evidence, model tiers, budget and audit for monitored assumptions; shadow or enforce mode

Every field has one implemented meaning. Many fields are fixed-value declarations: the file states the
semantics the engine implements, and any other value is rejected rather than silently ignored.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from common.config import CONFIG_DIR
from common.units import is_pct, parse_duration, parse_pct

INDEXES = ("NASDAQ_COMPOSITE", "NASDAQ_100", "SP_500", "DOW_JONES_INDUSTRIAL", "RUSSELL_2000")
CATALOG_NAME = "scenario-catalog.yaml"
SNAKE = re.compile(r"[a-z][a-z0-9_]{0,63}")
TICKER = re.compile(r"[A-Z][A-Z.]{0,5}")

PRECEDENCE = ["account_safety", "horizon_exit", "symbol_full_exit", "merged_macro_and_group_reduction", "macro_buy_pause",
              "symbol_daily_rule", "macro_rebuild", "symbol_rebuild_or_initial_buy"]
RESOLUTION = {
    "partial_reduction_conflict": "largest_sell_fraction_of_one_snapshot_not_sum",
    "full_exit_conflict": "sell_all_and_keep_correct_exit_state",
    "reductions_precede_buys": True,
    "at_most_one_buy_per_symbol_per_session": True,
    "macro_rebuild_vs_daily_add": "larger_target_increment_not_sum",
    "macro_rebuild_vs_daily_hold": "macro_rebuild_wins_after_recovery_starts",
    "macro_rebuild_vs_daily_trim": "reduction_wins_defer_rebuild",
    "restricted_symbol_reentry": "existing_waiting_condition_then_20_percent_to_100_before_macro_topup",
    "target_reached_test": "market_value_vs_fixed_original_usd_cap",
    "price_appreciation_above_cap": "no_add_no_automatic_sell_without_a_sell_rule",
}
REENTRY_BLOCKS = {"macro_wait_for_positive_day", "same_session_group_reduction", "hard_risk", "missing_required_data"}
CONTEXT_ACTIONS = {"log", "notify_dashboard", "request_t2", "block_new_entries"}
OVERLAY_ACTIONS = {"trim_sleeve_and_rebuild", "liquidate_sleeve", "trim_group_to_cash"}
ALL_ACTIONS = CONTEXT_ACTIONS | OVERLAY_ACTIONS | {"cancel_strategy_pending_buys", "latch_buy_pause"}
TRIGGERS = {"macro_threshold_crossing", "new_relevant_macro_news"}
ASSUMPTION_KINDS = {"implementation_note", "market_thesis", "risk_condition", "data_assumption"}
TIER_PROVIDERS = {"t1": {"laya", "claude_haiku"}, "t2": {"claude_sonnet", "claude_opus"}}
EVIDENCE = {"macro_prices", "member_prices", "relevant_macro_news", "assumption_definitions", "position_state"}
T0_CHECKS = {"input_freshness", "comparable_price_basis", "ownership", "mode", "budget"}
AUDIT_FIELDS = {"trigger_facts", "references", "snapshot_id", "model_answers", "rule_priority", "target_qty", "filled_qty",
                "cash_reserve", "pause_state", "usage"}
RECHECK = {"strategy_version", "ownership", "approval", "risk", "prices", "broker_state"}


@dataclass
class MarketInput:
    name: str
    kind: str  # index | basket
    instrument: str | None = None
    symbols: list[str] = field(default_factory=list)
    max_age: float = 30.0
    required: bool = True


@dataclass
class Overlay:
    id: str
    scenario: str
    action: str  # trim_sleeve_and_rebuild | liquidate_sleeve | trim_group_to_cash
    metric: str
    operator: str  # lte | lt | gt | gte
    threshold_param: str | None = None
    threshold: float | None = None
    on: str = "daily_decision"  # every_fresh_observation | daily_decision
    before_initial_buy: bool = False
    skip_initial_funding_session: bool = False
    trim_param: str | None = None
    rebuild_step_param: str | None = None
    target_group: str | None = None
    symbols: list[str] = field(default_factory=list)
    anchor_return_param: str | None = None
    anchor_trim_param: str | None = None
    max_fraction: float = 1.0
    laya_question: str = ""

    @property
    def kind(self) -> str:
        return "rotation" if self.action == "trim_group_to_cash" else "macro"


@dataclass
class Question:
    id: str
    prompt: str
    type: str  # choice | boolean
    choices: list
    assessed_by: list[str]
    evidence_required: bool = False


@dataclass
class Assumption:
    id: str
    kind: str
    statement: str
    monitored: bool = False
    critical: bool = False
    interval: float | None = None
    on: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    result_max_age: float | None = None
    questions: list[Question] = field(default_factory=list)
    supported_when: tuple[str, list[dict]] | None = None  # ("all"|"any", [{question, operator, value}])
    contradicted_when: tuple[str, list[dict]] | None = None
    responses: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Monitoring:
    enabled: bool
    mode: str  # shadow | enforce
    required_symbols: list[str]
    deadline: float
    min_gap: float
    evidence: dict
    tiers: dict
    allowed_actions: set[str]
    on_missing_input: list[str]
    on_provider_unavailable: list[str]
    budget: dict
    audit: list[str]


@dataclass
class V2:
    authoring: dict
    inputs: dict[str, MarketInput]
    overlays: list[Overlay]
    assumptions: list[Assumption]
    monitoring: Monitoring | None
    catalog_hash: str
    reentry_blocked_by: set[str]

    @property
    def mode(self) -> str:
        return self.monitoring.mode if self.monitoring else "shadow"

    @property
    def review_required(self) -> bool:
        return self.authoring.get("status") == "proposed_requires_review"

    @property
    def index_instruments(self) -> list[str]:
        return sorted({i.instrument for i in self.inputs.values() if i.kind == "index"})

    @property
    def input_symbols(self) -> list[str]:
        return sorted({sym for i in self.inputs.values() for sym in i.symbols})

    def monitored(self) -> list[Assumption]:
        return [a for a in self.assumptions if a.monitored]


def load_catalog() -> tuple[dict[str, dict], str]:
    """Portfolio scenarios by id, and the catalog file's SHA-256 ('' when the file is missing)."""
    import yaml

    p = CONFIG_DIR / CATALOG_NAME
    if not p.exists():
        return {}, ""
    text = p.read_text()
    data = yaml.safe_load(text) or {}
    return {sc["id"]: sc for sc in data.get("portfolio_scenarios") or []}, hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- helpers
class _Ctx:
    """What the 0.2 checks need from the 0.1 validation of the same file."""

    def __init__(self, v, d, params, groups, weights, max_holding):
        self.v, self.d, self.params, self.groups, self.weights, self.max_holding = v, d, params, groups, weights, max_holding
        self.raw_params = d.get("params") if isinstance(d.get("params"), dict) else {}
        self.used_params: set[str] = set()

    def err(self, msg):
        self.v.err(msg)


def fixed(c: _Ctx, where: str, raw, spec: dict, optional: tuple = ()) -> dict:
    """Check a mapping against {key: allowed}. allowed: a value, a tuple of values, or a function(where, value) -> parsed."""
    if not isinstance(raw, dict):
        c.err(f"{where} must be a mapping")
        return {}
    raw = {("on" if k is True else k): x for k, x in raw.items()}  # YAML 1.1 reads a bare `on:` key as true
    out = {}
    for k in raw:
        if k not in spec:
            c.err(f"unknown key {where}.{k}")
    for k, allowed in spec.items():
        if k not in raw:
            if k not in optional:
                c.err(f"{where}.{k} is required")
            continue
        val = raw[k]
        if callable(allowed):
            out[k] = allowed(f"{where}.{k}", val)
        elif isinstance(allowed, tuple):
            if val not in allowed:
                c.err(f"{where}.{k} must be one of {list(allowed)}, got {val!r}")
            out[k] = val
        else:
            if val != allowed or type(val) is not type(allowed):
                c.err(f"{where}.{k}: only {allowed!r} is supported, got {val!r}")
            out[k] = val
    return out


def _bool(c):
    def f(where, val):
        if not isinstance(val, bool):
            c.err(f"{where} must be true or false")
            return False
        return val
    return f


def _dur(c, lo=1.0, hi=86400.0):
    def f(where, val):
        try:
            x = parse_duration(val)
        except ValueError as e:
            c.err(f"{where}: {e}")
            return None
        if not lo <= x <= hi:
            c.err(f"{where} must be between {lo:g}s and {hi:g}s")
        return x
    return f


def _int(c, lo, hi):
    def f(where, val):
        if not isinstance(val, int) or isinstance(val, bool) or not lo <= val <= hi:
            c.err(f"{where} must be a whole number from {lo} to {hi}")
            return lo
        return val
    return f


def _num(c, lo, hi):
    def f(where, val):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= val <= hi:
            c.err(f"{where} must be a number from {lo:g} to {hi:g}")
            return lo
        return float(val)
    return f


def _text(c, max_len=2000):
    def f(where, val):
        if not isinstance(val, str) or not val.strip() or len(val) > max_len:
            c.err(f"{where} must be non-empty text (at most {max_len} characters)")
            return ""
        return val
    return f


def _subset(c, allowed: set, exact: list | None = None, nonempty=True):
    def f(where, val):
        if not isinstance(val, list) or (nonempty and not val) or len(set(map(str, val))) != len(val):
            c.err(f"{where} must be a list without duplicates")
            return []
        bad = [x for x in val if x not in allowed]
        if bad:
            c.err(f"{where}: unsupported {bad} (allowed: {sorted(allowed)})")
        if exact is not None and set(val) != set(exact):
            c.err(f"{where} must list exactly {exact}")
        return list(val)
    return f


def _exact_list(c, want: list):
    def f(where, val):
        if val != want:
            c.err(f"{where} must be exactly {want} (the order the engine applies)")
        return val
    return f


def _pct_word(x: float) -> str:
    return f"{abs(x) * 100:g}".replace(".", "_")


def _param(c: _Ctx, where, name, *, size=False, negative=False, positive=False) -> str | None:
    if not isinstance(name, str) or name not in c.params:
        c.err(f"{where} must name a declared param, got {name!r}")
        return None
    c.used_params.add(name)
    spec = c.raw_params.get(name) or {}
    lo, hi = c.v.ranges.get(name, (c.params[name], c.params[name]))
    if not is_pct(spec.get("value")):
        c.err(f"{where}: param {name!r} must be a percent")
    elif size and not 0 < lo <= hi <= 1.0:
        c.err(f"{where}: param {name!r} must have its range between 0% and 100%")
    elif negative and not (c.params[name] < 0 and hi < 0):
        c.err(f"{where}: param {name!r} must be a negative return with a negative range (e.g. -2%)")
    elif positive and not (c.params[name] > 0 and lo > 0):
        c.err(f"{where}: param {name!r} must be a positive return with a positive range")
    return name


# ---------------------------------------------------------------- top-level sections
def check_source(c: _Ctx, raw):
    if raw is None:
        return
    s = fixed(c, "source", raw, {"text": _text(c, 20_000), "based_on": lambda w, v: v, "compiled_by": _text(c, 200),
                                 "prompt": _text(c, 2000)}, optional=("based_on", "compiled_by", "prompt"))
    b = s.get("based_on")
    if b is not None:
        fixed(c, "source.based_on", b, {"id": _text(c, 64), "version": _int(c, 1, 10_000)})


def check_authoring(c: _Ctx, raw) -> dict:
    if raw is None:
        return {}

    def choices(where, val):
        if not isinstance(val, dict):
            c.err(f"{where} must map a choice name to its value")
            return {}
        for k, x in val.items():
            ok = isinstance(x, (str, int, float, bool)) or (isinstance(x, list) and all(isinstance(y, (str, int, float)) for y in x))
            if not SNAKE.fullmatch(str(k)) or not ok:
                c.err(f"{where}.{k} must be a snake_case name with a plain value or list")
        return dict(val)

    return fixed(c, "authoring", raw, {"status": ("proposed_requires_review", "reviewed"), "proposed_defaults": choices,
                                       "confirmed_choices": choices, "note": _text(c, 4000)},
                 optional=("proposed_defaults", "confirmed_choices", "note"))


def check_allocation_basis(c: _Ctx, raw):
    fixed(c, "holdings.allocation_basis", raw, {
        "equity": "first_actual_buy", "original_allocation": "fixed_per_symbol_usd_at_inception",
        "trims_do_not_reset_original_allocation": True, "proceeds": "strategy_owned_cash"})


SCHEDULE_V2 = {"timezone": "America/New_York", "calendar": "exchange_calendar",
               "session_count": "first_funded_session_counts_as_one", "early_close": "15min_before_regular_close",
               "missed_decision": "skip_and_notify_no_next_day_catchup", "horizon_action": "liquidate_sleeve_and_finish"}


def check_schedule_v2(c: _Ctx, sch: dict):
    for k, want in SCHEDULE_V2.items():
        if k in sch and (sch[k] != want):
            c.err(f"schedule.{k}: only {want!r} is supported, got {sch[k]!r}")


def check_reentry_v2(c: _Ctx, re_: dict) -> set[str]:
    spec = {"size_basis": "original_allocation_usd", "once_started": "every_session_until_100_percent_rebuilt",
            "while_building": "exit_rules_only"}
    for k, want in spec.items():
        if k in re_ and re_[k] != want:
            c.err(f"reentry.{k}: only {want!r} is supported, got {re_[k]!r}")
    blocked = re_.get("blocked_by", sorted(REENTRY_BLOCKS))
    _subset(c, REENTRY_BLOCKS, exact=sorted(REENTRY_BLOCKS))("reentry.blocked_by", blocked)
    return set(blocked) if isinstance(blocked, list) else set(REENTRY_BLOCKS)


def check_inputs(c: _Ctx, raw) -> dict[str, MarketInput]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not raw:
        c.err("market_inputs must map an input name to its definition")
        return {}
    out = {}
    for name, spec in raw.items():
        where = f"market_inputs.{name}"
        if not SNAKE.fullmatch(str(name)):
            c.err(f"{where}: name must be lowercase snake_case")
        if name in c.params:
            c.err(f"{where}: name shadows a param")
        if not isinstance(spec, dict):
            c.err(f"{where} must be a mapping")
            continue
        common = {"source": None, "measure": None, "reference": "previous_regular_session_close", "observation": None,
                  "max_age": _dur(c, 5, 600)}
        if "instrument" in spec:
            got = fixed(c, where, spec, {**common, "instrument": INDEXES, "instrument_class": "index",
                                         "source": "account_configured_index_feed", "measure": "simple_return",
                                         "observation": "fresh_verified_regular_session_value", "required": _bool(c),
                                         "silent_proxy_fallback": "prohibited"})
            out[name] = MarketInput(name, "index", instrument=got.get("instrument"), max_age=got.get("max_age") or 30.0,
                                    required=bool(got.get("required")))
        elif "symbols" in spec:
            def syms(w, val):
                if not isinstance(val, list) or not 1 <= len(val) <= 20 or len(set(val)) != len(val):
                    c.err(f"{w} must be a list of 1 to 20 distinct tickers")
                    return []
                for x in val:
                    if not isinstance(x, str) or not TICKER.fullmatch(x):
                        c.err(f"{w}: {x!r} is not a ticker symbol")
                return list(val)
            got = fixed(c, where, spec, {**common, "symbols": syms, "source": "account_configured_equity_feed",
                                         "held_membership_required": _bool(c),
                                         "measure": "equal_weight_mean_of_member_simple_returns",
                                         "weights": "fixed_equal_not_position_weights",
                                         "observation": "synchronized_completed_1min_bars",
                                         "missing_member": "unknown_do_not_reweight",
                                         "corporate_action_basis": "comparable_adjusted_prices_and_prior_close",
                                         "required_at": "daily_decision"})
            members = got.get("symbols") or []
            if got.get("held_membership_required") and set(members) - set(c.weights):
                c.err(f"{where}: held_membership_required is true but {sorted(set(members) - set(c.weights))} are not held")
            out[name] = MarketInput(name, "basket", symbols=members, max_age=got.get("max_age") or 90.0, required=True)
        else:
            c.err(f"{where} needs either instrument (an index) or symbols (an equal-weight ticker basket)")
    return out


def check_assumptions(c: _Ctx, raw, tiers_defined: set[str], allowed_actions: set[str]) -> tuple[list[Assumption], list[str]]:
    """Structured or plain assumptions; returns (structured, plain-text lines for display)."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        c.err("assumptions must be a list")
        return [], []
    out, lines, ids = [], [], set()
    for i, a in enumerate(raw):
        where = f"assumptions[{i}]"
        if isinstance(a, str):
            lines.append(a)
            continue
        if not isinstance(a, dict):
            c.err(f"{where} must be text or a mapping")
            continue
        aid = a.get("id")
        if not isinstance(aid, str) or not SNAKE.fullmatch(aid) or aid in ids:
            c.err(f"{where}.id must be a unique snake_case name")
            aid = f"assumption_{i}"
        ids.add(aid)
        where = f"assumptions.{aid}"
        monitored = a.get("monitored", False)
        base = {"id": lambda w, v: v, "kind": tuple(sorted(ASSUMPTION_KINDS)), "statement": _text(c), "monitored": _bool(c),
                "critical": _bool(c)}
        if monitored is True:
            spec = {**base, "evaluation": lambda w, v: v, "questions": lambda w, v: v, "assessment": lambda w, v: v,
                    "responses": lambda w, v: v}
            got = fixed(c, where, a, spec, optional=("critical",))
        else:
            got = fixed(c, where, a, base, optional=("monitored", "critical"))
            if got.get("critical"):
                c.err(f"{where}: critical needs monitored: true")
        x = Assumption(aid, got.get("kind") or "implementation_note", got.get("statement") or "", bool(monitored),
                       bool(got.get("critical")))
        lines.append(f"{aid}: {x.statement}")
        if monitored is True:
            ev = fixed(c, f"{where}.evaluation", got.get("evaluation"),
                       {"interval": _dur(c, 60, 86400), "on": _subset(c, TRIGGERS), "tiers": _subset(c, {"t1", "t2"}),
                        "result_max_age": _dur(c, 60, 86400)}, optional=("on",))
            x.interval, x.on, x.tiers, x.result_max_age = ev.get("interval"), ev.get("on") or [], ev.get("tiers") or [], ev.get("result_max_age")
            for t in set(x.tiers) - tiers_defined:
                c.err(f"{where}.evaluation.tiers: {t} is not defined in monitoring.tiers")
            x.questions = _questions(c, where, got.get("questions"), set(x.tiers))
            x.supported_when, x.contradicted_when = _assessment(c, where, got.get("assessment"), x.questions)
            resp = fixed(c, f"{where}.responses", got.get("responses"),
                         {k: _subset(c, CONTEXT_ACTIONS) for k in ("on_supported", "on_contradicted", "on_uncertain")})
            for k, acts in resp.items():
                if "block_new_entries" in acts and not x.critical:
                    c.err(f"{where}.responses.{k}: block_new_entries needs critical: true")
                for act in set(acts) - allowed_actions:
                    c.err(f"{where}.responses.{k}: {act} is not in monitoring.responses.allowed_actions")
            x.responses = resp
        out.append(x)
    return out, lines


def _questions(c, where, raw, tiers: set[str]) -> list[Question]:
    if not isinstance(raw, list) or not raw:
        c.err(f"{where}.questions must be a non-empty list")
        return []
    out, ids = [], set()
    for k, q in enumerate(raw):
        w = f"{where}.questions[{k}]"
        got = fixed(c, w, q, {"id": lambda ww, v: v, "prompt": _text(c, 1000), "type": ("choice", "boolean"),
                              "choices": lambda ww, v: v, "assessed_by": _subset(c, {"t1", "t2"}), "evidence_required": _bool(c)},
                    optional=("choices", "evidence_required"))
        qid = got.get("id")
        if not isinstance(qid, str) or not SNAKE.fullmatch(qid) or qid in ids:
            c.err(f"{w}.id must be a unique snake_case name")
            continue
        ids.add(qid)
        choices = [True, False]
        if got.get("type") == "choice":
            ch = got.get("choices")
            if not isinstance(ch, list) or len(ch) < 2 or not all(isinstance(x, str) and SNAKE.fullmatch(x) for x in ch) or len(set(ch)) != len(ch):
                c.err(f"{w}.choices must list at least two distinct snake_case answers")
                ch = []
            choices = list(ch)
        elif "choices" in (q or {}):
            c.err(f"{w}.choices is only for type: choice")
        for t in set(got.get("assessed_by") or []) - tiers:
            c.err(f"{w}.assessed_by: {t} is not in the assumption's evaluation.tiers")
        out.append(Question(qid, got.get("prompt") or "", got.get("type") or "choice", choices, got.get("assessed_by") or [],
                            bool(got.get("evidence_required"))))
    return out


def _assessment(c, where, raw, questions: list[Question]):
    qs = {q.id: q for q in questions}

    def rule(w, val):
        if not isinstance(val, dict) or len(val) != 1 or next(iter(val)) not in ("all", "any"):
            c.err(f"{w} must be {{all: [...]}} or {{any: [...]}}")
            return None
        (op, conds), = val.items()
        if not isinstance(conds, list) or not conds:
            c.err(f"{w}.{op} must be a non-empty list of conditions")
            return None
        for k, cond in enumerate(conds):
            got = fixed(c, f"{w}.{op}[{k}]", cond, {"question": tuple(qs), "operator": ("eq", "ne", "in"), "value": lambda ww, v: v})
            q = qs.get(got.get("question"))
            if q is None:
                continue
            vals = got.get("value") if got.get("operator") == "in" else [got.get("value")]
            if not isinstance(vals, list) or any(x not in q.choices or type(x) is not type(q.choices[0]) for x in vals):
                c.err(f"{w}.{op}[{k}].value must be among {q.id}'s answers {q.choices}")
        return op, conds

    got = fixed(c, f"{where}.assessment", raw, {"supported_when": rule, "contradicted_when": rule, "otherwise": "uncertain",
                                                 "conflicting_rules": "uncertain"})
    return got.get("supported_when"), got.get("contradicted_when")


def check_overlays(c: _Ctx, raw, inputs: dict[str, MarketInput], catalog: dict[str, dict]) -> list[Overlay]:
    if raw is None:
        return []
    top = fixed(c, "portfolio_overlays", raw, {"precedence": _exact_list(c, PRECEDENCE), "rules": lambda w, v: v,
                                               "resolution": lambda w, v: v})
    fixed(c, "portfolio_overlays.resolution", top.get("resolution"), dict(RESOLUTION))
    rules = top.get("rules")
    if not isinstance(rules, list) or not rules:
        c.err("portfolio_overlays.rules must be a non-empty list")
        return []
    out, ids = [], set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            c.err(f"portfolio_overlays.rules[{i}] must be a mapping")
            continue
        oid = r.get("id")
        if not isinstance(oid, str) or not SNAKE.fullmatch(oid) or oid in ids:
            c.err(f"portfolio_overlays.rules[{i}].id must be a unique snake_case name")
            continue
        ids.add(oid)
        sc = catalog.get(r.get("scenario_ref"))
        if sc is None:
            c.err(f"overlay {oid}: scenario_ref {r.get('scenario_ref')!r} is not in config/{CATALOG_NAME} "
                  f"(available: {sorted(catalog)})")
            continue
        o = _overlay(c, f"overlay {oid}", oid, r, sc, inputs)
        if o:
            out.append(o)
    if sum(o.kind == "macro" for o in out) > 1:
        c.err("at most one macro overlay (trim_sleeve_and_rebuild or liquidate_sleeve) is supported per strategy")
    return out


def _overlay(c: _Ctx, where, oid, r, sc, inputs) -> Overlay | None:
    got = fixed(c, where, r, {"id": lambda w, v: v, "scenario_ref": lambda w, v: v, "bindings": lambda w, v: v,
                              "evaluation": lambda w, v: v, "numeric_condition": lambda w, v: v,
                              "routing": sc.get("routing", "numeric_mandatory_laya_context"), "response": lambda w, v: v,
                              "idempotency": lambda w, v: v})
    params = sc.get("parameters") or {}
    b = got.get("bindings")
    if not isinstance(b, dict) or set(b) != set(params):
        c.err(f"{where}.bindings must bind exactly {sorted(params)} (scenario {sc['id']})")
        return None
    metric = threshold_param = target_group = None
    for k, p in params.items():
        val = b[k]
        if p["type"] == "market_input_ref":
            inp = inputs.get(val)
            if inp is None:
                c.err(f"{where}.bindings.{k}: {val!r} is not a market_inputs name")
                return None
            for req in p.get("requires") or []:
                if req in ("synchronized_members", "fixed_weights") and inp.kind != "basket":
                    c.err(f"{where}.bindings.{k}: scenario needs {req}, so {val!r} must be a symbols basket")
            metric = val
        elif p["type"] == "negative_return_param_ref":
            threshold_param = _param(c, f"{where}.bindings.{k}", val, negative=True)
        elif p["type"] == "holdings_group_ref":
            if val not in c.groups:
                c.err(f"{where}.bindings.{k}: {val!r} is not a holdings group")
                return None
            target_group = val
        else:
            c.err(f"{where}: catalog parameter type {p['type']!r} is not implemented")
            return None
    when = sc.get("when") or {}
    nc = got.get("numeric_condition")
    if not isinstance(nc, dict) or nc.get("operator") != when.get("operator"):
        c.err(f"{where}.numeric_condition.operator must be {when.get('operator')!r} (scenario {sc['id']})")
        return None
    o = Overlay(oid, sc["id"], "", metric, when["operator"], laya_question=sc.get("laya_question", ""))
    if "threshold_binding" in when:
        fixed(c, f"{where}.numeric_condition", nc, {"operator": when["operator"], "threshold_param": lambda w, v: v})
        if nc.get("threshold_param") != threshold_param:
            c.err(f"{where}.numeric_condition.threshold_param must be the bound {when['threshold_binding']} ({threshold_param!r})")
        o.threshold_param = threshold_param
    else:
        fixed(c, f"{where}.numeric_condition", nc, {"operator": when["operator"], "threshold": when["threshold"]})
        o.threshold = parse_pct(when["threshold"])
    resp = got.get("response")
    action = resp.get("action") if isinstance(resp, dict) else None
    if action not in (sc.get("supported_responses") or []):
        c.err(f"{where}.response.action must be one of {sc.get('supported_responses')} (scenario {sc['id']})")
        return None
    o.action = action
    idem = got.get("idempotency")
    last = {"symbol", f"{target_group}_symbol"} if target_group else {"symbol"}
    if not (isinstance(idem, list) and len(idem) == 4 and idem[:3] == ["strategy_hash", "session_id", "overlay_id"] and idem[3] in last):
        c.err(f"{where}.idempotency must be [strategy_hash, session_id, overlay_id, symbol]")
    if o.kind == "macro":
        _macro(c, where, o, got.get("evaluation"), resp, inputs)
    else:
        o.target_group = target_group
        o.symbols = list(c.groups[target_group][1])
        _rotation(c, where, o, got.get("evaluation"), resp)
    return o


def _macro(c: _Ctx, where, o: Overlay, ev, resp, inputs):
    e = fixed(c, f"{where}.evaluation", ev, {"on": ("every_fresh_observation", "daily_decision"), "session": "regular",
                                             "before_initial_buy": _bool(c)}, optional=("before_initial_buy",))
    o.on, o.before_initial_buy = e.get("on") or "daily_decision", bool(e.get("before_initial_buy"))
    if o.on == "every_fresh_observation" and inputs[o.metric].kind != "index":
        c.err(f"{where}.evaluation.on: every_fresh_observation needs an index input (baskets are read at the daily decision)")
    common = {"action": o.action, "scope": "all_strategy_owned_holdings", "block_buys_immediately": True,
              "cancel": "strategy_owned_pending_buys", "includes_other_strategies": False}
    if o.action == "liquidate_sleeve":
        fixed(c, f"{where}.response", resp, common)
        return
    r = fixed(c, f"{where}.response", resp, {**common, "sizing": lambda w, v: v, "state_machine": lambda w, v: v})
    sz = fixed(c, f"{where}.response.sizing", r.get("sizing"), {"trim_param": lambda w, v: v, "basis": "current_shares_at_trigger_snapshot"})
    o.trim_param = _param(c, f"{where}.response.sizing.trim_param", sz.get("trim_param"), size=True)
    thr = c.params.get(o.threshold_param or "", 0.0)
    trim = c.params.get(o.trim_param or "", 0.0)
    mh = c.max_holding or 1.0

    def one_of(*ok):
        def f(w, v):
            if v not in ok:
                c.err(f"{w} must be {' or '.join(repr(x) for x in ok)} (it must agree with the params), got {v!r}")
            return v
        return f

    spec = {
        "phases": _exact_list(c, ["normal", "reduction_pending", "awaiting_positive_session", "rebuilding"]),
        "recovery_observation": "daily_decision_on_a_later_exchange_session",
        "recovery_requires": "all_macro_reduction_orders_reconciled",
        "start_rebuilding_when": one_of(f"{o.metric}_is_strictly_positive", "metric_is_strictly_positive"),
        "rebuild_step_param": lambda w, v: v,
        "rebuild_step_basis": "original_allocation_usd_per_symbol",
        "rebuild_target": "holdings.max_holding_times_original_allocation",
        "once_started": "each_session_even_if_not_positive_unless_retriggered",
        "cash_limit": "unreserved_strategy_cash_and_account_risk",
        "no_cash_or_buy_block": "defer_without_claiming_completion",
        "complete": "all_eligible_symbols_at_target_within_rounding_tolerance",
        "retrigger": one_of(f"verified_minus_{_pct_word(thr)}_percent_on_new_session", "verified_threshold_crossing_on_new_session"),
        "retrigger_response": one_of(f"trim_current_shares_{_pct_word(trim)}_percent_and_restart_wait",
                                     "trim_current_shares_and_restart_wait"),
        "same_session_repeated_crossing": "no_second_trim",
        "symbol_full_exit": "preserve_symbol_wait_or_closed_state_never_override",
        "eligible_symbols": "held_at_macro_trigger_and_not_later_closed",
        "before_initial_buy": "block_initial_funding_wait_for_positive_session",
        "initial_funding_after_wait": one_of(f"original_100_percent_allocation_not_macro_{_pct_word(mh)}_percent_rebuild",
                                             "original_100_percent_allocation_not_macro_rebuild_target"),
    }
    optional = () if o.before_initial_buy else ("before_initial_buy", "initial_funding_after_wait")
    sm = fixed(c, f"{where}.response.state_machine", r.get("state_machine"), spec, optional=optional)
    o.rebuild_step_param = _param(c, f"{where}.response.state_machine.rebuild_step_param", sm.get("rebuild_step_param"), size=True)
    if (c.max_holding or 1.0) <= 1.0:
        c.v.warnings.append(f"{where}: max_holding is 100%, so the macro rebuild only restores the original allocation")


def _rotation(c: _Ctx, where, o: Overlay, ev, resp):
    e = fixed(c, f"{where}.evaluation", ev, {"on": "daily_decision", "once_per_session": True,
                                             "skip_initial_funding_session": _bool(c)})
    o.on, o.skip_initial_funding_session = "daily_decision", bool(e.get("skip_initial_funding_session"))
    g = o.target_group
    r = fixed(c, f"{where}.response", resp, {
        "action": "trim_group_to_cash", "target_group": g, "sizing": lambda w, v: v, "proceeds": "locked_strategy_cash",
        "reserve_attribution": "actual_filled_proceeds_up_to_requested_rotation_quantity",
        "prohibit_hardware_purchase": True,
        f"{g}_buys_after_trim": "next_session_subject_to_unreserved_cash_and_other_buy_gates",
        "daily_trim_conflict": "larger_fraction_of_same_snapshot_not_sum",
        "symbol_full_exit_conflict": "full_exit_wins",
        f"{g}_add_or_rebuild_conflict": "reduction_wins_no_same_session_buy"})

    def frac(w, v):
        if not is_pct(v) or not 0 < parse_pct(v) <= 1.0:
            c.err(f"{w} must be a percent in (0%, 100%]")
            return 1.0
        return parse_pct(v)

    sz = fixed(c, f"{where}.response.sizing", r.get("sizing"), {
        "method": "proportional_positive_return", "anchor_return_param": lambda w, v: v, "anchor_trim_param": lambda w, v: v,
        "maximum_fraction": frac, "basis": "each_symbol_current_shares_at_single_decision_snapshot",
        "aggregation": "one_group_trim_not_one_trim_per_hardware_symbol",
        "whole_shares": "floor_requested_sell_quantity_log_subshare_remainder"})
    o.anchor_return_param = _param(c, f"{where}.response.sizing.anchor_return_param", sz.get("anchor_return_param"), positive=True)
    o.anchor_trim_param = _param(c, f"{where}.response.sizing.anchor_trim_param", sz.get("anchor_trim_param"), size=True)
    o.max_fraction = sz.get("maximum_fraction") or 1.0


def check_monitoring(c: _Ctx, raw, inputs: dict[str, MarketInput], v2_symbols: list[str]) -> Monitoring | None:
    if raw is None:
        return None
    m = fixed(c, "monitoring", raw, {
        "enabled": _bool(c), "mode": ("shadow", "enforce"), "scope": lambda w, v: v, "required_symbols": lambda w, v: v,
        "index_feed_required": lambda w, v: v, "scenarios": lambda w, v: v, "evaluation": lambda w, v: v,
        "evidence": lambda w, v: v, "tiers": lambda w, v: v, "outputs": lambda w, v: v, "responses": lambda w, v: v,
        "budget": lambda w, v: v, "audit": lambda w, v: v}, optional=("index_feed_required", "scope"))
    if "scope" in m:
        fixed(c, "monitoring.scope", m["scope"], {"held_positions": _bool(c), "pending_orders": _bool(c), "market_inputs": _bool(c)})
    req = m.get("required_symbols")
    if not isinstance(req, list) or sorted(set(req)) != sorted(v2_symbols):
        c.err(f"monitoring.required_symbols must list every held and market-input ticker: {sorted(v2_symbols)}")
    idx = sorted({i.instrument for i in inputs.values() if i.kind == "index"})
    feeds = m.get("index_feed_required")
    feeds = [feeds] if isinstance(feeds, str) else feeds
    if (feeds or []) != idx and sorted(feeds or []) != idx:
        c.err(f"monitoring.index_feed_required must name the index inputs {idx}")
    fixed(c, "monitoring.scenarios", m.get("scenarios"), {"catalog": CATALOG_NAME, "selected_by": "portfolio_overlays.rules.scenario_ref",
                                                          "catalog_hash": "pinned_at_strategy_activation"})
    ev = fixed(c, "monitoring.evaluation", m.get("evaluation"), {"timezone": "America/New_York", "deadline": _dur(c, 5, 600),
                                                                 "min_gap": _dur(c, 10, 3600)})
    evid = fixed(c, "monitoring.evidence", m.get("evidence"), {
        "as_of": "evaluation_time", "allow_future_data": False, "include": _subset(c, EVIDENCE), "news_content": lambda w, v: v,
        "document_instructions": "treat_as_untrusted_data"})
    news = fixed(c, "monitoring.evidence.news_content", evid.get("news_content"), {
        "enabled": _bool(c), "max_articles": _int(c, 0, 20), "max_total_characters": _int(c, 0, 50_000)})
    evid["news_content"] = news
    tiers_raw = m.get("tiers") if isinstance(m.get("tiers"), dict) else {}
    tiers = {}
    for name in tiers_raw:
        if name not in ("t0", "t1", "t2"):
            c.err(f"unknown monitoring tier {name!r} (t0, t1, t2)")
    if "t0" in tiers_raw:
        tiers["t0"] = fixed(c, "monitoring.tiers.t0", tiers_raw["t0"], {"provider": "deterministic", "checks": _subset(c, T0_CHECKS)})
    for name, profile in (("t1", "typed_decisions"), ("t2", "deep_assessment")):
        if name not in tiers_raw:
            continue
        spec = {"provider": tuple(sorted(TIER_PROVIDERS[name])), "questions": "from_assumptions_assigned_to_this_tier",
                "model_profile": profile, "timeout": _dur(c, 1, 120), "retries": _int(c, 0, 2),
                "confidence_floor": _num(c, 0.0, 1.0), "result_max_age": _dur(c, 60, 86400)}
        if name == "t2":
            spec["escalate_on"] = _subset(c, {"material_macro_news", "t1_uncertain_on_threshold_crossing"})
        tiers[name] = fixed(c, f"monitoring.tiers.{name}", tiers_raw[name], spec, optional=("escalate_on",))
    fixed(c, "monitoring.outputs", m.get("outputs"), {
        "required_per_question": _exact_list(c, ["assumption_id", "question_id", "answer", "confidence", "evidence_ids", "rationale"]),
        "statuses": _exact_list(c, ["supported", "contradicted", "uncertain", "not_applicable"]),
        "numeric_overlay_status_independent_of_semantic_assessment": True})
    resp = fixed(c, "monitoring.responses", m.get("responses"), {
        "allowed_actions": _subset(c, ALL_ACTIONS), "on_missing_required_market_input": _subset(c, {"block_new_entries", "notify_dashboard", "log"}),
        "on_provider_unavailable": _subset(c, {"log", "notify_dashboard"}), "model_failure_cannot_disable_numeric_reduction": True,
        "shadow_mode": "audit_only_no_orders_or_buy_pauses"})
    if "block_new_entries" not in (resp.get("on_missing_required_market_input") or []):
        c.err("monitoring.responses.on_missing_required_market_input must include block_new_entries (missing inputs always block buys)")
    budget = fixed(c, "monitoring.budget", m.get("budget"), {
        "inherit_account_caps": True, "max_calls_per_ticker_per_hour": lambda w, v: v, "max_paid_calls_per_strategy_per_day": _int(c, 0, 200),
        "max_estimated_cost_usd_per_strategy_per_day": _num(c, 0, 100),
        "cache_key": _exact_list(c, ["strategy_hash", "assumption_context_hash", "evidence_snapshot_hash", "tier"])})
    budget["max_calls_per_ticker_per_hour"] = fixed(c, "monitoring.budget.max_calls_per_ticker_per_hour",
                                                    budget.get("max_calls_per_ticker_per_hour"),
                                                    {"t1": _int(c, 0, 120), "t2": _int(c, 0, 60)}, optional=("t1", "t2"))
    aud = fixed(c, "monitoring.audit", m.get("audit"), {"store": _subset(c, AUDIT_FIELDS), "durable_before_order_submission": True})
    return Monitoring(enabled=bool(m.get("enabled")), mode=m.get("mode") or "shadow", required_symbols=sorted(set(req or [])),
                      deadline=ev.get("deadline") or 45.0, min_gap=ev.get("min_gap") or 30.0, evidence=evid, tiers=tiers,
                      allowed_actions=set(resp.get("allowed_actions") or []), on_missing_input=resp.get("on_missing_required_market_input") or [],
                      on_provider_unavailable=resp.get("on_provider_unavailable") or [], budget=budget, audit=aud.get("store") or [])


def check_execution_v2(c: _Ctx, exe: dict):
    spec = {"activation": "requires_supported_validator_replay_and_owner_paper_enforcement", "model_actions": "contextual_only",
            "numeric_overlay_orders": "inherit_owner_selected_paper_execution_mode"}
    for k, want in spec.items():
        if k in exe and exe[k] != want:
            c.err(f"execution.{k}: only {want!r} is supported, got {exe[k]!r}")
    if "order_semantics" in exe:
        fixed(c, "execution.order_semantics", exe["order_semantics"], {
            "merge_to_one_target_per_symbol": True, "subtract_working_sell_quantity": True,
            "cancel_fill_race": "reconcile_actual_fills_before_new_target",
            "partial_fill": "continue_only_remaining_authorized_quantity", "blocked_sell": "retain_exit_intent_and_notify",
            "reserved_cash": "actual_net_fill_proceeds_only", "recheck": _subset(c, RECHECK)})


def validate_v2(v, d: dict, params: dict, groups: dict, weights: dict, max_holding: float | None) -> tuple[V2, list[str]]:
    """Check the 0.2 sections; returns the parsed sections and the assumption lines for display."""
    c = _Ctx(v, d, params, groups, weights, max_holding)
    check_source(c, d.get("source"))
    authoring = check_authoring(c, d.get("authoring"))
    hold = d.get("holdings") if isinstance(d.get("holdings"), dict) else {}
    if "allocation_basis" in hold:
        check_allocation_basis(c, hold["allocation_basis"])
    check_schedule_v2(c, d.get("schedule") if isinstance(d.get("schedule"), dict) else {})
    blocked = check_reentry_v2(c, d["reentry"]) if isinstance(d.get("reentry"), dict) else set(REENTRY_BLOCKS)
    inputs = check_inputs(c, d.get("market_inputs"))
    catalog, catalog_hash = load_catalog()
    if d.get("portfolio_overlays") is not None and not catalog:
        c.err(f"portfolio_overlays need config/{CATALOG_NAME}, which is missing")
    overlays = check_overlays(c, d.get("portfolio_overlays"), inputs, catalog)
    symbols = sorted(set(weights) | {sym for i in inputs.values() for sym in i.symbols})
    mon = check_monitoring(c, d.get("monitoring"), inputs, symbols)
    if mon is None and (overlays or inputs):
        c.err("market_inputs and portfolio_overlays need a monitoring section (mode: shadow or enforce)")
    for o in overlays:
        if mon and o.action not in mon.allowed_actions:
            c.err(f"overlay {o.id}: action {o.action} is not in monitoring.responses.allowed_actions")
    tiers = set(mon.tiers) if mon else set()
    assumptions, lines = check_assumptions(c, d.get("assumptions"), tiers, mon.allowed_actions if mon else set(CONTEXT_ACTIONS))
    if any(a.monitored for a in assumptions) and not (mon and mon.enabled):
        c.err("monitored assumptions need monitoring.enabled: true")
    exe = d.get("execution") if isinstance(d.get("execution"), dict) else {}
    check_execution_v2(c, exe)
    used_inputs = {o.metric for o in overlays}
    for name in set(inputs) - used_inputs:
        v.warnings.append(f"market input {name!r} is declared but no overlay uses it")
    v.v2_params = c.used_params
    return V2(authoring, inputs, overlays, assumptions, mon, catalog_hash, blocked), lines
