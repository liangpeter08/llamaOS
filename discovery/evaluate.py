"""Evaluate one candidate on a window of sessions and say what is wrong with it.

All replays go through backtest.engine.run (the same session code as paper trading). A candidate gets:
  * base replay and a cost-stress replay (3x slippage),
  * fold statistics (the window cut into contiguous folds) for consistency,
  * ablation: one replay per removable condition/rule, to find parts that don't earn their place,
  * a parameter neighbourhood (only when the base is profitable), to find fragile values,
  * a regime split of its returns (own volatility, own trend, the market's prior day),
and from those a ranked list of weaknesses, each with a concrete revision to try (discovery.mutate).
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, field
from datetime import date

import yaml

from backtest import engine
from dsl import expr as E
from dsl import features as F
from dsl.validator import validate

from . import mutate as M
from .stats import sharpe, t_stat


@dataclass
class Context:
    ticker: str
    kind: str  # intraday | daily
    days: list[date]
    peers: list[str] = field(default_factory=list)
    closes: dict[str, list] = field(default_factory=dict)  # symbol -> [(day, close)] for regime analysis
    folds: int = 4
    slippage: float = 0.0002
    stress: float = 3.0
    min_trades: int = 15
    min_history_sessions: int = 20
    thorough: bool = True  # ablation and parameter neighbourhood


def replay(conn, strat, risk, ctx: Context, days: list[date] | None = None, slippage: float | None = None) -> dict:
    opts = {"slippage": ctx.slippage if slippage is None else slippage, "keep_records": 0}
    if ctx.kind == "intraday":
        opts.update(symbols=[ctx.ticker], min_history_sessions=ctx.min_history_sessions)
    else:
        opts.update(repeat_cycles=True)
    return engine.run(conn, strat, risk, days=days if days is not None else ctx.days, **opts)


def daily_returns(rep: dict) -> dict[str, float]:
    out, prev = {}, rep.get("starting_equity") or 100_000.0
    for p in rep.get("equity_curve") or []:
        out[p["day"]] = p["equity"] / prev - 1 if prev else 0.0
        prev = p["equity"]
    return out


def _sh(rets) -> float:
    s = sharpe(list(rets))
    return 0.0 if s is None else s


def summarize(rep: dict, ctx: Context, days: list[date]) -> dict:
    """Window statistics from one replay: per-session returns, folds, headline metrics."""
    rets = daily_returns(rep)
    keys = [d.isoformat() for d in days]
    series = [rets.get(k, 0.0) for k in keys]
    n = max(1, min(ctx.folds, len(keys) // 5))
    size = len(keys) // n if n else len(keys)
    folds = []
    for f in range(n):
        chunk = series[f * size:(f + 1) * size if f < n - 1 else len(series)]
        folds.append({"from": keys[f * size], "sessions": len(chunk), "return": sum(chunk), "sharpe": _sh(chunk)})
    m = rep["metrics"]
    sells = sum(1 for o in rep.get("orders") or [] if o["side"] == "sell" and o["reason"] not in ("horizon", "backtest_end"))
    return {
        "sessions": len(rep.get("days") or []), "returns": rets, "series": series,
        "sharpe": _sh(series), "folds": folds,
        "consistency": sum(f["return"] > 0 for f in folds) / len(folds) if folds else 0.0,
        "trades": m.get("trades", 0), "orders": m.get("orders"), "rule_sells": sells if ctx.kind == "daily" else None,
        "return_pct": m.get("return_pct"), "max_drawdown": m.get("max_drawdown"), "win_rate": m.get("win_rate"),
        "profit_factor": m.get("profit_factor"), "exits": m.get("exits") or {},
        "failed_conditions": (rep.get("diagnostics") or {}).get("failed_conditions") or {},
    }


def baseline_sharpe(ctx: Context, days: list[str]) -> float:
    """Buy-and-hold of the ticker over the same sessions (daily candidates must beat it)."""
    closes = dict(ctx.closes.get(ctx.ticker) or [])
    keys = sorted(closes)
    rets = []
    for d in days:
        k = bisect.bisect_left(keys, d)
        if 0 < k < len(keys) and keys[k] == d:
            rets.append(closes[d] / closes[keys[k - 1]] - 1)
    return _sh(rets)


def complexity(strat) -> dict:
    if strat.kind == "portfolio":
        conds = len(strat.rules) + (1 if strat.reentry_when is not None else 0)
    else:
        conds = len(strat.entry_when_all) + max(0, len(strat.universe_filters) - 1) + len(strat.exit_when_any)
    return {"conditions": conds, "params": len(strat.params),
            "penalty": 0.15 * max(0, conds - 2) + 0.05 * max(0, len(strat.params) - 6)}


def evaluate(conn, text: str, risk, ctx: Context) -> dict:
    v = validate(text, risk)
    if not v.ok:
        return {"ok": False, "errors": v.errors, "score": None, "weaknesses": [], "gates": {"valid": False}, "hash": v.hash}
    s = v.strategy
    try:
        base_rep = replay(conn, s, risk, ctx)
        stress_rep = replay(conn, s, risk, ctx, slippage=ctx.slippage * ctx.stress)
    except ValueError as e:  # e.g. a reference without stored data
        return {"ok": False, "errors": [str(e)], "score": None, "weaknesses": [], "gates": {"valid": True, "data": False}, "hash": v.hash}
    base, stress = summarize(base_rep, ctx, ctx.days), summarize(stress_rep, ctx, ctx.days)
    cx = complexity(s)
    # the whole window: a sleeve that stopped early sits in cash for the rest, and holding didn't stop
    bench = baseline_sharpe(ctx, [d.isoformat() for d in ctx.days]) if ctx.kind == "daily" else 0.0
    fold_med = statistics.median(f["sharpe"] for f in base["folds"]) if base["folds"] else 0.0
    core = min(fold_med, stress["sharpe"])
    score = core - bench - cx["penalty"]
    ev = {
        "ok": True, "hash": v.hash, "id": s.id, "kind": ctx.kind, "complexity": cx, "baseline_sharpe": bench,
        "base": {k: v_ for k, v_ in base.items() if k not in ("returns", "series")}, "stress": {"sharpe": stress["sharpe"],
                 "return_pct": stress["return_pct"], "trades": stress["trades"]},
        "fold_median_sharpe": fold_med, "score": score, "series": base["series"], "days": [d.isoformat() for d in ctx.days],
        "ablation": [], "plateau": [], "regimes": regimes(base["returns"], base_rep, ctx),
    }
    if ctx.thorough:
        ev["ablation"] = ablation(conn, text, risk, ctx, base)
        if base["sharpe"] > 0:
            ev["plateau"] = plateau(conn, text, s, risk, ctx, base)
    ev["gates"] = gates(ev, ctx)
    ev["weaknesses"] = weaknesses(ev, s, ctx)
    return ev


def gates(ev: dict, ctx: Context) -> dict:
    b = ev["base"]
    g = {"valid": True, "consistent": b["consistency"] >= 0.5, "survives_costs": (ev["stress"]["return_pct"] or 0) > 0}
    if ctx.kind == "intraday":
        g["enough_trades"] = b["trades"] >= ctx.min_trades
        g["positive_edge"] = ev["score"] > 0  # worst of fold-median and cost-stressed Sharpe, after the complexity penalty
    else:
        g["acts"] = (b["rule_sells"] or 0) >= 1
        g["beats_holding"] = ev["score"] + ev["complexity"]["penalty"] > 0
    g["passed"] = all(g.values())
    return g


# ---------------------------------------------------------------- probes
def _removable(d: dict, kind: str) -> list[dict]:
    if kind == "daily":
        rules = d.get("rules") or []
        return [{"op": "remove", "where": "rules", "index": i, "src": r["when"]} for i, r in enumerate(rules)] if len(rules) > 1 else []
    out = []
    conds = (d.get("entry") or {}).get("when_all") or []
    if len(conds) > 1:
        out += [{"op": "remove", "where": "entry.when_all", "index": i, "src": c} for i, c in enumerate(conds)]
    filters = (d.get("universe") or {}).get("filters") or []
    out += [{"op": "remove", "where": "universe.filters", "index": i, "src": c} for i, c in enumerate(filters) if not str(c).startswith("adv(")]
    return out


def ablation(conn, text, risk, ctx, base) -> list[dict]:
    out = []
    for op in _removable(yaml.safe_load(text), ctx.kind)[:5]:
        variant = M.apply(text, op)
        if variant is None:
            continue
        v = validate(variant[0], risk)
        if not v.ok:
            continue
        try:
            rep = summarize(replay(conn, v.strategy, risk, ctx), ctx, ctx.days)
        except ValueError:
            continue
        out.append({**op, "sharpe": rep["sharpe"], "trades": rep["trades"], "delta": rep["sharpe"] - base["sharpe"]})
    return out


def _tunable(s) -> list[str]:
    if s.kind == "portfolio":
        nodes = [r.when for r in s.rules] + ([s.reentry_when] if s.reentry_when is not None else [])
    else:
        nodes = [*s.entry_when_all, *s.universe_filters]
    names: list[str] = []
    for n in nodes:
        for x in E.walk(n):
            if isinstance(x, E.Name) and x.id in s.params and x.id != "min_adv" and x.id not in names:
                names.append(x.id)
    return names[:3]


def plateau(conn, text, s, risk, ctx, base) -> list[dict]:
    out = []
    for name in _tunable(s):
        lo, hi = s.param_ranges[name]
        step = (hi - lo) * 0.25
        integer = all(float(x).is_integer() for x in (lo, hi, s.params[name]))
        points = []
        for val in (s.params[name] - step, s.params[name] + step):
            val = min(hi, max(lo, round(val) if integer else val))
            if abs(val - s.params[name]) < 1e-12 or any(abs(val - p[0]) < 1e-12 for p in points):
                continue
            variant = M.apply(text, {"op": "set_param", "param": name, "value": val})
            v = validate(variant[0], risk) if variant else None
            if not v or not v.ok:
                continue
            try:
                points.append((val, summarize(replay(conn, v.strategy, risk, ctx), ctx, ctx.days)["sharpe"]))
            except ValueError:
                continue
        if points:
            out.append({"param": name, "value": s.params[name], "sharpe": base["sharpe"],
                        "neighbors": [{"value": a, "sharpe": b} for a, b in points]})
    return out


def regimes(rets: dict[str, float], rep: dict, ctx: Context) -> dict:
    """Mean per-session return on active sessions, bucketed by conditions known before the session."""
    if ctx.kind == "intraday":
        active = [p["day"] for p in rep.get("per_day") or [] if p.get("trades")]
    else:
        active = [d for d, r in rets.items() if abs(r) > 1e-12]
    own = ctx.closes.get(ctx.ticker) or []
    own_days = [d for d, _ in own]
    peer = ctx.peers[0] if ctx.peers else None
    pser = (ctx.closes.get(peer) or []) if peer else []
    pdays = [d for d, _ in pser]
    buckets: dict[str, dict[str, list[float]]] = {"vol": {}, "trend": {}, "market": {}}
    for d in active:
        hist = own[:bisect.bisect_left(own_days, d)]
        vr, tr = F.vol_rank(hist, 10, 60), F.ret_d(hist, 20)
        if vr is not None:
            buckets["vol"].setdefault("vol_low" if vr < 1 / 3 else "vol_high" if vr > 2 / 3 else "vol_mid", []).append(rets.get(d, 0.0))
        if tr is not None:
            buckets["trend"].setdefault("trend_up" if tr > 0 else "trend_down", []).append(rets.get(d, 0.0))
        if pser:
            mr = F.ret_d(pser[:bisect.bisect_left(pdays, d)], 1)
            if mr is not None:
                buckets["market"].setdefault("mkt_prev_up" if mr > 0 else "mkt_prev_down", []).append(rets.get(d, 0.0))
    return {feat: {b: {"n": len(xs), "mean": statistics.mean(xs), "t": t_stat(xs)} for b, xs in sorted(bs.items())}
            for feat, bs in buckets.items() if bs} | ({"peer": peer} if peer else {})


# ---------------------------------------------------------------- critique
def _loosen_for(cond_src: str, s) -> dict | None:
    """The param in a failing condition, and which way makes the condition easier to meet."""
    try:
        node = E.parse(cond_src)
    except Exception:  # noqa: BLE001 - diagnostics text, not a DSL file
        return None
    if isinstance(node, E.Name) and node.id in s.vars:
        node = s.vars[node.id]
    if not isinstance(node, E.Cmp):
        return None
    for side, sign in ((node.r, 1), (node.l, -1)):
        if isinstance(side, E.Name) and side.id in s.params:
            # `x >= p`: lower p loosens it; `p >= x` (param on the left): raise p
            easier = -1 if node.op in (">=", ">") else 1 if node.op in ("<=", "<") else 0
            return {"op": "loosen", "param": side.id, "direction": easier * sign} if easier else None
    return None


def weaknesses(ev: dict, s, ctx: Context) -> list[dict]:
    b, out = ev["base"], []
    if ctx.kind == "intraday" and b["trades"] < ctx.min_trades:
        failed = sorted(b["failed_conditions"].items(), key=lambda kv: -kv[1])
        if failed and failed[0][0].endswith("(missing value)"):
            out.append({"kind": "missing_data", "detail": f"{b['trades']} trades; `{failed[0][0]}` most often: not enough stored history "
                        "for its lookback, or a reference without bars. Load more history; loosening thresholds won't help.", "fix": None})
            failed = []
        for cond, _ in failed:
            if cond.endswith("(missing value)"):
                continue
            op = _loosen_for(cond, s)
            if op:
                out.append({"kind": "too_few_trades", "detail": f"{b['trades']} trades (< {ctx.min_trades}); most often false: {cond}", "fix": op})
                break
        else:
            if failed:
                out.append({"kind": "too_few_trades", "detail": f"{b['trades']} trades; most often false: {failed[0][0]}", "fix": None})
    if ctx.kind == "daily" and (b["rule_sells"] or 0) < 1 and s.rules:
        first = s.rules[0].when
        op = _loosen_for(first.src, s)
        out.append({"kind": "never_acts", "detail": "no rule-driven sells in the window: it behaved like buy-and-hold", "fix": op})
    active = (b["trades"] if ctx.kind == "intraday" else (b["rule_sells"] or 0)) >= (5 if ctx.kind == "intraday" else 1)
    for a in ev["ablation"] if active else []:  # with no activity every removal looks harmless: say nothing
        if a["delta"] >= -0.1 and a["trades"] > 0:
            out.append({"kind": "dead_condition", "detail": f"removing `{a['src']}` changes Sharpe by {a['delta']:+.2f}: it doesn't earn its place",
                        "fix": {k: a[k] for k in ("op", "where", "index", "src")}})
    reg = ev["regimes"]
    for feat in ("vol", "trend", "market"):
        bs = reg.get(feat) or {}
        for bucket, st in bs.items():
            others = [x for k, x in bs.items() if k != bucket]
            if st["n"] >= 5 and st["mean"] < 0 and (st["t"] or 0) <= -1.0 and any(o["mean"] > 0 for o in others):
                fix = {"op": "add_filter", "feature": feat, "bucket": bucket, "peer": reg.get("peer")}
                if M.filter_available(fix, ctx.kind):
                    out.append({"kind": "regime_drag", "detail": f"loses in {bucket} sessions (mean {st['mean']:+.3%}, n={st['n']}, t={st['t']:.1f}) "
                                f"while other {feat} regimes make money", "fix": fix})
    for p in ev["plateau"]:
        vals = [(p["value"], p["sharpe"])] + [(x["value"], x["sharpe"]) for x in p["neighbors"]]
        worst = min(x[1] for x in vals[1:])
        if p["sharpe"] > 0 and worst < 0.5 * p["sharpe"]:
            vals.sort()
            best = max(range(len(vals)), key=lambda k: statistics.mean(x[1] for x in vals[max(0, k - 1):k + 2]))
            centre = vals[best][0]
            out.append({"kind": "fragile_param", "detail": f"{p['param']}={p['value']:g}: a neighbour drops Sharpe from {p['sharpe']:.2f} to {worst:.2f}",
                        "fix": {"op": "set_param", "param": p["param"], "value": centre} if centre != p["value"] else None})
    if ctx.kind == "intraday" and b["trades"] >= 5:
        stops = b["exits"].get("stop", 0) / max(1, b["trades"])
        if stops > 0.6 and (b["win_rate"] or 0) < 0.4:
            out.append({"kind": "stop_heavy", "detail": f"{stops:.0%} of trades stopped out, win rate {b['win_rate']:.0%}",
                        "fix": {"op": "loosen", "param": "atr_mult", "direction": 1}})
    if b["sharpe"] > 0 and ev["stress"]["sharpe"] <= 0:
        out.append({"kind": "cost_sensitive", "detail": f"Sharpe {b['sharpe']:.2f} becomes {ev['stress']['sharpe']:.2f} at {ctx.stress:g}x costs",
                    "fix": {"op": "cap_trades"} if ctx.kind == "intraday" else None})
    if b["consistency"] < 0.5 and b["trades"] and not any(w["kind"] in ("regime_drag", "too_few_trades") for w in out):
        fix = {"op": "add_filter", "feature": "trend", "bucket": "trend_down", "peer": reg.get("peer")}
        out.append({"kind": "inconsistent", "detail": f"profitable in {b['consistency']:.0%} of folds", "fix": fix})
    order = ["missing_data", "too_few_trades", "never_acts", "dead_condition", "regime_drag", "fragile_param", "stop_heavy", "cost_sensitive", "inconsistent"]
    out.sort(key=lambda w: order.index(w["kind"]))
    return out


def explorations(ev: dict, kind: str) -> list[dict]:
    """Speculative revisions for a candidate with nothing measurably wrong: skip its weakest regimes.

    Weaker evidence than a weakness (any losing bucket with n >= 5, worst first). Each is still a counted
    trial that has to beat its parent's score to be kept.
    """
    reg, out = ev.get("regimes") or {}, []
    for feat in ("vol", "trend", "market"):
        for bucket, st in (reg.get(feat) or {}).items():
            fix = {"op": "add_filter", "feature": feat, "bucket": bucket, "peer": reg.get("peer")}
            if st["n"] >= 5 and st["mean"] < 0 and M.filter_available(fix, kind):
                out.append((st["t"] if st["t"] is not None else 0.0, {"kind": "exploration", "detail": f"weakest regime: {bucket} "
                            f"(mean {st['mean']:+.3%}, n={st['n']})", "fix": fix}))
    return [w for _, w in sorted(out, key=lambda x: x[0])]


def score_or(ev: dict, default: float = -math.inf) -> float:
    return ev["score"] if ev.get("ok") and ev.get("score") is not None else default
