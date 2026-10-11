"""Deterministic revisions of a DSL file: each op answers one weakness found by discovery.evaluate.

apply(text, op) returns (new_text, change) or None when the op doesn't apply. Every revision is
recorded in the file's assumptions, so the final draft carries its own lineage.

Ops:
  remove      {where: entry.when_all | universe.filters | rules, index, src}
  set_param   {param, value}
  loosen      {param, direction: +1 | -1}   move a quarter of the range, clipped
  add_filter  {feature: vol | trend | market, bucket, peer}   skip the losing regime
  cap_trades  {}                            intraday: at most one trade a day
"""
from __future__ import annotations

import yaml

from .templates import dump, pct

# (kind, feature, losing bucket) -> (condition to require, reentry for daily files, new params)
_FILTERS = {
    ("intraday", "vol", "vol_high"): ("vol_rank(10, 60) <= vol_cap", None, {"vol_cap": (0.66, 0.3, 1.0)}),
    ("intraday", "vol", "vol_low"): ("vol_rank(10, 60) >= vol_floor", None, {"vol_floor": (0.33, 0.0, 0.7)}),
    ("intraday", "trend", "trend_down"): ("ret_d(20) >= trend_floor", None, {"trend_floor": ("0%", "-10%", "10%")}),
    ("intraday", "trend", "trend_up"): ("ret_d(20) <= trend_cap", None, {"trend_cap": ("0%", "-10%", "10%")}),
    ("intraday", "market", "mkt_prev_down"): ("peer({P}, ret_d(1)) >= mkt_floor", None, {"mkt_floor": ("0%", "-3%", "3%")}),
    ("intraday", "market", "mkt_prev_up"): ("peer({P}, ret_d(1)) <= mkt_cap", None, {"mkt_cap": ("0%", "-3%", "3%")}),
    # daily: exit when the losing regime starts, rebuild when it ends
    ("daily", "vol", "vol_high"): ("vol_rank(10, 60) > vol_cap", "vol_rank(10, 60) <= vol_cap", {"vol_cap": (0.66, 0.3, 1.0)}),
    ("daily", "trend", "trend_down"): ("ret_d(20) < trend_floor", "ret_d(20) >= trend_floor", {"trend_floor": ("0%", "-10%", "10%")}),
    ("daily", "market", "mkt_prev_down"): ("peer({P}, ret_d(1)) < mkt_floor", "peer({P}, ret_d(1)) >= mkt_floor",
                                           {"mkt_floor": ("0%", "-3%", "3%")}),
}


def filter_available(op: dict, kind: str) -> bool:
    return (kind, op["feature"], op["bucket"]) in _FILTERS and (op["feature"] != "market" or bool(op.get("peer")))


def _kind(d: dict) -> str:
    return "daily" if d.get("kind") == "portfolio" else "intraday"


def _fmt(old, value: float):
    if isinstance(old, str) and old.endswith("%"):
        return pct(value)
    if isinstance(old, int) and float(value).is_integer():
        return int(value)
    return round(value, 6)


def _num(x) -> float:
    return float(x[:-1]) / 100 if isinstance(x, str) and x.endswith("%") else float(x)


def _note(d: dict, change: str):
    d.setdefault("assumptions", []).append(f"discovery revision: {change}")


def apply(text: str, op: dict) -> tuple[str, str] | None:
    d = yaml.safe_load(text)
    kind = _kind(d)
    o = op.get("op")
    if o == "remove":
        if op["where"] == "rules":
            items = d.get("rules") or []
        else:
            sec, key = op["where"].split(".")
            items = (d.get(sec) or {}).get(key) or []
        i = op["index"]
        if i >= len(items) or (op["where"] != "universe.filters" and len(items) < 2):
            return None
        removed = items.pop(i)
        if op["where"] == "rules" and removed.get("do") == "exit_and_wait" and not any(r.get("do") == "exit_and_wait" for r in items):
            d.pop("reentry", None)  # nothing waits any more
        change = f"removed `{op.get('src') or removed}` ({op['where']}) because it did not earn its place"
    elif o in ("set_param", "loosen"):
        spec = (d.get("params") or {}).get(op["param"])
        if not spec:
            return None
        lo, hi, cur = _num(spec["range"][0]), _num(spec["range"][1]), _num(spec["value"])
        if o == "set_param":
            new = min(hi, max(lo, float(op["value"])))
        else:
            new = min(hi, max(lo, cur + op["direction"] * (hi - lo) * 0.25))
            if all(float(x).is_integer() for x in (lo, hi, cur)):
                new = float(round(new))
        if abs(new - cur) < 1e-12:
            return None
        spec["value"] = _fmt(spec["value"], new)
        change = f"{op['param']} {spec['value']} (was {pct(cur) if isinstance(spec['value'], str) else f'{cur:g}'})" + \
                 (" to sit in the middle of a stable neighbourhood" if o == "set_param" else " to loosen the condition that blocked most entries"
                  if op["param"] != "atr_mult" else " to give trades more room before the stop")
    elif o == "add_filter":
        key = (kind, op["feature"], op["bucket"])
        if key not in _FILTERS or (op["feature"] == "market" and not op.get("peer")):
            return None
        cond, reentry, params = _FILTERS[key]
        peer = op.get("peer") or ""
        cond, reentry = cond.replace("{P}", peer), reentry.replace("{P}", peer) if reentry else None
        existing = [r["when"] for r in d.get("rules") or []] if kind == "daily" else (d.get("entry") or {}).get("when_all") or []
        if cond in existing or any(n in (d.get("params") or {}) for n in params):
            return None  # this filter (or one sharing its params) is already there
        for n, (v, lo, hi) in params.items():
            d.setdefault("params", {})[n] = {"value": v, "range": [lo, hi]}
        if op["feature"] == "market" and peer not in (d.get("references") or []):
            d["references"] = [*(d.get("references") or []), peer]
        if kind == "daily":
            d["rules"].insert(0, {"when": cond, "do": "exit_and_wait"})
            re_ = d.get("reentry")
            if re_:
                re_["when"] = f"({re_['when']}) and ({reentry})"
            else:
                d.setdefault("params", {}).setdefault("rebuild_step", {"value": "50%", "range": ["10%", "100%"]})
                d["reentry"] = {"when": reentry, "step": "rebuild_step"}
            change = f"exit while `{cond}` (it lost money in {op['bucket']} sessions) and rebuild once `{reentry}`"
        else:
            d["entry"]["when_all"].append(cond)
            change = f"added entry filter `{cond}`: it lost money in {op['bucket']} sessions"
    elif o == "cap_trades":
        lim = d.setdefault("limits", {})
        if lim.get("max_trades_per_day") == 1 or kind != "intraday":
            return None
        lim["max_trades_per_day"] = 1
        change = "max_trades_per_day 1: costs were eating the edge"
    else:
        return None
    _note(d, change)
    return dump(d), change
