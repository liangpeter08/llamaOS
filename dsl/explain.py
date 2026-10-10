"""Independent back-translation: renders a compiled strategy as plain English with code.

Because it's generated from the parsed AST (not by the model that wrote the file), it shows
what the rules actually say, which is what you approve.
"""
from __future__ import annotations

from . import expr as E
from .validator import Strategy

_OPS = {">=": "is at least", "<=": "is at most", ">": "is above", "<": "is below", "==": "equals", "!=": "is not"}
_NAMES = {
    "close": "the bar close", "open": "the bar open", "high": "the bar high", "low": "the bar low",
    "volume": "bar volume", "vwap": "VWAP", "day_open": "today's open", "day_high": "today's high so far",
    "day_low": "today's low so far", "prior_close": "yesterday's close", "gap": "the opening gap",
    "entry_price": "the entry price", "risk_per_share": "the risk per share (entry to stop)",
    "bars_held": "bars held",
}
_FN = {
    "or_high": "the high of the first {0} minutes", "or_low": "the low of the first {0} minutes",
    "sma": "the {1}-bar average of {0}", "ema": "the {1}-bar EMA of {0}", "rsi": "the {1}-bar RSI of {0}",
    "atr": "the {0}-bar ATR", "highest": "the highest {0} over {1} bars", "lowest": "the lowest {0} over {1} bars",
    "rvol": "relative volume vs the {0}-day average", "adv": "the {0}-day average dollar volume",
    "atr_d": "the {0}-day daily ATR", "crosses_above": "{0} crosses above {1}",
    "crosses_below": "{0} crosses below {1}", "streak": "the number of consecutive bars on which {0}",
}


def _fmt_num(v: float, name: str | None, s: Strategy) -> str:
    raw = None
    if name:
        spec = (s.raw.get("params") or {}).get(name) or {}
        raw = spec.get("value")
    if isinstance(raw, str) and raw.endswith("%"):
        return raw
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:g}M"
    return f"{v:g}"


def say(node: E.Node, s: Strategy) -> str:
    if isinstance(node, E.Num):
        return node.src
    if isinstance(node, E.BoolLit):
        return "true" if node.value else "false"
    if isinstance(node, E.Name):
        n = node.id
        if n in s.params:
            return f"{n} ({_fmt_num(s.params[n], n, s)})"
        if n in s.vars:
            return f"{n} [= {say(s.vars[n], s)}]"
        if n.startswith(("news.", "note.")):
            return f"signal {n}"
        return _NAMES.get(n, n)
    if isinstance(node, E.Index):
        return f"{say(node.base, s)} {node.n} bar{'s' if node.n != 1 else ''} ago"
    if isinstance(node, E.Neg):
        return f"minus {say(node.x, s)}"
    if isinstance(node, E.BinOp):
        word = {"+": "plus", "-": "minus", "*": "times", "/": "divided by"}[node.op]
        return f"{say(node.l, s)} {word} {say(node.r, s)}"
    if isinstance(node, E.Cmp):
        return f"{say(node.l, s)} {_OPS[node.op]} {say(node.r, s)}"
    if isinstance(node, E.In):
        return f"{say(node.l, s)} is {'not ' if node.negate else ''}one of {', '.join(node.items)}"
    if isinstance(node, E.Not):
        return f"NOT ({say(node.x, s)})"
    if isinstance(node, E.BoolOp):
        return f" {node.op.upper()} ".join(f"({say(x, s)})" for x in node.items)
    if isinstance(node, E.Call):
        args = [say(a, s) if not isinstance(a, E.Num) else a.src for a in node.args]
        tmpl = _FN.get(node.fn)
        return tmpl.format(*args) if tmpl else f"{node.fn}({', '.join(args)})"
    return node.src


def back_translate(s: Strategy) -> str:
    if getattr(s, "kind", "intraday") == "portfolio":
        return back_translate_portfolio(s)
    lines = [f"{s.id} v{s.version}: a {s.side}-only intraday strategy on 1-minute bars."]
    uni = "symbols in the watchlist"
    if s.universe_news_today:
        uni += " that have recorded news since yesterday's close"
    lines.append(f"Universe: {uni}" + (", where " + "; and ".join(say(f, s) for f in s.universe_filters) if s.universe_filters else "") + ".")
    lines.append(f"Entries are considered at each bar close between {s.entry_window[0]:%H:%M} and {s.entry_window[1]:%H:%M} ET, only when ALL of:")
    lines += [f"  {k + 1}. {say(c, s)}" for k, c in enumerate(s.entry_when_all)]
    lines.append(f"Size: risk {s.risk_per_trade:.2%} of equity between entry and stop, capped by the position limits in risk.yaml.")
    if s.exit_stop is not None:
        lines.append(f"Stop (set once, held at the broker): {say(s.exit_stop, s)}.")
    if s.exit_target is not None:
        lines.append(f"Target (set once, held at the broker): {say(s.exit_target, s)}.")
    if s.exit_when_any:
        lines.append("Exit early at the next open if ANY of:")
        lines += [f"  - {say(c, s)}" for c in s.exit_when_any]
    lines.append(f"Flatten at {s.flatten_at:%H:%M} ET at the latest.")
    if s.limits:
        lines.append("Strategy limits: " + ", ".join(f"{k}={v:g}" for k, v in s.limits.items()) + ".")
    if s.signals:
        decl = [f"{d.name} (max age {d.max_age}{', default ' + str(d.default) if d.has_default else ''})" for d in s.signals.values()]
        lines.append("Model signals used (advisory, gate entries/exits only): " + "; ".join(decl) + ". Missing signals fail closed.")
    else:
        lines.append("Uses no model signals (price-only).")
    return "\n".join(lines)


_DAILY_NAMES = {
    "day_change": "today's move (price at the decision time vs the prior close)",
    "holding": "the value held as a share of the original allocation",
    "sessions_held": "sessions since the first buy",
    "gain": "the gain on the shares held (vs average cost)",
}
_ACTION = {
    "hold": "hold: sell nothing and buy nothing",
    "exit": "sell the whole position and never buy it back",
    "exit_and_wait": "sell the whole position and wait for the re-entry condition",
}


def _say_p(node: E.Node, s) -> str:
    """say() for portfolio conditions: params with their values, daily builtins in words."""
    if isinstance(node, E.Name):
        if node.id in s.params:
            return f"{node.id} ({_fmt_num(s.params[node.id], node.id, s)})"
        return _DAILY_NAMES.get(node.id, node.id)
    if isinstance(node, E.Cmp):
        return f"{_say_p(node.l, s)} {_OPS[node.op]} {_say_p(node.r, s)}"
    if isinstance(node, E.BoolOp):
        return f" {node.op.upper()} ".join(f"({_say_p(x, s)})" for x in node.items)
    if isinstance(node, E.Not):
        return f"NOT ({_say_p(node.x, s)})"
    if isinstance(node, E.Neg):
        return f"minus {_say_p(node.x, s)}"
    if isinstance(node, E.BinOp):
        word = {"+": "plus", "-": "minus", "*": "times", "/": "divided by"}[node.op]
        return f"{_say_p(node.l, s)} {word} {_say_p(node.r, s)}"
    return node.src


def back_translate_portfolio(s) -> str:
    lines = [f"{s.id} v{s.version}: a long-only portfolio held overnight, decided once a day."]
    lines.append(f"Capital: {s.capital:.0%} of account equity at the first buy, split as:")
    for name, (w, syms) in s.groups.items():
        lines.append(f"  - {name}: {w:.0%} -> {', '.join(syms)} ({w / len(syms):.1%} each)")
    lines.append("First session: buy every symbol's full allocation with market orders.")
    lines.append(f"Every session at {s.decide_at:%H:%M} ET, for each symbol, the FIRST matching rule applies:")
    for k, r in enumerate(s.rules):
        if r.action == "trim":
            act = f"sell {_fmt_num(s.params[r.size], r.size, s)} of the shares held"
        elif r.action == "add":
            act = (f"buy {_fmt_num(s.params[r.size], r.size, s)} of the original allocation, "
                   f"never above {s.max_holding:.0%} of it at market value")
        else:
            act = _ACTION[r.action]
        lines.append(f"  {k + 1}. if {_say_p(r.when, s)}: {act}")
    lines.append("  No rule matches: do nothing.")
    if s.reentry_when is not None:
        lines.append(f"After an exit_and_wait: on the first session where {_say_p(s.reentry_when, s)}, buy back "
                     f"{_fmt_num(s.params[s.reentry_step], s.reentry_step, s)} of the original allocation, then the same "
                     "every session until fully rebuilt. While rebuilding only exit rules apply.")
    end = f"after {s.hold_sessions} sessions"
    if s.end_date:
        end += f" or on {s.end_date.isoformat()}, whichever comes first"
    start = f" Starts on the first session on or after {s.start_date.isoformat()}." if s.start_date else ""
    lines.append(f"Sell everything {end}.{start}")
    dd = s.limits.get("max_drawdown")
    lines.append("Sleeve stop: sell everything if the sleeve falls "
                 + (f"{dd:.0%}" if dd else "by risk.yaml portfolio.max_drawdown") + " from its peak value.")
    lines.append("Not flattened at the close; checked against risk.yaml portfolio limits before every order. Uses no model signals (price-only).")
    return "\n".join(lines)
