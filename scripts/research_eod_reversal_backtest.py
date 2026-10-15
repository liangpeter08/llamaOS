#!/usr/bin/env python3
"""Standalone, rerunnable research backtest for strategies/eod_reversal_losers_long.v1.yaml
(quant_research@v1 artifact; see reports/eod_reversal_*/results.md for saved runs).

Strategy (Baltussen, Da & Soebhag 2025, "End-of-Day Reversal", working paper,
https://academicweb.nd.edu/~zda/EOD.pdf): a stock's return from the previous close to
15:00 ET (ROD3) negatively predicts its 15:30-16:00 return cross-sectionally; the effect
is entirely driven by intraday losers bouncing into the close. This script implements the
exact deployed DSL rules on 1-minute bars, long the losers only.

Data source: the repository's own stored 1-minute bars (ingested from Alpaca by the paper
broker) in PostgreSQL. Alpaca Market Data keys are NOT present in this checkout's .env, so
direct Alpaca download is unavailable; the stored bars are the same feed the system trades.
yfinance is intentionally not used: its 1-minute history is limited to roughly the last 30
days (7-8 days per request), which cannot cover the stored window.

Fill model mirrors core/exchange.py and docs/backtesting.md:
  * Signal at bar close -> marketable-limit entry filled at the NEXT bar's open plus costs.
  * Cost per side = slippage_bps + half_spread_bps (full spread contributes half per side);
    commission_per_share and commission_per_fill are additive and charged on every fill.
  * Broker-side bracket from the fill bar onward; if stop and target are both touched in one
    bar, the stop fills first (conservative). Fresh-fill bar: stop/target fill at the level.
    Otherwise stop fills at min(open, stop), target at max(open, target); targets get no
    extra adverse slippage.
  * Time exit: position is flattened on the 15:55 bar at that bar's close minus costs.
Sizing mirrors core/engine.py: qty = floor(equity * risk_per_trade / risk_per_share), capped
by floor(max_position_pct * equity / entry_price).

Usage:
  python scripts/research_eod_reversal_backtest.py \
      --dsn postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_paper_utf8
Defaults match the YAML. Outputs (trades.csv, daily.csv, summary.json, config.json,
results.md) go to --outdir (default reports/eod_reversal_<timestamp>/).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dsl.evaluator import VOLUME_CUM  # noqa: E402  (same U-shaped intraday volume profile)

ET = ZoneInfo("America/New_York")
OPEN = dtime(9, 30)
FLATTEN_MIN = 385  # minute index of the bar starting 15:55 (390-bar session)
DECISION_MINS = (359, 360)  # bars starting 15:29 and 15:30 (the YAML entry window)

SYMBOLS = ["AAPL", "AMD", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]


# --------------------------------------------------------------------------- data
@dataclass
class DailyRow:
    day: str
    o: float
    h: float
    l: float
    c: float
    v: float


def load_daily(conn, symbol: str, before: date, n: int = 260) -> list[DailyRow]:
    rows = conn.execute(
        "SELECT day, o, h, l, c, v FROM daily WHERE symbol=%s AND day<%s ORDER BY day DESC LIMIT %s",
        (symbol, before.isoformat(), n),
    ).fetchall()
    return [DailyRow(str(r[0]), *map(float, r[1:])) for r in reversed(rows)]


def load_bars(conn, symbol: str, day: date) -> list[tuple]:
    lo = int(datetime.combine(day, OPEN, tzinfo=ET).timestamp())
    hi = int(datetime.combine(day, dtime(16, 0), tzinfo=ET).timestamp())
    rows = conn.execute(
        "SELECT ts, o, h, l, c, v FROM bars WHERE symbol=%s AND ts>=%s AND ts<%s ORDER BY ts",
        (symbol, lo, hi),
    ).fetchall()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def bar_days(conn) -> list[date]:
    rows = conn.execute(
        "SELECT DISTINCT (to_timestamp(ts) AT TIME ZONE 'America/New_York')::date::text AS d FROM bars ORDER BY d"
    ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


# ------------------------------------------------------------------ indicators
def atr_d(hist: list[DailyRow], n: int) -> float | None:
    """Daily ATR over the prior n sessions (needs n+1 rows), exactly dsl.evaluator atr_d."""
    h = hist[-(n + 1):]
    if len(h) < n + 1:
        return None
    trs = [
        max(h[k].h - h[k].l, abs(h[k].h - h[k - 1].c), abs(h[k].l - h[k - 1].c))
        for k in range(1, len(h))
    ]
    return sum(trs) / len(trs)


def adv(hist: list[DailyRow], n: int) -> float | None:
    h = hist[-n:]
    if len(h) < n:
        return None
    return sum(x.c * x.v for x in h) / n


def rvol(hist: list[DailyRow], n: int, cum_v: float, minute_index: int) -> float | None:
    h = hist[-n:]
    if len(h) < n or not 0 <= minute_index < len(VOLUME_CUM):
        return None
    avg = sum(x.v for x in h) / n
    if avg <= 0:
        return None
    return cum_v / (avg * VOLUME_CUM[minute_index])


# ------------------------------------------------------------------ simulation
def run(cfg) -> dict:
    import psycopg

    conn = psycopg.connect(cfg["dsn"])
    days = bar_days(conn)
    if cfg["start"]:
        days = [d for d in days if d >= date.fromisoformat(cfg["start"])]
    if cfg["end"]:
        days = [d for d in days if d <= date.fromisoformat(cfg["end"])]

    cost_side = (cfg["slippage_bps"] + cfg["half_spread_bps"]) / 1e4  # per side, fraction
    equity = cfg["equity"]
    trades: list[dict] = []
    daily_pnl: dict[str, float] = {}
    coverage = []
    fp = hashlib.sha256()

    for d in days:
        day_rows = {}
        for sym in cfg["symbols"]:
            bars = load_bars(conn, sym, d)
            hist = load_daily(conn, sym, d)
            day_rows[sym] = (bars, hist)
            fp.update(f"{sym},{d},{len(bars)},".encode())
            if bars:
                fp.update(f"{bars[0][0]},{bars[-1][0]},{bars[-1][4]:.4f};".encode())
        coverage.append({"day": d.isoformat(),
                         "symbols": {s: len(v[0]) for s, v in day_rows.items()}})

        # ---- build candidates at the decision bars
        cands = []
        for sym in cfg["symbols"]:
            bars, hist = day_rows[sym]
            if len(bars) < cfg["rod_minutes"] or len(hist) < 21:
                continue
            prior_close = hist[-1].c
            if prior_close < cfg["min_price"]:
                continue
            a20 = adv(hist, 20)
            if a20 is None or a20 < cfg["min_adv"]:
                continue
            atr = atr_d(hist, 14)
            if atr is None or atr <= 0:
                continue
            idx = {ts: i for i, (ts, *_x) in enumerate(bars)}
            open_ts = int(datetime.combine(d, OPEN, tzinfo=ET).timestamp())
            # ROD3: close of the last bar inside the first rod_minutes minutes (the 15:00 bar)
            rod_bar = None
            for i, (ts, *_x) in enumerate(bars):
                if (ts - open_ts) // 60 <= cfg["rod_minutes"] - 1:
                    rod_bar = i
                else:
                    break
            if rod_bar is None:
                continue
            rod3 = bars[rod_bar][4] / prior_close - 1
            atr_frac = atr / prior_close
            drop_line = -cfg["drop_atr"] * atr_frac
            for dm in DECISION_MINS:
                ts_dec = open_ts + dm * 60
                if ts_dec not in idx:
                    continue
                i = idx[ts_dec]
                if i + 1 >= len(bars):
                    continue
                cum_v = sum(b[5] for b in bars[: i + 1])
                rv = rvol(hist, cfg["rvol_n"], cum_v, dm)
                # rvol filter is optional: disabled at 0 because stored bar volume has a units
                # break on/after 2026-06-13 (see the YAML assumptions and the research report).
                if cfg["rvol_min"] > 0 and (rv is None or rv < cfg["rvol_min"]):
                    continue
                if rod3 <= drop_line:
                    cands.append({
                        "symbol": sym, "day": d.isoformat(), "rod3": rod3, "rvol": rv,
                        "decision_index": i, "fill_index": i + 1, "atr": atr,
                        "prior_close": prior_close,
                    })
                break  # first qualifying decision bar only (same signal at both)

        cands.sort(key=lambda c: c["rod3"])  # most extreme loser first (cross-sectional intent)
        slots = min(cfg["max_open"], cfg["max_trades_per_day"])
        picked, seen = [], set()
        for c in cands:
            if len(picked) >= slots:
                break
            if c["symbol"] in seen:
                continue
            seen.add(c["symbol"])
            picked.append(c)

        day_pnl = 0.0
        for c in picked:
            bars, _hist = day_rows[c["symbol"]]
            fi = c["fill_index"]
            entry = bars[fi][1] * (1 + cost_side)  # next bar open plus costs (buy)
            rps = cfg["stop_atr"] * c["atr"]
            stop = entry - rps
            target = entry + cfg["target_r"] * rps
            qty = math.floor(equity * cfg["risk_per_trade"] / rps)
            qty = min(qty, math.floor(cfg["max_position_pct"] * equity / entry))
            if qty < 1:
                continue
            fees = qty * cfg["commission_per_share"] + cfg["commission_per_fill"]
            exit_px = exit_kind = None
            exit_i = None
            for j in range(fi, len(bars)):
                ts, o, h, l, cl, _v = bars[j]
                m = (ts - int(datetime.combine(d, OPEN, tzinfo=ET).timestamp())) // 60
                fresh = j == fi
                if l <= stop:  # stop first on ambiguity (conservative)
                    px = stop if fresh else min(o, stop)
                    exit_px, exit_kind, exit_i = px * (1 - cost_side), "stop", j
                    break
                if h >= target:
                    px = target if fresh else max(o, target)
                    exit_px, exit_kind, exit_i = px, "target", j
                    break
                if m >= FLATTEN_MIN:
                    exit_px, exit_kind, exit_i = cl * (1 - cost_side), "flatten", j
                    break
            if exit_px is None:
                exit_px, exit_kind, exit_i = bars[-1][4] * (1 - cost_side), "flatten", len(bars) - 1
            fees += qty * cfg["commission_per_share"] + cfg["commission_per_fill"]
            pnl_gross = (exit_px / (1 - cost_side) - entry / (1 + cost_side)) * qty  # strip cost rates -> raw-price pnl
            pnl_net = (exit_px - entry) * qty - fees
            trades.append({
                "day": c["day"], "symbol": c["symbol"], "qty": qty,
                "entry_index": fi, "exit_index": exit_i,
                "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
                "exit_kind": exit_kind, "stop": round(stop, 4), "target": round(target, 4),
                "rod3": round(c["rod3"], 6), "rvol": round(c["rvol"], 3) if c["rvol"] is not None else None,
                "atr_frac": round(c["atr"] / c["prior_close"], 6),
                "gross_pnl": round(pnl_gross, 4), "fees": round(fees, 4),
                "net_pnl": round(pnl_net, 4),
                "net_bps": round((exit_px / entry - 1) * 1e4, 3),
                "r_multiple": round(pnl_net / (rps * qty), 4) if rps * qty > 0 else None,
                "equity_before": round(equity, 2),
            })
            day_pnl += pnl_net
        daily_pnl[d.isoformat()] = day_pnl
        equity += day_pnl

    conn.close()
    return {"trades": trades, "daily": daily_pnl, "coverage": coverage,
            "final_equity": equity, "fingerprint": fp.hexdigest()}


# ------------------------------------------------------------------ statistics
def sharpe(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 3:
        return None
    sd = statistics.stdev(daily_rets)
    return statistics.mean(daily_rets) / sd * math.sqrt(252) if sd > 1e-12 else None


def tstat(xs: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / (sd / math.sqrt(len(xs))) if sd > 1e-12 else None


def max_drawdown(curve: list[float]) -> float:
    peak, mdd = curve[0], 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    return mdd


def block_bootstrap(daily_rets: list[float], n_boot: int, block: int, seed: int) -> dict:
    rng = random.Random(seed)
    n = len(daily_rets)
    if n < block * 3:
        return {}
    means, sharpes = [], []
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            k = rng.randrange(0, n - block + 1)
            sample.extend(daily_rets[k: k + block])
        sample = sample[:n]
        means.append(statistics.mean(sample))
        s = sharpe(sample)
        if s is not None:
            sharpes.append(s)
    means.sort()
    sharpes.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, max(0, int(p * len(xs))))]  # noqa: E731
    return {"n_boot": n_boot, "block": block,
            "mean_daily_p5": q(means, 0.05), "mean_daily_p50": q(means, 0.50),
            "mean_daily_p95": q(means, 0.95), "p_mean_le_0": sum(m <= 0 for m in means) / len(means),
            "sharpe_p5": q(sharpes, 0.05) if sharpes else None,
            "sharpe_p50": q(sharpes, 0.50) if sharpes else None,
            "p_sharpe_le_0": sum(s <= 0 for s in sharpes) / len(sharpes) if sharpes else None}


def summarize(trades: list[dict], daily: dict[str, float], equity0: float) -> dict:
    r3 = lambda x: round(x, 3) if x is not None else None  # noqa: E731
    days_sorted = sorted(daily)
    rets = []
    prev = equity0
    curve = [equity0]
    for d in days_sorted:
        cur = prev + daily[d]
        rets.append(cur / prev - 1 if prev > 0 else 0.0)
        curve.append(cur)
        prev = cur
    n = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    return {
        "sessions": len(days_sorted), "trades": n,
        "trades_per_day": round(n / len(days_sorted), 3) if days_sorted else None,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
        "gross_pnl": round(sum(t["gross_pnl"] for t in trades), 2),
        "fees": round(sum(t["fees"] for t in trades), 2),
        "expectancy_usd": round(sum(t["net_pnl"] for t in trades) / n, 2) if n else None,
        "expectancy_bps": round(statistics.mean([t["net_bps"] for t in trades]), 3) if n else None,
        "per_trade_tstat_bps": r3(tstat([t["net_bps"] for t in trades])) if n >= 3 else None,
        "daily_tstat": r3(tstat(rets)) if len(rets) >= 3 else None,
        "sharpe_daily": r3(sharpe(rets)),
        "profit_factor": round(sum(t["net_pnl"] for t in wins) / abs(sum(t["net_pnl"] for t in losses)), 3) if losses else None,
        "avg_win_bps": round(statistics.mean([t["net_bps"] for t in wins]), 3) if wins else None,
        "avg_loss_bps": round(statistics.mean([t["net_bps"] for t in losses]), 3) if losses else None,
        "max_drawdown_pct": round(100 * max_drawdown(curve), 3) if len(curve) > 1 else None,
        "final_equity": round(prev, 2),
        "exits": {k: sum(1 for t in trades if t["exit_kind"] == k) for k in ("stop", "target", "flatten")},
    }


# ------------------------------------------------------------------ main
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""),
                   help="PostgreSQL DSN (default: env DATABASE_URL)")
    p.add_argument("--symbols", default=",".join(SYMBOLS))
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--equity", type=float, default=100_000.0)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--min-adv", type=float, default=1_000_000_000.0)
    p.add_argument("--slippage-bps", type=float, default=2.0, help="per-side slippage, bps")
    p.add_argument("--half-spread-bps", type=float, default=1.0, help="per-side half spread, bps")
    p.add_argument("--commission-per-share", type=float, default=0.0)
    p.add_argument("--commission-per-fill", type=float, default=0.0)
    p.add_argument("--risk-per-trade", type=float, default=0.0025)
    p.add_argument("--max-position-pct", type=float, default=0.10)
    p.add_argument("--drop-atr", type=float, default=0.25)
    p.add_argument("--rod-minutes", type=int, default=330)
    p.add_argument("--rvol-min", type=float, default=0.0,
                   help="relative-volume floor at decision time; 0 disables the filter (default, "
                        "because stored bar volume has a units break on/after 2026-06-13)")
    p.add_argument("--rvol-n", type=int, default=20)
    p.add_argument("--stop-atr", type=float, default=0.3)
    p.add_argument("--target-r", type=float, default=3.0)
    p.add_argument("--max-trades-per-day", type=int, default=3)
    p.add_argument("--max-open", type=int, default=3)
    p.add_argument("--cost-stress", default="1,2,3", help="multipliers on slippage+spread+commissions")
    p.add_argument("--bootstrap", type=int, default=10_000)
    p.add_argument("--block", type=int, default=5)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--outdir", default="")
    a = p.parse_args(argv)
    cfg = vars(a)
    cfg["symbols"] = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if not cfg["dsn"]:
        p.error("--dsn or DATABASE_URL is required")
    return cfg


def main(argv=None) -> int:
    cfg = parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(cfg["outdir"] or REPO / "reports" / f"eod_reversal_{stamp}")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[data] {cfg['dsn'].split('@')[-1]} symbols={','.join(cfg['symbols'])}")
    res = run(cfg)
    trades, daily = res["trades"], res["daily"]
    days_sorted = sorted(daily)
    print(f"[data] sessions={len(days_sorted)} {days_sorted[0] if days_sorted else '-'}..{days_sorted[-1] if days_sorted else '-'} "
          f"trades={len(trades)} fingerprint={res['fingerprint'][:16]}")

    # ---- main summary + bootstrap on daily returns
    eq0 = cfg["equity"]
    prev = eq0
    rets = []
    for d in days_sorted:
        cur = prev + daily[d]
        rets.append(cur / prev - 1 if prev > 0 else 0.0)
        prev = cur
    summary = summarize(trades, daily, eq0)
    summary["final_equity"] = round(res["final_equity"], 2)
    boot = block_bootstrap(rets, cfg["bootstrap"], cfg["block"], cfg["seed"])
    summary["bootstrap_daily_returns"] = boot

    # ---- chronological holdout: first 60% of sessions in-sample, last 40% holdout
    cut = days_sorted[int(len(days_sorted) * 0.6)] if days_sorted else None
    for label, flt in (("in_sample", lambda d: d <= cut), ("holdout", lambda d: d > cut)):
        dd = {d: p for d, p in daily.items() if flt(d)}
        tt = [t for t in trades if flt(t["day"])]
        summary[label] = {"from": min(dd) if dd else None, "to": max(dd) if dd else None,
                          **summarize(tt, dd, eq0)}

    # ---- per-month table (walk-forward by year is infeasible: one year of stored bars)
    months: dict[str, list] = {}
    for t in trades:
        months.setdefault(t["day"][:7], []).append(t)
    summary["per_month"] = {m: {"trades": len(tt),
                                "net_pnl": round(sum(x["net_pnl"] for x in tt), 2),
                                "expectancy_bps": round(statistics.mean([x["net_bps"] for x in tt]), 3),
                                "win_rate": round(sum(x["net_pnl"] > 0 for x in tt) / len(tt), 4)}
                            for m, tt in sorted(months.items())}

    # ---- regime splits: ATR-percentile of the day (vs trailing 60 sessions) and basket direction
    import psycopg
    conn = psycopg.connect(cfg["dsn"])
    atr_pct_rank = {}
    for t in trades:
        sym, d = t["symbol"], date.fromisoformat(t["day"])
        hist = load_daily(conn, sym, d)
        a = atr_d(hist, 14)
        if not a:
            continue
        series = []
        for k in range(60, 0, -1):
            hh = hist[:-k] if k else hist
            aa = atr_d(hh, 14)
            if aa and hh:
                series.append(aa / hh[-1].c)
        cur = a / hist[-1].c
        atr_pct_rank[(sym, t["day"])] = (sum(x <= cur for x in series) / len(series)) if series else None
    for t in trades:
        t["atr_pctile"] = atr_pct_rank.get((t["symbol"], t["day"]))
    conn.close()
    basket_ret = {}
    syms = cfg["symbols"]
    day_set = sorted({t["day"] for t in trades})
    conn = psycopg.connect(cfg["dsn"])
    for dstr in day_set:
        rs = []
        for sym in syms:
            bars, hist = load_bars(conn, sym, date.fromisoformat(dstr)), load_daily(conn, sym, date.fromisoformat(dstr))
            if bars and hist:
                rs.append(bars[-1][4] / hist[-1].c - 1)
        basket_ret[dstr] = statistics.mean(rs) if rs else None
    conn.close()
    for t in trades:
        t["basket_ret"] = round(basket_ret[t["day"]], 6) if basket_ret.get(t["day"]) is not None else None

    def split_stats(key_fn, label_fn):
        out = {}
        for t in trades:
            lab = label_fn(t)
            if lab is None:
                continue
            out.setdefault(lab, []).append(t)
        return {k: {"trades": len(v),
                    "expectancy_bps": round(statistics.mean([x["net_bps"] for x in v]), 3),
                    "win_rate": round(sum(x["net_pnl"] > 0 for x in v) / len(v), 4),
                    "net_pnl": round(sum(x["net_pnl"] for x in v), 2)}
                for k, v in sorted(out.items())}
    summary["regime_vol"] = split_stats(None, lambda t: ("high_vol" if (t["atr_pctile"] or 0) >= 0.5 else "low_vol"))
    summary["regime_market"] = split_stats(None, lambda t: (
        "basket_up" if (t["basket_ret"] or 0) > 0 else "basket_down") if t["basket_ret"] is not None else None)
    summary["per_symbol"] = split_stats(None, lambda t: t["symbol"])

    # ---- cost stress: rerun whole simulation at 1x/2x/3x execution costs
    stress = {}
    for mult in [float(x) for x in str(cfg["cost_stress"]).split(",") if x.strip()]:
        c2 = dict(cfg)
        c2["slippage_bps"] = cfg["slippage_bps"] * mult
        c2["half_spread_bps"] = cfg["half_spread_bps"] * mult
        c2["commission_per_share"] = cfg["commission_per_share"] * mult
        c2["commission_per_fill"] = cfg["commission_per_fill"] * mult
        r2 = run(c2)
        s2 = summarize(r2["trades"], r2["daily"], eq0)
        stress[f"{mult:g}x"] = {"trades": s2["trades"], "net_pnl": s2["net_pnl"],
                                "expectancy_bps": s2["expectancy_bps"],
                                "sharpe_daily": s2["sharpe_daily"], "win_rate": s2["win_rate"]}
    summary["cost_stress"] = stress

    # ---- save artifacts
    (outdir / "config.json").write_text(json.dumps({k: v for k, v in cfg.items()}, indent=2, default=str))
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if trades:
        with open(outdir / "trades.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
    with open(outdir / "daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "net_pnl"])
        for d in days_sorted:
            w.writerow([d, round(daily[d], 4)])

    lines = [
        f"# End-of-day reversal research backtest — {stamp}",
        "",
        f"- Strategy: `strategies/eod_reversal_losers_long.v1.yaml` (Baltussen, Da & Soebhag 2025 long-loser side)",
        f"- Data: stored 1-minute bars, {len(days_sorted)} sessions, {days_sorted[0] if days_sorted else '-'} .. {days_sorted[-1] if days_sorted else '-'}",
        f"- Symbols: {', '.join(cfg['symbols'])}",
        f"- Data fingerprint (sha256): `{res['fingerprint']}`",
        f"- Costs per side: slippage {cfg['slippage_bps']} bps + half-spread {cfg['half_spread_bps']} bps; "
        f"commission ${cfg['commission_per_share']}/share + ${cfg['commission_per_fill']}/fill",
        "",
        "## Headline (net of costs)",
        "",
        f"- trades {summary['trades']} ({summary['trades_per_day']}/day), win rate {summary['win_rate']}",
        f"- expectancy {summary['expectancy_usd']}$/trade = {summary['expectancy_bps']} bps/trade "
        f"(per-trade t {summary['per_trade_tstat_bps']})",
        f"- net P&L {summary['net_pnl']}$ (gross {summary['gross_pnl']}$, fees {summary['fees']}$)",
        f"- daily Sharpe {summary['sharpe_daily']}, daily t-stat {summary['daily_tstat']}, "
        f"profit factor {summary['profit_factor']}, max drawdown {summary['max_drawdown_pct']}%",
        f"- exits: {summary['exits']}",
        "",
        "## Holdout (chronological 60/40)",
        "",
        f"- in-sample {summary['in_sample']['from']}..{summary['in_sample']['to']}: "
        f"trades {summary['in_sample']['trades']}, expectancy {summary['in_sample']['expectancy_bps']} bps, "
        f"Sharpe {summary['in_sample']['sharpe_daily']}",
        f"- holdout {summary['holdout']['from']}..{summary['holdout']['to']}: "
        f"trades {summary['holdout']['trades']}, expectancy {summary['holdout']['expectancy_bps']} bps, "
        f"Sharpe {summary['holdout']['sharpe_daily']}",
        "",
        "## Cost stress (execution costs x1/x2/x3)",
        "",
    ]
    for k, v in stress.items():
        lines.append(f"- {k}: expectancy {v['expectancy_bps']} bps, net {v['net_pnl']}$, Sharpe {v['sharpe_daily']}")
    lines += ["", "## Bootstrap of daily returns (block resampling)", ""]
    if boot:
        lines.append(f"- mean daily return p5/p50/p95: {boot['mean_daily_p5']:.5f} / {boot['mean_daily_p50']:.5f} / "
                     f"{boot['mean_daily_p95']:.5f}; P(mean<=0)={boot['p_mean_le_0']:.3f}; "
                     f"Sharpe p5/p50: {boot['sharpe_p5']} / {boot['sharpe_p50']}; P(Sharpe<=0)={boot['p_sharpe_le_0']:.3f}")
    lines += ["", "## Per month", ""]
    for m, v in summary["per_month"].items():
        lines.append(f"- {m}: trades {v['trades']}, net {v['net_pnl']}$, expectancy {v['expectancy_bps']} bps, win {v['win_rate']}")
    lines += ["", "## Regimes", "", f"- volatility: {json.dumps(summary['regime_vol'])}",
              f"- market: {json.dumps(summary['regime_market'])}", "", "## Per symbol", ""]
    for s, v in summary["per_symbol"].items():
        lines.append(f"- {s}: {json.dumps(v)}")
    (outdir / "results.md").write_text("\n".join(lines) + "\n")

    print(f"[done] artifacts in {outdir}")
    print(json.dumps({k: summary[k] for k in ("trades", "win_rate", "expectancy_bps", "per_trade_tstat_bps",
                                              "daily_tstat", "sharpe_daily", "net_pnl", "profit_factor",
                                              "max_drawdown_pct")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
