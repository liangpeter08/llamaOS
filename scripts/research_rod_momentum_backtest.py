"""Rest-of-day market intraday momentum on SPY/QQQ/IWM: standalone research backtest.

Strategy (pre-registered before any data was loaded; see reports/rod_momentum/README.md):
  signal  r = price at 15:30 ET / prior close - 1   (Baltussen, Da, Lammers & Martens 2021, "r_ROD")
  long    r >  k * vol_d(20)      short  r < -k * vol_d(20)       k = 0.5
  entry   next 1-minute bar open after the 15:29 bar closes (15:30), marketable
  exit    15:55 flatten (the account's flatten_by): the close of the 15:54 bar, as core/session.py fills it;
          protective stop 0.5 x atr_d(14); target 4R
This mirrors strategies/rod_momentum_{long,short}.v1.yaml. vol_d and atr_d are computed exactly as dsl/features.py
and dsl/evaluator.py compute them (sample stdev of n daily returns; simple mean of n true ranges).

    .venv/bin/python scripts/research_rod_momentum_backtest.py                  # download (cached) and run
    .venv/bin/python scripts/research_rod_momentum_backtest.py --refresh        # re-download bars and quotes
    .venv/bin/python scripts/research_rod_momentum_backtest.py --start 2016-01-01 --end 2026-10-15

Data: Alpaca Market Data v2, SIP feed, split-adjusted 1-minute and daily bars; NBBO quotes for the spread sample.
Keys are read from the environment (ALPACA_KEY_ID / ALPACA_SECRET_KEY), falling back to secrets/broker.env; they are
never printed. VIX daily closes (regime split only) come from Cboe's public history file. Cached inputs go to
data/research/ (git-ignored); results go to reports/rod_momentum/. Research only: this script never places orders.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.env import load  # noqa: E402

ET = ZoneInfo("America/New_York")
DATA_URL = "https://data.alpaca.markets"
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
CACHE = ROOT / "data" / "research"
OUT = ROOT / "reports" / "rod_momentum"

UNIVERSE = ["SPY", "QQQ", "IWM"]
ROBUSTNESS = ["DIA"]  # not traded by the strategy; reported once as an out-of-universe check

# Pre-registered specification (identical to the YAML params)
K = 0.5            # band_k: |r| must exceed K x vol_d(VOL_N)
VOL_N = 20         # vol_n
ATR_N = 14
STOP_ATR = 0.5     # stop_atr
TARGET_R = 4       # target_r
RANK_M = 200       # vol_rank(VOL_N, RANK_M): the DSL caps daily lookback at 250 sessions (20 + 200 + 1 fits)
RANK_MIN_V2 = 0.67  # v2 only (post hoc): trade only when today's vol_d(20) is in the top third of the last 200 sessions
POSITION = 0.10    # account max_position: 10% of equity per position (binds before risk_per_trade 0.25%)

# Execution costs, per side, as fractions of price (see the cost section of the report)
SLIP = 0.0001                     # 1 bp explicit slippage/latency per fill
SEC_FEE = 20.60e-6                # SEC Section 31, $20.60 per $1M sold (FY2026 rate from 2026-04-04)
TAF_PER_SHARE = 0.000195          # FINRA TAF per share sold (2026), cap ignored (orders here are far below it)
HALF_SPREAD_FLOOR = {"SPY": 0.5e-4, "QQQ": 0.5e-4, "IWM": 1.0e-4, "DIA": 1.5e-4}  # conservative floor, bps/1e4

K_GRID = [0.0, 0.25, 0.5, 1.0]    # walk-forward candidates; every value is counted as a research trial
BOOT_REPS, BOOT_BLOCK, PERM_REPS, SEED = 10_000, 10, 5_000, 20261016


# ------------------------------------------------------------------------------------------------ data
def headers() -> dict:
    if not (os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY")):
        load(ROOT / "secrets" / "broker.env")
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("No Alpaca market-data keys in the environment or secrets/broker.env.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get(client: httpx.Client, path: str, params: dict) -> dict:
    for attempt in range(6):
        r = client.get(DATA_URL + path, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def paged(client, path, params, key):
    params = dict(params)
    while True:
        page = get(client, path, params)
        yield page.get(key) or {}
        token = page.get("next_page_token")
        if not token:
            return
        params["page_token"] = token


def et_of(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)


def sip_end(day: date, utc_time: str) -> str:
    """The free data plan refuses SIP data newer than 15 minutes: clamp the request end."""
    want = datetime.fromisoformat(f"{day}T{utc_time}+00:00")
    return min(want, datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat().replace("+00:00", "Z")


def fetch_daily(client, sym, start: date, end: date) -> list[dict]:
    rows = []
    params = {"symbols": sym, "timeframe": "1Day", "start": f"{start}T00:00:00Z", "end": sip_end(end, "23:59:00"),
              "feed": "sip", "adjustment": "split", "limit": 10000, "sort": "asc"}
    for bars in paged(client, "/v2/stocks/bars", params, "bars"):
        for b in bars.get(sym, []):
            rows.append({"day": et_of(b["t"]).date().isoformat(), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return rows


def fetch_minute_extract(client, sym, start: date, end: date) -> dict:
    """Per session: the 09:59 and 15:29 closes, the 15:59 close, and every bar from 15:30 to 15:59 (o, h, l, c)."""
    out: dict[str, dict] = {}
    cur = start
    while cur <= end:
        nxt = min(date(cur.year, 12, 31), end)
        params = {"symbols": sym, "timeframe": "1Min", "start": f"{cur}T13:00:00Z", "end": sip_end(nxt, "21:30:00"),
                  "feed": "sip", "adjustment": "split", "limit": 10000, "sort": "asc"}
        for bars in paged(client, "/v2/stocks/bars", params, "bars"):
            for b in bars.get(sym, []):
                t = et_of(b["t"])
                hm = t.hour * 60 + t.minute
                if hm < 570 or hm >= 960:
                    continue
                d = out.setdefault(t.date().isoformat(), {"late": {}})
                d["n"] = d.get("n", 0) + 1
                if hm == 599:
                    d["c0959"] = b["c"]
                if hm == 929:
                    d["c1529"] = b["c"]
                if hm >= 930:
                    d["late"][hm - 930] = [b["o"], b["h"], b["l"], b["c"]]
        print(f"  {sym} {cur.year}: {len(out)} sessions so far", flush=True)
        cur = nxt + timedelta(days=1)
    return out


def fetch_spreads(client, sym, days: list[str]) -> list[dict]:
    """Median quoted NBBO spread (bps of mid) in the 20 seconds around 15:30:00 and 15:55:00 on each sampled day."""
    rows = []
    for day in days:
        for label, hh, mm in (("15:30", 15, 30), ("15:55", 15, 55)):
            t0 = datetime(*map(int, day.split("-")), hh, mm, tzinfo=ET) - timedelta(seconds=10)
            params = {"symbols": sym, "start": t0.astimezone(timezone.utc).isoformat(),
                      "end": (t0 + timedelta(seconds=20)).astimezone(timezone.utc).isoformat(),
                      "feed": "sip", "limit": 2000}
            q = get(client, "/v2/stocks/quotes", params).get("quotes", {}).get(sym, [])
            spreads = [(x["ap"] - x["bp"]) / ((x["ap"] + x["bp"]) / 2) * 1e4 for x in q
                       if x.get("ap", 0) > 0 and x.get("bp", 0) > 0 and x["ap"] >= x["bp"]]
            if spreads:
                rows.append({"symbol": sym, "day": day, "time": label, "quotes": len(spreads),
                             "median_spread_bps": statistics.median(spreads)})
    return rows


def fetch_vix() -> dict[str, float]:
    r = httpx.get(VIX_URL, timeout=30, follow_redirects=True)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        m, d, y = row["DATE"].split("/")
        out[f"{y}-{m}-{d}"] = float(row["CLOSE"])
    return out


def load_inputs(start: date, end: date, refresh: bool) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"rod_inputs_{start}_{end}.json.gz"
    if path.exists() and not refresh:
        return json.loads(gzip.decompress(path.read_bytes()))
    data = {"daily": {}, "minute": {}, "spreads": [], "vix": {}}
    with httpx.Client(headers=headers(), timeout=60) as client:
        for sym in UNIVERSE + ROBUSTNESS:
            print(f"downloading {sym}", flush=True)
            data["daily"][sym] = fetch_daily(client, sym, start - timedelta(days=420), end)
            data["minute"][sym] = fetch_minute_extract(client, sym, start, end)
        recent = [d["day"] for d in data["daily"]["SPY"] if d["day"] <= end.isoformat()][-60:]
        for sym in UNIVERSE + ROBUSTNESS:
            data["spreads"] += fetch_spreads(client, sym, recent)
    data["vix"] = fetch_vix()
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    return data


# ------------------------------------------------------------------------------------------------ features
def daily_features(rows: list[dict]) -> dict[str, dict]:
    """For each session: prior close, vol_d(VOL_N), atr_d(ATR_N), vol_rank(VOL_N, RANK_M) from COMPLETED sessions only."""
    out = {}
    closes = [r["c"] for r in rows]
    rets = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    vols = [None] * len(rows)  # vol_d as of session i (uses sessions < i)
    for i in range(VOL_N + 1, len(rows)):
        vols[i] = statistics.stdev(rets[i - VOL_N:i])
    for i in range(1, len(rows)):
        f = {"prior_close": closes[i - 1], "close": closes[i], "vol": vols[i], "atr": None, "vol_rank": None}
        if i >= ATR_N + 1:
            trs = [max(rows[k]["h"] - rows[k]["l"], abs(rows[k]["h"] - rows[k - 1]["c"]), abs(rows[k]["l"] - rows[k - 1]["c"]))
                   for k in range(i - ATR_N, i)]
            f["atr"] = sum(trs) / ATR_N
        if vols[i] is not None and i - RANK_M >= 0 and all(v is not None for v in vols[i - RANK_M:i]):
            f["vol_rank"] = sum(v <= vols[i] for v in vols[i - RANK_M:i]) / RANK_M
        f["prev_day"] = rows[i - 1]["day"]
        out[rows[i]["day"]] = f
    return out


# ------------------------------------------------------------------------------------------------ simulation
def costs_per_side(sym: str, price: float, spreads: dict[str, float], mult: float, model: str = "preregistered") -> float:
    if model == "measured":  # measured half-spread only: no floor, no extra slippage (an optimistic lower bound)
        return mult * spreads.get(sym, 0.0) / 2e4
    half = max(spreads.get(sym, 0.0) / 2e4, HALF_SPREAD_FLOOR[sym])
    return mult * (half + SLIP)


def simulate(data: dict, *, syms, k=K, signal="rod", exit_at="15:55", cost_mult=1.0, start=None, end=None,
             stop_atr=STOP_ATR, cost_model="preregistered", rank_min=None) -> list[dict]:
    """One row per trade; returns are fractions of entry notional."""
    spreads = measured_spreads(data)
    trades = []
    for sym in syms:
        feats = daily_features(data["daily"][sym])
        for day, m in sorted(data["minute"][sym].items()):
            if (start and day < start) or (end and day > end):
                continue
            f = feats.get(day)
            late = {int(k_): v for k_, v in m["late"].items()}
            if not f or f["vol"] is None or f["atr"] is None or "c1529" not in m or 0 not in late or 24 not in late:
                continue  # fail closed: missing history, decision bar, entry bar or exit bar (also skips half-days)
            if rank_min is not None and (f["vol_rank"] is None or f["vol_rank"] < rank_min):
                continue  # v2 regime gate (fails closed without 221 sessions of history)
            pc = f["prior_close"]
            if signal == "rod":
                r = m["c1529"] / pc - 1
            elif "c0959" in m:
                r = m["c0959"] / pc - 1  # Gao et al. (2018): previous close to 10:00
            else:
                continue
            band = k * f["vol"]
            side = 1 if r > band else -1 if r < -band else 0
            if side == 0:
                continue
            o = late[0][0]
            c = costs_per_side(sym, o, spreads, cost_mult, cost_model)
            fill = o * (1 + side * c)
            stop = o - side * stop_atr * f["atr"]
            target = o + side * TARGET_R * stop_atr * f["atr"]
            exit_px, kind = None, "flatten"
            for j in range(0, 25):  # bars 15:30 .. 15:54 carry the bracket; stop assumed first
                if j not in late:
                    continue
                bo, bh, bl, _ = late[j]
                if (side > 0 and bl <= stop) or (side < 0 and bh >= stop):
                    exit_px, kind = ((min(bo, stop) if side > 0 else max(bo, stop)) if j else stop), "stop"
                    break
                if (side > 0 and bh >= target) or (side < 0 and bl <= target):
                    exit_px, kind = ((max(bo, target) if side > 0 else min(bo, target)) if j else target), "target"
                    break
            if exit_px is None:
                if exit_at == "15:55":
                    exit_px = late[24][3]  # core/session.py flattens at the close of the bar that ends at 15:55
                else:  # paper-faithful hold to the official close (market-on-close); NOT available in this system
                    exit_px, kind = f["close"], "close"
            x_cost = 0.0 if kind == "close" else c
            if kind == "target":
                x_cost = 0.0  # resting limit: no adverse slippage, as in core/exchange.py
            exit_fill = exit_px * (1 - side * x_cost)
            gross = side * (exit_px / o - 1)
            sell_px = exit_fill if side > 0 else fill
            fees = cost_mult * (SEC_FEE * sell_px + TAF_PER_SHARE) / fill
            net = side * (exit_fill / fill - 1) - fees
            trades.append({"symbol": sym, "day": day, "side": "long" if side > 0 else "short", "signal": r,
                           "band": band, "entry": fill, "exit": exit_fill, "exit_kind": kind, "gross": gross,
                           "net": net, "cost": gross - net, "vol_rank": f["vol_rank"],
                           "vix_prev": data["vix"].get(f["prev_day"])})
    return trades


def measured_spreads(data) -> dict[str, float]:
    by = {}
    for row in data.get("spreads", []):
        by.setdefault(row["symbol"], []).append(row["median_spread_bps"])
    return {s: statistics.median(v) for s, v in by.items()}


# ------------------------------------------------------------------------------------------------ statistics
def daily_series(trades, sessions: list[str], weight: float) -> np.ndarray:
    """Account return per session: each trade is `weight` of equity; sessions with no trade return 0."""
    pnl = dict.fromkeys(sessions, 0.0)
    for t in trades:
        if t["day"] in pnl:
            pnl[t["day"]] += weight * t["net"]
    return np.array([pnl[d] for d in sessions])


def sharpe(x: np.ndarray) -> float | None:
    sd = x.std(ddof=1) if len(x) > 2 else 0
    return float(x.mean() / sd * math.sqrt(252)) if sd > 0 else None


def max_drawdown(x: np.ndarray) -> float:
    eq = np.cumprod(1 + x)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float((eq / peak - 1).min()) if len(x) else 0.0


def block_bootstrap(x: np.ndarray, rng, reps=BOOT_REPS, block=BOOT_BLOCK) -> dict:
    n = len(x)
    nb = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(reps, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(reps, -1)[:, :n]
    s = x[idx]
    means = s.mean(1) * 252
    sds = s.std(1, ddof=1)
    shs = np.where(sds > 0, s.mean(1) / np.where(sds > 0, sds, 1) * math.sqrt(252), 0)
    return {"ann_return_ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "sharpe_ci95": [float(np.percentile(shs, 2.5)), float(np.percentile(shs, 97.5))],
            "p_sharpe_le_0": float((shs <= 0).mean())}


def deflated_sharpe(x: np.ndarray, trial_sharpes_daily: list[float]) -> dict:
    """Bailey & Lopez de Prado (2014): probability that the true Sharpe exceeds the best of N unskilled trials."""
    n = len(x)
    sr = x.mean() / x.std(ddof=1)
    z = (x - x.mean()) / x.std(ddof=0)
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    N = len(trial_sharpes_daily)
    v = statistics.variance(trial_sharpes_daily) if N > 1 else 0.0
    g, nd = 0.5772156649, NormalDist()
    sr0 = math.sqrt(v) * ((1 - g) * nd.inv_cdf(1 - 1 / N) + g * nd.inv_cdf(1 - 1 / (N * math.e))) if N > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    psr0 = nd.cdf((sr - 0) * math.sqrt(n - 1) / denom)
    dsr = nd.cdf((sr - sr0) * math.sqrt(n - 1) / denom)
    return {"trials": N, "daily_sharpe": float(sr), "sr0_daily": sr0, "skew": skew, "kurtosis": kurt,
            "psr_vs_zero": psr0, "deflated_sharpe_prob": dsr}


def trade_stats(trades) -> dict:
    if not trades:
        return {"trades": 0}
    net = np.array([t["net"] for t in trades]) * 1e4
    gross = np.array([t["gross"] for t in trades]) * 1e4
    wins, losses = net[net > 0], net[net < 0]
    sd = net.std(ddof=1) if len(net) > 1 else 0
    return {"trades": len(net), "win_rate": float((net > 0).mean()), "gross_bps": float(gross.mean()),
            "cost_bps": float((gross - net).mean()), "net_bps": float(net.mean()),
            "t_net": float(net.mean() / (sd / math.sqrt(len(net)))) if sd > 0 else None,
            "t_gross": float(gross.mean() / (gross.std(ddof=1) / math.sqrt(len(gross)))) if len(gross) > 1 and gross.std(ddof=1) > 0 else None,
            "avg_win_bps": float(wins.mean()) if len(wins) else None,
            "avg_loss_bps": float(losses.mean()) if len(losses) else None,
            "payoff": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
            "stops": sum(t["exit_kind"] == "stop" for t in trades)}


def permutation_p(data, trades, rng, reps=PERM_REPS) -> float:
    """Keep the traded days and symbols, randomize the side: is the sign of the signal worth anything?"""
    if not trades:
        return 1.0
    g = np.array([t["gross"] * (1 if t["side"] == "long" else -1) for t in trades])  # long-only market move
    cost = np.array([t["cost"] for t in trades])
    obs = np.array([t["net"] for t in trades]).mean()
    signs = rng.choice([-1.0, 1.0], size=(reps, len(g)))
    null = (signs * g[None, :] - cost[None, :]).mean(1)
    return float((null >= obs).mean())


def summarize(trades, sessions, weight, rng=None) -> dict:
    x = daily_series(trades, sessions, weight)
    out = {"trade": trade_stats(trades), "sessions": len(sessions),
           "trade_days": int(len({t["day"] for t in trades})), "ann_return": float(x.mean() * 252),
           "ann_vol": float(x.std(ddof=1) * math.sqrt(252)) if len(x) > 2 else None,
           "sharpe": sharpe(x), "max_drawdown": max_drawdown(x),
           "t_daily": float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if x.std(ddof=1) > 0 else None}
    if rng is not None:
        out["bootstrap"] = block_bootstrap(x, rng)
    return out


def by(trades, key) -> dict[str, list]:
    out: dict[str, list] = {}
    for t in trades:
        out.setdefault(key(t), []).append(t)
    return dict(sorted(out.items()))


def vix_bucket(v):
    if v is None:
        return "n/a"
    return "<15" if v < 15 else "15-20" if v < 20 else "20-30" if v < 30 else ">=30"


def rank_bucket(v):
    if v is None:
        return "n/a"
    return "calm (<1/3)" if v < 1 / 3 else "middle" if v < 2 / 3 else "volatile (>=2/3)"


# ------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2026-10-15")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    data = load_inputs(start, end, a.refresh)
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    rng = np.random.default_rng(SEED)
    sessions = sorted(d for d, m in data["minute"]["SPY"].items() if a.start <= d <= a.end and "c1529" in m)
    w = POSITION  # account view: 10% of equity per position, at most 3 open (one per ETF)

    primary = simulate(data, syms=UNIVERSE)
    trials = {
        "primary: rod k=0.5, exit 15:55": primary,
        "rod sign rule k=0, exit 15:55": simulate(data, syms=UNIVERSE, k=0.0),
        "rod k=0.25, exit 15:55": simulate(data, syms=UNIVERSE, k=0.25),
        "rod k=1.0, exit 15:55": simulate(data, syms=UNIVERSE, k=1.0),
        "gao first-half-hour sign rule, exit 15:55": simulate(data, syms=UNIVERSE, k=0.0, signal="onfh"),
        "rod k=0.5, hold to 16:00 close (paper; not deployable)": simulate(data, syms=UNIVERSE, exit_at="close"),
        "POST HOC v2: k=0.5 + vol_rank(20,200) >= 0.67": simulate(data, syms=UNIVERSE, rank_min=RANK_MIN_V2),
    }
    results = {"generated": datetime.now(ET).isoformat(timespec="seconds"), "data_fingerprint": fingerprint,
               "window": [sessions[0], sessions[-1]], "sessions": len(sessions), "universe": UNIVERSE,
               "spec": {"k": K, "vol_n": VOL_N, "atr_n": ATR_N, "stop_atr": STOP_ATR, "target_r": TARGET_R,
                        "position": POSITION, "entry": "15:30 bar open", "exit": "15:54 bar close (15:55)"},
               "costs": {"slippage_bps_per_side": SLIP * 1e4, "sec_fee_per_million": SEC_FEE * 1e6,
                         "taf_per_share": TAF_PER_SHARE, "half_spread_floor_bps": {s: v * 1e4 for s, v in HALF_SPREAD_FLOOR.items()},
                         "measured_spread_bps_median": measured_spreads(data),
                         "spread_sample": f"{len(data['spreads'])} symbol-day-times, last 60 sessions, +/-10s around 15:30:00 and 15:55:00"},
               "trials": {}}
    for name, tr in trials.items():
        results["trials"][name] = summarize(tr, sessions, w, rng if name.startswith("primary") else None)

    # Walk-forward: choose k on all prior years by net daily Sharpe, apply to the next year (first OOS year 2018)
    years = sorted({d[:4] for d in sessions})
    wf, wf_trades = [], []
    for y in years[2:]:
        train_sessions = [d for d in sessions if d[:4] < y]
        best = max(K_GRID, key=lambda kk: sharpe(daily_series(
            simulate(data, syms=UNIVERSE, k=kk, end=f"{int(y) - 1}-12-31"), train_sessions, w)) or -9)
        test = simulate(data, syms=UNIVERSE, k=best, start=f"{y}-01-01", end=f"{y}-12-31")
        fixed = [t for t in primary if t["day"][:4] == y]
        ys = [d for d in sessions if d[:4] == y]
        wf.append({"year": y, "chosen_k": best, "wf": summarize(test, ys, w), "fixed_k0.5": summarize(fixed, ys, w)})
        wf_trades += test
    oos_sessions = [d for d in sessions if d[:4] >= years[2]]
    results["walk_forward"] = {"folds": wf, "combined_oos": summarize(wf_trades, oos_sessions, w),
                               "fixed_primary_same_years": summarize([t for t in primary if t["day"][:4] >= years[2]], oos_sessions, w)}

    results["primary_by_year"] = {y: summarize(tr, [d for d in sessions if d[:4] == y], w) for y, tr in by(primary, lambda t: t["day"][:4]).items()}
    results["primary_by_side"] = {s: summarize(tr, sessions, w) for s, tr in by(primary, lambda t: t["side"]).items()}
    results["primary_by_symbol"] = {s: trade_stats(tr) for s, tr in by(primary, lambda t: t["symbol"]).items()}
    results["primary_by_vix"] = {b: trade_stats(tr) for b, tr in by(primary, lambda t: vix_bucket(t["vix_prev"])).items()}
    results["primary_by_vol_rank"] = {b: trade_stats(tr) for b, tr in by(primary, lambda t: rank_bucket(t["vol_rank"])).items()}
    results["primary_by_exit"] = {b: trade_stats(tr) for b, tr in by(primary, lambda t: t["exit_kind"]).items()}
    results["cost_stress"] = {f"{m}x": summarize(simulate(data, syms=UNIVERSE, cost_mult=m), sessions, w) for m in (1, 2, 3)}
    # Cost sensitivity (not new trials: the same trades under other cost assumptions)
    results["cost_sensitivity"] = {
        "zero costs (gross)": summarize(simulate(data, syms=UNIVERSE, cost_mult=0.0), sessions, w),
        "measured half-spread + fees, no slippage": summarize(simulate(data, syms=UNIVERSE, cost_model="measured"), sessions, w),
        "pre-registered (spread floor + 1 bp slippage + fees)": summarize(primary, sessions, w)}
    results["robustness_dia"] = trade_stats(simulate(data, syms=ROBUSTNESS))
    v2 = trials["POST HOC v2: k=0.5 + vol_rank(20,200) >= 0.67"]
    results["v2_post_hoc"] = {
        "note": "Regime gate chosen AFTER seeing the 2016-2026 regime split: in-sample, not validated.",
        "by_year": {y: summarize(tr, [d for d in sessions if d[:4] == y], w) for y, tr in by(v2, lambda t: t["day"][:4]).items()},
        "by_side": {s_: trade_stats(tr) for s_, tr in by(v2, lambda t: t["side"]).items()},
        "measured_costs": summarize(simulate(data, syms=UNIVERSE, rank_min=RANK_MIN_V2, cost_model="measured"), sessions, w),
        "cost_2x": summarize(simulate(data, syms=UNIVERSE, rank_min=RANK_MIN_V2, cost_mult=2), sessions, w),
        "bootstrap": block_bootstrap(daily_series(v2, sessions, w), rng)}
    results["permutation_p_primary"] = permutation_p(data, primary, rng)
    trial_sr = [x.mean() / x.std(ddof=1) for x in (daily_series(tr, sessions, w) for tr in trials.values())]
    results["deflated_sharpe_primary"] = deflated_sharpe(daily_series(primary, sessions, w), trial_sr)
    pyears = results["primary_by_year"]
    results["falsification_gates"] = {
        "oos_t_net_ge_2": (results["trials"]["primary: rod k=0.5, exit 15:55"]["trade"]["t_net"] or 0) >= 2,
        "min_trades_per_year_ge_30": min(v["trade"]["trades"] for v in pyears.values()) >= 30,
        "net_positive": results["trials"]["primary: rod k=0.5, exit 15:55"]["trade"]["net_bps"] > 0,
        "positive_years": f"{sum(v['trade']['net_bps'] > 0 for v in pyears.values())}/{len(pyears)}",
        "permutation_p_lt_0.05": results["permutation_p_primary"] < 0.05,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=float))
    with open(OUT / "trades_primary.csv", "w", newline="") as fh:
        cols = ["day", "symbol", "side", "signal", "band", "entry", "exit", "exit_kind", "gross", "net", "cost", "vol_rank", "vix_prev"]
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(primary)
    eq = np.cumprod(1 + daily_series(primary, sessions, w))
    with open(OUT / "equity_primary.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["day", "equity_account_view"])
        wr.writerows(zip(sessions, [round(float(v), 6) for v in eq]))
    write_markdown(results)
    print(f"wrote {OUT.relative_to(ROOT)}/results.json, trades_primary.csv, equity_primary.csv, RESULTS.md")


def fmt(v, kind="f", nd=2):
    if v is None:
        return "-"
    if kind == "%":
        return f"{v * 100:.{nd}f}%"
    return f"{v:.{nd}f}"


def write_markdown(r: dict):
    L = [f"# Rest-of-day momentum backtest: results", "",
         f"Generated {r['generated']} by `scripts/research_rod_momentum_backtest.py`. Sessions {r['window'][0]} to "
         f"{r['window'][1]} ({r['sessions']}), universe {', '.join(r['universe'])}, Alpaca SIP 1-minute bars (split-adjusted). "
         f"Data fingerprint `{r['data_fingerprint'][:16]}`.", "",
         "Account view: each position is 10% of equity (the account's max_position), at most one per ETF. "
         "Per-trade figures are in basis points of notional. All figures are this script's own backtest, net of the costs listed.", "",
         "## Trials (every configuration evaluated)", "",
         "| Configuration | Trades | Win | Gross bps | t gross | Cost bps | Net bps | t net | Ann. ret | Sharpe | Max DD |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in r["trials"].items():
        t = s["trade"]
        L.append(f"| {name} | {t['trades']} | {fmt(t['win_rate'], '%', 1)} | {fmt(t['gross_bps'])} | {fmt(t['t_gross'])} | {fmt(t['cost_bps'])} | "
                 f"{fmt(t['net_bps'])} | {fmt(t['t_net'])} | {fmt(s['ann_return'], '%')} | {fmt(s['sharpe'])} | {fmt(s['max_drawdown'], '%')} |")
    p = r["trials"]["primary: rod k=0.5, exit 15:55"]
    b = p["bootstrap"]
    L += ["", "## Primary specification", "",
          f"Block bootstrap ({BOOT_REPS} reps, {BOOT_BLOCK}-day blocks): Sharpe 95% CI {fmt(b['sharpe_ci95'][0])} to {fmt(b['sharpe_ci95'][1])}; "
          f"annual return 95% CI {fmt(b['ann_return_ci95'][0], '%')} to {fmt(b['ann_return_ci95'][1], '%')}; P(Sharpe <= 0) = {fmt(b['p_sharpe_le_0'], 'f', 3)}.", "",
          f"Daily-return t-stat {fmt(p['t_daily'])}; permutation p (random side, same days) = {fmt(r['permutation_p_primary'], 'f', 4)}.", ""]
    d = r["deflated_sharpe_primary"]
    L += [f"Deflated Sharpe (Bailey & Lopez de Prado 2014) over {d['trials']} trials: probability {fmt(d['deflated_sharpe_prob'], 'f', 3)} "
          f"(PSR vs 0: {fmt(d['psr_vs_zero'], 'f', 3)}; daily skew {fmt(d['skew'])}, kurtosis {fmt(d['kurtosis'])}).", "",
          "Falsification gates (Mesfin 2026 style): " + ", ".join(f"{k} = {v}" for k, v in r["falsification_gates"].items()), "",
          "### By year", "", "| Year | Trades | Win | Net bps | t (trade) | Ann. ret | Sharpe | Max DD |", "|---|---|---|---|---|---|---|---|"]
    for y, s in r["primary_by_year"].items():
        t = s["trade"]
        L.append(f"| {y} | {t['trades']} | {fmt(t['win_rate'], '%', 1)} | {fmt(t['net_bps'])} | {fmt(t['t_net'])} | {fmt(s['ann_return'], '%')} | {fmt(s['sharpe'])} | {fmt(s['max_drawdown'], '%')} |")
    L += ["", "### Walk-forward (k chosen on prior years by net Sharpe, applied to the next year)", "",
          "| Year | Chosen k | WF trades | WF net bps | WF Sharpe | Fixed k=0.5 net bps | Fixed Sharpe |", "|---|---|---|---|---|---|---|"]
    for f in r["walk_forward"]["folds"]:
        L.append(f"| {f['year']} | {f['chosen_k']} | {f['wf']['trade']['trades']} | {fmt(f['wf']['trade'].get('net_bps'))} | {fmt(f['wf']['sharpe'])} | "
                 f"{fmt(f['fixed_k0.5']['trade'].get('net_bps'))} | {fmt(f['fixed_k0.5']['sharpe'])} |")
    c, fx = r["walk_forward"]["combined_oos"], r["walk_forward"]["fixed_primary_same_years"]
    L += ["", f"Combined walk-forward OOS: {c['trade']['trades']} trades, net {fmt(c['trade']['net_bps'])} bps, Sharpe {fmt(c['sharpe'])}, "
          f"ann. return {fmt(c['ann_return'], '%')}. Fixed pre-registered k=0.5 over the same years: net {fmt(fx['trade']['net_bps'])} bps, Sharpe {fmt(fx['sharpe'])}.", ""]
    for title, key in (("By side", "primary_by_side"),):
        L += [f"### {title}", "", "| Side | Trades | Win | Net bps | t | Sharpe |", "|---|---|---|---|---|---|"]
        for k_, s in r[key].items():
            t = s["trade"]
            L.append(f"| {k_} | {t['trades']} | {fmt(t['win_rate'], '%', 1)} | {fmt(t['net_bps'])} | {fmt(t['t_net'])} | {fmt(s['sharpe'])} |")
        L.append("")
    for title, key in (("By symbol", "primary_by_symbol"), ("By prior-day VIX close", "primary_by_vix"),
                       ("By own vol_rank(20, 200)", "primary_by_vol_rank"), ("By exit", "primary_by_exit")):
        L += [f"### {title}", "", "| Bucket | Trades | Win | Gross bps | t gross | Net bps | t net |", "|---|---|---|---|---|---|---|"]
        for k_, t in r[key].items():
            L.append(f"| {k_} | {t['trades']} | {fmt(t['win_rate'], '%', 1)} | {fmt(t['gross_bps'])} | {fmt(t['t_gross'])} | {fmt(t['net_bps'])} | {fmt(t['t_net'])} |")
        L.append("")
    L += ["### Cost stress (slippage, spread and fees multiplied)", "", "| Costs | Net bps | t | Sharpe | Ann. ret |", "|---|---|---|---|---|"]
    for k_, s in r["cost_stress"].items():
        L.append(f"| {k_} | {fmt(s['trade']['net_bps'])} | {fmt(s['trade']['t_net'])} | {fmt(s['sharpe'])} | {fmt(s['ann_return'], '%')} |")
    v = r["v2_post_hoc"]
    L += ["", "### Post-hoc v2 (regime gate chosen after seeing these results; in-sample only)", "",
          "| Year | Trades | Net bps | t net | Sharpe |", "|---|---|---|---|---|"]
    for y, s in v["by_year"].items():
        L.append(f"| {y} | {s['trade']['trades']} | {fmt(s['trade']['net_bps'])} | {fmt(s['trade']['t_net'])} | {fmt(s['sharpe'])} |")
    m_, c2 = v["measured_costs"], v["cost_2x"]
    L += ["", f"v2 by side: " + "; ".join(f"{k_} {t['trades']} trades, gross {fmt(t['gross_bps'])} bps (t {fmt(t['t_gross'])}), net {fmt(t['net_bps'])} bps" for k_, t in v["by_side"].items()) + ".",
          f"v2 with measured spreads and no slippage: net {fmt(m_['trade']['net_bps'])} bps (t {fmt(m_['trade']['t_net'])}), Sharpe {fmt(m_['sharpe'])}. "
          f"At 2x pre-registered costs: net {fmt(c2['trade']['net_bps'])} bps, Sharpe {fmt(c2['sharpe'])}. "
          f"Bootstrap Sharpe 95% CI {fmt(v['bootstrap']['sharpe_ci95'][0])} to {fmt(v['bootstrap']['sharpe_ci95'][1])}.", ""]
    L += ["", "### Cost sensitivity (same trades, other cost assumptions)", "", "| Costs | Gross bps | t gross | Net bps | t net | Sharpe |", "|---|---|---|---|---|---|"]
    for k_, s in r["cost_sensitivity"].items():
        t = s["trade"]
        L.append(f"| {k_} | {fmt(t['gross_bps'])} | {fmt(t['t_gross'])} | {fmt(t['net_bps'])} | {fmt(t['t_net'])} | {fmt(s['sharpe'])} |")
    dia = r["robustness_dia"]
    L += ["", f"Out-of-universe check, DIA with the same rules: {dia['trades']} trades, net {fmt(dia.get('net_bps'))} bps, t {fmt(dia.get('t_net'))}.", "",
          "Costs: " + json.dumps(r["costs"]), ""]
    (OUT / "RESULTS.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
