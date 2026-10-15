"""Parity check: replay strategies/rod_momentum_{long,short}.v1.yaml through the repository's own backtest engine
(backtest/engine.py -> core/session.py -> core/exchange.py, the code that trades) on the cached Alpaca bars, and compare
its trades with scripts/research_rod_momentum_backtest.py, trade by trade.

    .venv/bin/python scripts/research_rod_momentum_parity.py        # v1, after research_rod_momentum_backtest.py has cached data
    .venv/bin/python scripts/research_rod_momentum_parity.py 2      # v2 (volatility-regime gate)

Only the bars the rules need are replayed (the 15:29 decision bar and 15:30-15:59), so VWAP and other session values are
not meaningful here; the rules do not read them. Minute volume is set high so the engine's participation gate never
binds. Costs are set to zero on both sides, so prices are compared before costs. Writes reports/rod_momentum/PARITY_v<n>.md.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import engine  # noqa: E402
from common import marketdata as MD  # noqa: E402
from common import timeutil as T  # noqa: E402
from common.config import build_risk, risk_defaults  # noqa: E402
from dsl.evaluator import Daily, DayData, SignalBook  # noqa: E402
from dsl.validator import validate  # noqa: E402
from scripts import research_rod_momentum_backtest as R  # noqa: E402


def main():
    version = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cache = sorted(R.CACHE.glob("rod_inputs_*.json.gz"))
    if not cache:
        sys.exit("No cached inputs: run scripts/research_rod_momentum_backtest.py first.")
    data = json.loads(gzip.decompress(cache[-1].read_bytes()))
    d, raw = risk_defaults()
    d["account"]["allow_short"] = True  # the short file needs it; the long file is unaffected
    risk = build_risk(d, raw)
    strategies = []
    for f in (f"rod_momentum_long.v{version}.yaml", f"rod_momentum_short.v{version}.yaml"):
        v = validate((ROOT / "strategies" / f).read_text(), risk)
        assert v.ok, (f, v.errors)
        strategies.append(v.strategy)

    days_by_sym = {}
    for sym in R.UNIVERSE:
        rows = data["daily"][sym]
        if any("v" not in r for r in rows):  # caches written before daily volume was stored: fetch it (adv filter)
            import httpx
            with httpx.Client(headers=R.headers(), timeout=60) as client:
                fresh = {r["day"]: r for r in R.fetch_daily(client, sym, date.fromisoformat(rows[0]["day"]),
                                                            date.fromisoformat(rows[-1]["day"]))}
            rows = [{**r, "v": fresh.get(r["day"], {}).get("v", 0.0)} for r in rows]
        hist = [Daily(r["day"], r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rows]
        index = {r["day"]: i for i, r in enumerate(rows)}
        out = {}
        for day, m in data["minute"][sym].items():
            if "c1529" not in m or day not in index:
                continue
            dd = DayData(sym, date.fromisoformat(day), hist[max(0, index[day] - 260):index[day]])
            base = T.open_ts(dd.day)
            c = m["c1529"]
            dd.append(base + 60 * 359, c, c, c, c, 1e12)
            for k, (o, h, lo, cl) in sorted((int(k), v) for k, v in m["late"].items()):
                dd.append(base + 60 * (360 + k), o, h, lo, cl, 1e12)
            out[day] = dd
        days_by_sym[sym] = out

    def load_day(conn, symbol, day):
        src = days_by_sym[symbol].get(day.isoformat())
        if src is None:
            return DayData(symbol, day, [])
        copy = DayData(symbol, day, src.history)
        for i in range(len(src)):
            copy.append(src.ts[i], src.o[i], src.h[i], src.l[i], src.c[i], src.v[i])
        return copy

    MD.load_day = load_day
    days = sorted({date.fromisoformat(x) for s in days_by_sym.values() for x in s})
    rep = engine.simulate(None, strategies, risk, days, starting_equity=100_000.0, slippage=0.0,
                          symbols=R.UNIVERSE, signal_source=lambda *a: SignalBook(), min_history_sessions=21)
    eng = {(t["day"], t["symbol"]): t for t in rep["trades"]}

    # the research script's own trades, recomputed with zero costs for a price comparison
    R.SLIP, R.SEC_FEE, R.TAF_PER_SHARE = 0.0, 0.0, 0.0
    R.HALF_SPREAD_FLOOR = dict.fromkeys(R.HALF_SPREAD_FLOOR, 0.0)
    data["spreads"] = []
    rank_min = R.RANK_MIN_V2 if version == 2 else None
    mine = {(t["day"], t["symbol"]): t for t in R.simulate(data, syms=R.UNIVERSE, rank_min=rank_min)}

    both = sorted(set(eng) & set(mine))
    only_eng, only_mine = sorted(set(eng) - set(mine)), sorted(set(mine) - set(eng))
    side_mismatch = [k for k in both if eng[k]["side"] != mine[k]["side"]]
    entry_diff = [abs(eng[k]["entry_price"] / mine[k]["entry"] - 1) * 1e4 for k in both]
    exit_diff = [abs(eng[k]["exit_price"] / mine[k]["exit"] - 1) * 1e4 for k in both]
    kinds = {}
    for k in both:
        pair = (mine[k]["exit_kind"], eng[k]["exit_kind"])
        kinds[pair] = kinds.get(pair, 0) + 1
    lines = ["# Parity: repository engine vs research script", "",
             f"Engine replay of `rod_momentum_long.v{version}` + `rod_momentum_short.v{version}` (allow_short on), {len(days)} sessions, "
             f"{', '.join(R.UNIVERSE)}, zero costs on both sides.", "",
             f"- Engine trades: {len(eng)}; script trades: {len(mine)}; matched (same day and symbol): {len(both)}",
             f"- Only in engine: {len(only_eng)} {only_eng[:10]}",
             f"- Only in script: {len(only_mine)} {only_mine[:10]}",
             f"- Side mismatches among matched: {len(side_mismatch)}",
             f"- Entry price difference, bps: median {sorted(entry_diff)[len(entry_diff) // 2]:.3f}, max {max(entry_diff):.3f}" if both else "",
             f"- Exit price difference, bps: median {sorted(exit_diff)[len(exit_diff) // 2]:.3f}, max {max(exit_diff):.3f}" if both else "",
             f"- Exit kinds (script, engine): {', '.join(f'{a}/{b}: {n}' for (a, b), n in sorted(kinds.items()))}",
             f"- Engine diagnostics: {json.dumps(rep['diagnostics']['funnel'])}", ""]
    (R.OUT / f"PARITY_v{version}.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
