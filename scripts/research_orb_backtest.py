#!/usr/bin/env python3
"""
research_orb_backtest.py — Rerunnable evidence backtest for the enhanced
"Stocks in Play" 5-minute opening-range breakout (orb_stocks_in_play_pro_*).

Paper: Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy For
The U.S. Equity Market" (SSRN 4729284, SFI RP 24-98).

Design notes / assumptions (read before trusting numbers):
- Data: Alpaca Market Data v2 REST. Keys are read from secrets/broker.env (fallback
  secrets/paper.env); key values are never printed. Feed = ALPACA_DATA_FEED.
  DATA host is always https://data.alpaca.markets (the paper base URL is for orders).
- Universe: config/universe.yaml lists only 8 mega-caps, which is too narrow for a
  "Stocks in Play" study, so we use a curated liquid, news-prone US-equity basket
  plus SPY (the regime reference). Edit --universe to override.
- Filters at the day's prior close: price >= $5, ADV14 >= $20M, ATR14 >= $0.50.
- Opening range = first 5 one-minute bars (09:30-09:35 ET). Direction = first candle
  close vs day open. RVOL = cumulative volume at the entry bar vs the mean of the
  prior 14 sessions' cumulative volume at the same minute-of-day.
- Entry: a 1-min bar CROSSES the range high/low within 09:35-10:30 ET with
  rvol >= 2.0. The fill is modeled at the NEXT bar's OPEN (not the crossing-bar
  close) to avoid look-ahead. One position per symbol per day; max 3 entries per
  day across the whole book, chosen by earliest crossing time.
- Exit: stop = 0.10 * ATR14 (paper-faithful), target = 10R (almost never hit; it
  is a bracket placeholder). True exit = flatten at the 15:50 ET bar. Intrabar
  stop/target are checked conservatively (stop before target on a conflicting bar).
- Sizing: $100k account, risk 0.5% = $500 risk per trade; shares = $500 / (0.10*ATR14).
- Costs: Alpaca equity commissions are $0. Model entry+exit cost = half-spread +
  slippage, per side. DEFAULTS half_spread_bps=2.0, slippage_bps=3.0 per side =>
  10 bps round turn for liquid Stocks-in-Play (tight spreads, mild impact). We also
  report 2x and 3x cost stresses. Justification: large-cap high-RVOL names trade
  ~1-2 cent effective spreads on a ~$30-100 print (roughly 2-4 bps half-spread),
  plus ~3 bps slippage crossing the spread; this is a midpoint assumption, not a
  measured fill model.
- The strategy has no fitted params beyond the paper's thresholds; volrank_min and
  candle_atr_min default to OFF (0.0), so this backtest is the base enhanced rule.

Outputs: reports/backtest_evidence.json, reports/backtest_trades.csv,
reports/backtest_evidence.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "reports" / ".orb_cache"
REPORTS = ROOT / "reports"
ET = __import__("zoneinfo").ZoneInfo("America/New_York")

# Curated liquid, news-prone basket (Stocks-in-Play proxies). SPY added separately.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO",
    "NFLX", "ORCL", "QCOM", "ADBE", "CRM", "INTC", "MU", "PLTR", "COIN",
    "MRNA", "AFRM", "UBER", "RIVN", "F", "PYPL", "DIS", "BABA", "NIO",
    "SOFI", "RBLX", "DASH", "ABNB", "HOOD", "SHOP", "SMCI", "LC",
]
REGIME_SYMBOL = "SPY"


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #
def load_env(paths):
    env = {}
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


# --------------------------------------------------------------------------- #
# Alpaca REST (stdlib urllib; retries; on-disk cache)
# --------------------------------------------------------------------------- #
class Alpaca:
    def __init__(self, feed: str):
        env = load_env([ROOT / "secrets/broker.env", ROOT / "secrets/paper.env"])
        self.key = env.get("ALPACA_KEY_ID", "")
        self.sec = env.get("ALPACA_SECRET_KEY", "")
        self.feed = feed or env.get("ALPACA_DATA_FEED", "iex")
        self.base = "https://data.alpaca.markets"
        if not self.key or not self.sec:
            raise SystemExit("ALPACA_KEY_ID / ALPACA_SECRET_KEY not found in secrets/")
        CACHE.mkdir(parents=True, exist_ok=True)

    def _get(self, path: str, params: dict, cache_key: str):
        cf = CACHE / (cache_key + ".json")
        if cf.exists():
            return json.loads(cf.read_text())
        q = urllib.parse.urlencode(params)
        url = f"{self.base}{path}?{q}"
        last = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    url, headers={"APCA-API-KEY-ID": self.key,
                                  "APCA-API-SECRET-KEY": self.sec})
                with urllib.request.urlopen(req, timeout=40) as r:
                    data = json.loads(r.read())
                cf.write_text(json.dumps(data))
                time.sleep(0.03)
                return data
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET {path} failed: {last}")

    def bars(self, symbol: str, timeframe: str, start: str, end: str):
        """All bars for [start,end), paginated by 10k using next_page_token."""
        out, page_token = [], None
        while True:
            params = {"timeframe": timeframe, "start": start, "end": end,
                      "limit": 10000, "feed": self.feed}
            if page_token:
                params["page_token"] = page_token
            ck = f"{symbol}_{timeframe}_{start}_{end}_{page_token or 'p0'}".replace("/", "-")
            data = self._get(f"/v2/stocks/{symbol}/bars", params, ck)
            out.extend(data.get("bars", []))
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return out


# --------------------------------------------------------------------------- #
# bar parsing -> per-date minute arrays (regular session only)
# --------------------------------------------------------------------------- #
def to_et(iso: str):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


def parse_intraday(bars):
    """bars: Alpaca 1Min list. Return {date_str: [tuple(minute_idx, o,h,l,c,v), ...]}
    keeping only regular-session minutes 0..389 (09:30..15:59 ET)."""
    by_day = defaultdict(list)
    for b in bars:
        dt = to_et(b["t"])
        idx = (dt.hour * 60 + dt.minute) - 570  # 0 at 09:30
        if idx < 0 or idx > 389:
            continue  # extended hours / closing print
        by_day[dt.strftime("%Y-%m-%d")].append(
            (idx, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), int(b["v"])))
    for d in by_day:
        by_day[d].sort()
    return by_day


def parse_daily(bars):
    """Return sorted list of dicts (date_str, o,h,l,c,v)."""
    out = []
    for b in bars:
        dt = to_et(b["t"])
        out.append({"date": dt.strftime("%Y-%m-%d"),
                    "o": float(b["o"]), "h": float(b["h"]),
                    "l": float(b["l"]), "c": float(b["c"]), "v": int(b["v"])})
    out.sort(key=lambda x: x["date"])
    return out


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #
def true_ranges(days):
    tr = []
    for i in range(1, len(days)):
        h, l, pc = days[i]["h"], days[i]["l"], days[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return tr


def atr14(days_up_to, i):
    """Mean TR over the 14 sessions ending at index i (i = today's index-1 = prior)."""
    if i < 15:
        return None
    trs = true_ranges(days_up_to[: i + 1])[-14:]
    return sum(trs) / 14 if len(trs) == 14 else None


def adv14(days_up_to, i):
    if i < 14:
        return None
    window = days_up_to[i - 14:i]
    return sum(d["c"] * d["v"] for d in window) / 14


def stdev(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5  # sample, matches statistics.stdev


def spy_regime(daily):
    """Per-date dict: vol_rank(20,60) (0-1), up_day bool. Uses completed sessions."""
    closes = [d["c"] for d in daily]
    dates = [d["date"] for d in daily]
    rets = [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]
    # vol_d(20) ending at session k (uses closes k-20..k)
    def vol_at(end):  # end = index into closes, exclusive
        if end - 21 < 0:
            return None
        w = rets[end - 20:end]
        return stdev(w)
    out = {}
    for k in range(len(daily)):
        # value known at close of session k; applied to trading day k+1
        now = vol_at(k + 1)
        if now is None:
            out[dates[k + 1] if k + 1 < len(daily) else dates[k]] = {"vr": None, "up": None}
            continue
        past = [vol_at(k + 1 - off) for off in range(1, 61)]
        past = [p for p in past if p is not None]
        vr = sum(p <= now for p in past) / len(past) if past else None
        nxt = dates[k + 1] if k + 1 < len(daily) else None
        if nxt:
            out[nxt] = {"vr": vr, "up": closes[k + 1] > closes[k]}
    return out


# --------------------------------------------------------------------------- #
# simulation for one day, one symbol
# --------------------------------------------------------------------------- #
OPEN_MIN, OR_MIN = 0, 5
ENTRY_LAST = 60      # 10:30 ET
FLATTEN = 380        # 15:50 ET
RVOL_N, RVOL_MIN = 14, 2.0
STOP_ATR_MULT, TARGET_R = 0.10, 10.0


def day_candidates(date, sym, bars, daily, prior_cumvol, atr, adv, prior_close):
    """Return list of candidate entry dicts for this symbol-day (0 or 1)."""
    if atr is None or adv is None or atr <= 0:
        return []
    if prior_close < 5 or adv < 20_000_000:
        return []
    if len(bars) < OR_MIN + 1:
        return []
    idx = [b[0] for b in bars]
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    c = [b[4] for b in bars]
    v = [b[5] for b in bars]
    pos = {m: j for j, m in enumerate(idx)}
    day_open = o[0]
    or_bars = [j for j in range(len(bars)) if idx[j] <= OR_MIN]
    if len(or_bars) < OR_MIN:
        return []  # opening range incomplete (missing early bars)
    range_high = max(h[j] for j in or_bars)
    range_low = min(l[j] for j in or_bars)
    first_close = c[max(or_bars)]
    # cumulative volume today by minute
    cum = {}
    run = 0
    for j in range(len(bars)):
        run += v[j]
        cum[idx[j]] = run
    # RVOL baseline: mean over prior 14 sessions of cum volume at minute m
    def rvol_at(m):
        vals = [cv.get(m) for cv in prior_cumvol if cv.get(m)]
        if not vals:
            return None
        base = sum(vals) / len(vals)
        if base <= 0:
            return None
        return cum.get(m, 0) / base

    # scan for crossing within entry window
    for j in range(len(bars)):
        m = idx[j]
        if m < OR_MIN or m > ENTRY_LAST:
            continue
        if j == 0:
            continue
        prev_c = c[j - 1]
        # long
        if first_close > day_open and prev_c <= range_high and c[j] > range_high:
            rv = rvol_at(m)
            if rv is not None and rv >= RVOL_MIN:
                if j + 1 < len(bars):
                    return [mk_trade(date, sym, "long", bars, j + 1, o[j + 1],
                                     atr, range_high, rv)]
        # short
        if first_close < day_open and prev_c >= range_low and c[j] < range_low:
            rv = rvol_at(m)
            if rv is not None and rv >= RVOL_MIN:
                if j + 1 < len(bars):
                    return [mk_trade(date, sym, "short", bars, j + 1, o[j + 1],
                                     atr, range_low, rv)]
    return []


def mk_trade(date, sym, side, bars, entry_j, entry_px, atr, level, rvol):
    risk = STOP_ATR_MULT * atr
    if side == "long":
        stop = entry_px - risk
        target = entry_px + TARGET_R * risk
    else:
        stop = entry_px + risk
        target = entry_px - TARGET_R * risk
    exit_px, exit_reason, exit_j = None, None, None
    for j in range(entry_j, len(bars)):
        m, bj_h, bj_l = bars[j][0], bars[j][2], bars[j][3]
        if m >= FLATTEN:
            exit_px, exit_reason, exit_j = bars[j][4], "flatten", j
            break
        if side == "long":
            if bj_l <= stop:
                exit_px, exit_reason, exit_j = stop, "stop", j
                break
            if bj_h >= target:
                exit_px, exit_reason, exit_j = target, "target", j
                break
        else:
            if bj_h >= stop:
                exit_px, exit_reason, exit_j = stop, "stop", j
                break
            if bj_l <= target:
                exit_px, exit_reason, exit_j = target, "target", j
                break
    if exit_px is None:  # ran to end of data
        exit_px, exit_reason = bars[-1][4], "eod"
    return {"date": date, "symbol": sym, "side": side, "rvol": round(rvol, 2),
            "entry": round(entry_px, 4), "exit": round(exit_px, 4),
            "stop": round(stop, 4), "target": round(target, 4),
            "exit_reason": exit_reason, "atr14": round(atr, 4),
            "entry_min": bars[entry_j][0]}


# --------------------------------------------------------------------------- #
# stats helpers
# --------------------------------------------------------------------------- #
def sharpe_ann(rets):
    n = len(rets)
    if n < 2:
        return 0.0
    mu = sum(rets) / n
    sd = (sum((r - mu) ** 2 for r in rets) / (n - 1)) ** 0.5
    return 0.0 if sd == 0 else mu / sd * (252 ** 0.5)


def max_drawdown(curve):
    peak, mdd = -1e18, 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x - peak)
    return mdd


def block_bootstrap(rets, n_boot=1000, block=10, seed=42):
    import random
    rng = random.Random(seed)
    n = len(rets)
    if n < block:
        return {"mean_lo": None, "mean_hi": None, "sharpe_lo": None, "sharpe_hi": None}
    means, sharpes = [], []
    for _ in range(n_boot):
        samp = []
        while len(samp) < n:
            s = rng.randrange(0, n - block + 1)
            samp.extend(rets[s:s + block])
        samp = samp[:n]
        mu = sum(samp) / n
        sd = (sum((x - mu) ** 2 for x in samp) / (n - 1)) ** 0.5
        means.append(mu)
        sharpes.append(0.0 if sd == 0 else mu / sd * (252 ** 0.5))
    means.sort(); sharpes.sort()
    p = lambda a: (a[int(0.025 * len(a))], a[int(0.975 * len(a)) - 1])
    ml, mh = p(means); sl, sh = p(sharpes)
    return {"mean_lo": ml, "mean_hi": mh, "sharpe_lo": sl, "sharpe_hi": sh}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--universe", default=",".join(DEFAULT_UNIVERSE))
    ap.add_argument("--half-spread-bps", type=float, default=2.0)
    ap.add_argument("--slippage-bps", type=float, default=3.0)
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--risk-pct", type=float, default=0.005)
    ap.add_argument("--max-trades-per-day", type=int, default=3)
    args = ap.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    symbols = [s.strip() for s in args.universe.split(",") if s.strip()]
    if REGIME_SYMBOL not in symbols:
        symbols = symbols + [REGIME_SYMBOL]
    client = Alpaca(feed=os.environ.get("ALPACA_DATA_FEED", ""))

    print(f"[data] feed={client.feed} window {args.start}..{end} symbols={len(symbols)}", file=sys.stderr)

    # 1) daily bars (full history from 2023 for warmup)
    daily_by = {}
    for sym in symbols:
        bars = client.bars(sym, "1Day", "2023-01-01", end)
        daily_by[sym] = parse_daily(bars)
        print(f"[daily] {sym}: {len(daily_by[sym])} days "
              f"{daily_by[sym][0]['date'] if daily_by[sym] else '-'}.."
              f"{daily_by[sym][-1]['date'] if daily_by[sym] else '-'}", file=sys.stderr)
    spy = {d["date"]: d for d in daily_by[REGIME_SYMBOL]}
    regime = spy_regime(daily_by[REGIME_SYMBOL])

    # index daily by date
    daily_idx = {sym: {d["date"]: i for i, d in enumerate(daily_by[sym])}
                 for sym in symbols}

    # 2) intraday, streamed month by month
    trades = []
    # rolling per-symbol prior-14 cum-volume curves
    rolling_cum = {sym: deque(maxlen=RVOL_N) for sym in symbols}
    # set of all trading days from SPY calendar
    cal = [d["date"] for d in daily_by[REGIME_SYMBOL] if d["date"] >= args.start]

    # prefetch intraday month chunks and group by date per symbol
    months = []
    y, m = int(args.start[:4]), int(args.start[5:7])
    while f"{y:04d}-{m:02d}" <= end[:7]:
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1

    intraday_by_day = defaultdict(lambda: defaultdict(list))  # date -> sym -> bars

    def fetch_chunk(sym, mstart, mend):
        try:
            bars = client.bars(sym, "1Min", mstart, mend)
            return sym, parse_intraday(bars) if bars else {}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {sym} {mstart[:7]} failed: {e}", file=sys.stderr)
            return sym, {}

    jobs = [(sym, f"{y:04d}-{m:02d}-01",
             f"{(y+1) if m == 12 else y:04d}-{(1 if m == 12 else m+1):02d}-01")
            for (y, m) in months for sym in symbols]
    from concurrent.futures import ThreadPoolExecutor
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, by_day in ex.map(lambda j: fetch_chunk(*j), jobs):
            for d, bs in by_day.items():
                intraday_by_day[d][sym] = bs
            done += 1
            if done % 50 == 0:
                print(f"[intraday] {done}/{len(jobs)} chunks fetched", file=sys.stderr)

    # 3) simulate chronologically
    for date in cal:
        day_syms = intraday_by_day.get(date, {})
        cands = []
        for sym in symbols:
            if sym == REGIME_SYMBOL or sym not in day_syms:
                continue
            bars = day_syms[sym]
            di = daily_idx[sym].get(date)
            if di is None or di < 15:
                continue
            prior_close = daily_by[sym][di - 1]["c"]
            atr = atr14(daily_by[sym], di)
            adv = adv14(daily_by[sym], di)
            cs = day_candidates(date, sym, bars, daily_by[sym], rolling_cum[sym],
                                atr, adv, prior_close)
            cands.extend(cs)
        # earliest 3 by crossing time
        cands.sort(key=lambda t: (t["entry_min"], t["symbol"]))
        cands = cands[: args.max_trades_per_day]

        # sizing + costs per accepted trade
        for t in cands:
            atr = t["atr14"]
            risk_per_share = STOP_ATR_MULT * atr
            shares = (args.risk_pct * args.account) / risk_per_share
            entry, exitp = t["entry"], t["exit"]
            gross = shares * ((exitp - entry) if t["side"] == "long" else (entry - exitp))
            notional = shares * entry
            t["shares"] = round(shares, 2)
            t["gross"] = round(gross, 2)
            t["notional"] = round(notional, 0)
            t["regime_vr"] = regime.get(date, {}).get("vr")
            t["spy_up"] = regime.get(date, {}).get("up")
            trades.append(t)

        # append today's cum-volume curves for RVOL baseline
        for sym in symbols:
            if sym in day_syms:
                cum = {}
                run = 0
                for (mm, o, h, l, c, v) in day_syms[sym]:
                    run += v
                    cum[mm] = run
                rolling_cum[sym].append(cum)

    # 4) costs (1x, 2x, 3x stress)
    sides_bps = args.half_spread_bps + args.slippage_bps  # per side
    def net_of(t, mult):
        cost = t["notional"] * (2 * sides_bps) / 10000.0 * mult
        return t["gross"] - cost
    for mult in (1, 2, 3):
        for t in trades:
            t[f"net_{mult}x"] = round(net_of(t, mult), 2)

    # daily P&L (1x costs) over the FULL trading calendar (0 on no-trade days)
    by_day_pnl = defaultdict(float)
    for t in trades:
        by_day_pnl[t["date"]] += t["net_1x"]
    trade_days = [d for d in cal if d in by_day_pnl]
    dates_sorted = list(cal)  # every trading day in window; no-trade days = 0
    daily_rets = [by_day_pnl.get(d, 0.0) / args.account for d in dates_sorted]
    cum = []
    run = 0.0
    for r in daily_rets:
        run += r
        cum.append(run)

    def group_stats(pred):
        sub = [t for t in trades if pred(t)]
        if not sub:
            return {"n": 0}
        wins = [t for t in sub if t["net_1x"] > 0]
        net_dollars = sum(t["net_1x"] for t in sub)
        gross_dollars = sum(t["gross"] for t in sub)
        exp_bps = net_dollars / sum(t["notional"] for t in sub) * 10000
        return {
            "n": len(sub),
            "win_rate": round(len(wins) / len(sub), 4),
            "net_$": round(net_dollars, 2),
            "gross_$": round(gross_dollars, 2),
            "expectancy_$/trade": round(net_dollars / len(sub), 2),
            "expectancy_bps_of_notional": round(exp_bps, 2),
            "avg_rvol": round(sum(t["rvol"] for t in sub) / len(sub), 2),
        }

    evidence = {
        "strategy": "orb_stocks_in_play_pro (enhanced Stocks-in-Play ORB)",
        "source": "Zarattini, Barbon & Aziz 2024, SSRN 4729284 / SFI RP 24-98",
        "data": {"provider": "alpaca_v2", "feed": client.feed,
                 "window": [args.start, end],
                 "trading_days": len(dates_sorted),
                 "symbols": [s for s in symbols if s != REGIME_SYMBOL]},
        "params": {"open_range_minutes": OR_MIN, "entry_window": "09:35-10:30",
                   "entry_last_minute": ENTRY_LAST, "flatten": "15:50",
                   "rvol_min": RVOL_MIN, "stop_atr_mult": STOP_ATR_MULT,
                   "target_r": TARGET_R, "risk_pct": args.risk_pct,
                   "account_$": args.account, "max_trades_per_day": args.max_trades_per_day,
                   "volrank_min": 0.0, "candle_atr_min": 0.0},
        "costs": {"half_spread_bps_per_side": args.half_spread_bps,
                  "slippage_bps_per_side": args.slippage_bps,
                  "round_trip_bps_1x": 2 * sides_bps},
        "totals": {"trades": len(trades),
                   "longs": sum(1 for t in trades if t["side"] == "long"),
                   "shorts": sum(1 for t in trades if t["side"] == "short"),
                   "net_total_$": round(sum(t["net_1x"] for t in trades), 2),
                   "gross_total_$": round(sum(t["gross"] for t in trades), 2)},
        "by_year": {},
        "by_side": {},
        "by_regime": {},
        "by_spy_day": {},
        "daily_series": {
            "net_sharpe_ann": round(sharpe_ann(daily_rets), 3),
            "gross_sharpe_ann": 0.0,  # overwritten below from gross daily P&L
            "mean_daily_return": round(sum(daily_rets) / len(daily_rets), 6) if daily_rets else 0,
            "max_drawdown_$": round(max_drawdown([by_day_pnl[d] for d in dates_sorted]), 2),
            "t_stat": round(
                (sum(daily_rets) / len(daily_rets)) /
                (((sum((r - (sum(daily_rets)/len(daily_rets)))**2 for r in daily_rets)/(len(daily_rets)-1))**0.5) /
                 (len(daily_rets)**0.5)) if len(daily_rets) > 1 and daily_rets else 0, 3),
        },
        "bootstrap": block_bootstrap(daily_rets),
        "cost_stress": {
            f"{m}x": {"net_total_$": round(sum(t[f'net_{m}x'] for t in trades), 2),
                      "round_trip_bps": round(2 * sides_bps * m, 1)}
            for m in (1, 2, 3)},
    }
    # gross sharpe recomputed cleanly
    gross_day = defaultdict(float)
    for t in trades:
        gross_day[t["date"]] += t["gross"]
    gross_rets = [gross_day[d] / args.account for d in dates_sorted]
    evidence["daily_series"]["gross_sharpe_ann"] = round(sharpe_ann(gross_rets), 3)

    for yr in sorted({t["date"][:4] for t in trades}):
        evidence["by_year"][yr] = group_stats(lambda t, yr=yr: t["date"].startswith(yr))
    for side in ("long", "short"):
        evidence["by_side"][side] = group_stats(lambda t, s=side: t["side"] == s)
    evidence["by_regime"]["high_vol_spy"] = group_stats(
        lambda t: t["regime_vr"] is not None and t["regime_vr"] >= 0.5)
    evidence["by_regime"]["low_vol_spy"] = group_stats(
        lambda t: t["regime_vr"] is not None and t["regime_vr"] < 0.5)
    evidence["by_spy_day"]["spy_up"] = group_stats(lambda t: t["spy_up"] is True)
    evidence["by_spy_day"]["spy_down"] = group_stats(lambda t: t["spy_up"] is False)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "backtest_evidence.json").write_text(json.dumps(evidence, indent=2))

    # trades CSV
    import csv
    cols = ["date", "symbol", "side", "rvol", "entry", "exit", "exit_reason",
            "shares", "atr14", "gross", "notional", "net_1x", "net_2x", "net_3x",
            "regime_vr", "spy_up"]
    with (REPORTS / "backtest_trades.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in trades:
            w.writerow({k: t.get(k) for k in cols})

    # markdown summary
    md = []
    md.append("# ORB Stocks-in-Play Pro — backtest evidence\n")
    md.append(f"- Source: {evidence['source']}")
    md.append(f"- Data: Alpaca v2 feed `{client.feed}`, window **{args.start} → {end}**, "
              f"{len(dates_sorted)} trading days, {len(evidence['data']['symbols'])} symbols.")
    md.append(f"- Trades: **{len(trades)}** "
              f"({evidence['totals']['longs']} long / {evidence['totals']['shorts']} short).")
    md.append(f"- Sizing: ${args.account:,.0f} account, risk {args.risk_pct:.2%}/trade, "
              f"stop {STOP_ATR_MULT}·ATR14, target {TARGET_R}R (placeholder), flatten 15:50.")
    md.append(f"- Costs: {2*sides_bps:.0f} bps round-turn (half-spread {args.half_spread_bps} + "
              f"slippage {args.slippage_bps}/side).")
    md.append(f"- Regime gate & first-candle filter: **OFF** (volrank_min=0, candle_atr_min=0).\n")
    md.append("## Totals (1x costs)")
    md.append(f"- Net P&L: **${evidence['totals']['net_total_$']:,.2f}**  "
              f"(gross ${evidence['totals']['gross_total_$']:,.2f})")
    ds = evidence["daily_series"]
    bb = evidence["bootstrap"]
    md.append(f"- Net annualized Sharpe: **{ds['net_sharpe_ann']}** "
              f"(gross {ds['gross_sharpe_ann']}); t-stat {ds['t_stat']}; "
              f"max drawdown ${abs(ds['max_drawdown_$']):,.2f}.")
    if bb["mean_lo"] is not None:
        md.append(f"- Block bootstrap (1000×, 10-day blocks) 95% CI — "
                  f"mean daily return [{bb['mean_lo']:.5f}, {bb['mean_hi']:.5f}], "
                  f"Sharpe [{bb['sharpe_lo']:.2f}, {bb['sharpe_hi']:.2f}].\n")
    def tbl(title, d):
        md.append(f"## {title}\n")
        md.append("| group | n | win% | net$ | exp$/trade | exp bps | avg RVOL |")
        md.append("|---|---|---|---|---|---|---|")
        for k, v in d.items():
            if v.get("n"):
                md.append(f"| {k} | {v['n']} | {v['win_rate']:.1%} | "
                          f"{v['net_$']:,.0f} | {v['expectancy_$/trade']:,.1f} | "
                          f"{v['expectancy_bps_of_notional']:.1f} | {v['avg_rvol']:.1f} |")
        md.append("")
    tbl("By year", evidence["by_year"])
    tbl("By side", evidence["by_side"])
    tbl("By SPY vol regime (vol_rank 20/60)", evidence["by_regime"])
    tbl("By SPY daily direction", evidence["by_spy_day"])
    md.append("## Cost stress (net total $)")
    md.append("| multiplier | round-turn bps | net $ |")
    md.append("|---|---|---|")
    for m, v in evidence["cost_stress"].items():
        md.append(f"| {m} | {v['round_trip_bps']:.0f} | {v['net_total_$']:,.0f} |")
    md.append("\n_Headline figures from the paper are the authors' own backtests; these are "
              "this mechanical version on the curated basket._")
    (REPORTS / "backtest_evidence.md").write_text("\n".join(md))

    print(json.dumps({"trades": len(trades),
                      "net_total": evidence['totals']['net_total_$'],
                      "net_sharpe": ds['net_sharpe_ann'],
                      "file": str(REPORTS / "backtest_evidence.json")}, indent=2))


if __name__ == "__main__":
    main()
