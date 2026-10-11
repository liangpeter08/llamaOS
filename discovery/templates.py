"""Offline hypothesis generator (templates@v1) and the DSL builders shared with mutations.

Each template is a hypothesis family with a stated mechanism and a falsifier, written as a complete
DSL file for one ticker. Initial values come from the focus profile where it has evidence; ranges stay
wide so later tuning has room. Used when no model key is set, and as a baseline set next to Claude.
"""
from __future__ import annotations

import re
from datetime import time

import yaml

from common.config import RiskConfig

ORIGIN = "templates@v1"
PROMPT = "discovery@v1"


def pct(x: float) -> str:
    return f"{round(x * 100, 4):g}%"


def dump(d: dict) -> str:
    return yaml.safe_dump(d, sort_keys=False, width=1000, allow_unicode=True)


def sid_for(ticker: str, family: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", f"disc_{ticker.lower()}_{family}")[:63]


def _hhmm(t: time) -> str:
    return f"{t:%H:%M}"


def intraday_file(ticker: str, family: str, text: str, assumptions: list[str], risk: RiskConfig, params: dict,
                  when_all: list[str], refs: list[str] = (), vars_: dict | None = None, filters: list[str] = (),
                  start: time = time(9, 45), origin: str = ORIGIN) -> str:
    lo, hi = max(start, risk.entries[0]), min(time(14, 30), risk.entries[1])
    d = {
        "dsl": "0.1", "id": sid_for(ticker, family), "version": 1,
        "source": {"text": text, "compiled_by": origin, "prompt": PROMPT},
        "assumptions": [f"single-ticker discovery candidate for {ticker} (family: {family})", *assumptions],
        "suggestions": [],
        **({"references": list(refs)} if refs else {}),
        "universe": {"symbols": [ticker], "filters": ["adv(20) >= min_adv", *filters]},
        "timeframe": "1min",
        "params": {"min_adv": {"value": 5_000_000, "range": [1_000_000, 10_000_000_000]}, **params,
                   "atr_mult": {"value": 1.5, "range": [0.5, 4.0]}, "target_r": {"value": 2.0, "range": [1.0, 4.0]}},
        **({"vars": vars_} if vars_ else {}),
        "entry": {"side": "long", "window": f"{_hhmm(lo)}-{_hhmm(hi)}", "when_all": when_all},
        "sizing": {"risk_per_trade": pct(min(0.0025, risk.max_risk))},
        "exit": {"stop": "entry_price - atr_mult * atr(14)", "target": "entry_price + target_r * risk_per_share",
                 "flatten_at": _hhmm(min(time(15, 50), risk.flatten_by))},
        "execution": {"mode": "manual"},
        "limits": {"max_trades_per_day": min(2, risk.max_trades_per_day)},
    }
    return dump(d)


def daily_file(ticker: str, family: str, text: str, assumptions: list[str], risk: RiskConfig, params: dict,
               rules: list[dict], reentry: dict | None = None, refs: list[str] = (), origin: str = ORIGIN) -> str:
    pr = risk.portfolio
    max_holding = 1.5
    capital = min(pr.max_allocation, pr.max_position / max_holding)
    capital = int(capital * 100) / 100
    d = {
        "dsl": "0.1", "kind": "portfolio", "id": sid_for(ticker, family), "version": 1,
        "source": {"text": text, "compiled_by": origin, "prompt": PROMPT},
        "assumptions": [f"single-ticker discovery candidate for {ticker} (family: {family})",
                        f"capital {pct(capital)} of equity x max_holding {pct(max_holding)} fits the per-symbol cap {pct(pr.max_position)}",
                        *assumptions],
        "suggestions": [],
        **({"references": list(refs)} if refs else {}),
        "holdings": {"capital": pct(capital), "max_holding": pct(max_holding),
                     "groups": {"focus": {"weight": "100%", "symbols": [ticker]}}},
        "schedule": {"hold_sessions": pr.max_hold_sessions, "decide_at": _hhmm(pr.decide_at)},
        "params": params,
        "rules": rules,
        **({"reentry": reentry} if reentry else {}),
        "execution": {"mode": "manual"},
    }
    return dump(d)


def _step():
    return {"rebuild_step": {"value": "50%", "range": ["10%", "100%"]}}


def hypotheses(ticker: str, risk: RiskConfig, peers: list[str], prof: dict, kinds: tuple[str, ...]) -> list[dict]:
    """The initial population: every family whose data exists."""
    out = []
    daily_p, intra_p = prof.get("daily") or {}, prof.get("intraday") or {}
    peer = peers[0] if peers else None
    if "intraday" in kinds:
        big_gap = max(0.003, min(0.05, (intra_p.get("big_gap_threshold") or 0.01)))
        H = lambda fam, title, mech, fals, **kw: out.append({  # noqa: E731
            "kind": "intraday", "family": fam, "title": title, "mechanism": mech, "falsify": fals, "origin": ORIGIN,
            "yaml": intraday_file(ticker, fam, f"{title}. {mech}", [mech], risk, **kw)})
        H("orb_trend", f"{ticker} opening-range breakout in the direction of its daily trend",
          "Breakouts above the opening range follow through more often when the multi-day trend already points up.",
          "Breakout trades taken in an up-trend lose money after costs, or do no better than those in a down-trend.",
          params={"or_minutes": {"value": 15, "range": [5, 30]}, "trend_n": {"value": 20, "range": [5, 60]},
                  "trend_min": {"value": "0%", "range": ["-10%", "10%"]}},
          when_all=["crosses_above(close, or_high(or_minutes))", "ret_d(trend_n) >= trend_min"])
        H("gap_down_vwap_reclaim", f"{ticker} gap-down reversal once VWAP is reclaimed",
          "Large opening gaps down are partly overreaction; holding above VWAP shows buyers absorbing the selling.",
          "After big gap-downs, VWAP reclaims fail to make money after costs.",
          params={"min_gap": {"value": pct(big_gap), "range": ["0.3%", "5%"]}, "hold_bars": {"value": 3, "range": [2, 10]}},
          vars_={"above": "close > vwap"}, filters=["gap <= -min_gap"], when_all=["streak(above) >= hold_bars"])
        H("first30_continuation", f"{ticker} continuation after a strong first 30 minutes",
          "A strong first half hour reflects informed demand that keeps lifting the price; buying the VWAP cross joins it with a defined stop.",
          "Sessions with a strong open are no more profitable to buy than any other session.",
          params={"open_move_min": {"value": "0.5%", "range": ["0%", "3%"]}},
          when_all=["close / day_open - 1 >= open_move_min", "crosses_above(close, vwap)"], start=time(10, 0))
        H("calm_trend_pullback", f"{ticker} VWAP reclaim in a calm up-trend",
          "Pullbacks to VWAP in a persistent trend are bought by trend followers, but only while volatility is calm; in turbulent regimes the same signal is noise.",
          "The calm-regime filter does not improve results versus the unfiltered signal, or the filtered signal loses money.",
          params={"trend_n": {"value": 20, "range": [5, 60]}, "trend_min": {"value": "0%", "range": ["-10%", "10%"]},
                  "vol_cap": {"value": 0.66, "range": [0.3, 1.0]}},
          when_all=["ret_d(trend_n) >= trend_min", "vol_rank(10, 60) <= vol_cap", "crosses_above(close, vwap)"])
        if peer:
            H("market_lead", f"{ticker} catches up when {peer} leads the open",
              f"When {peer} is already up on the day and {ticker} hasn't moved, index flows tend to pull {ticker} along.",
              f"Entries after a {peer} lead do no better than unconditional VWAP crosses.",
              refs=[peer], params={"mkt_min": {"value": "0.3%", "range": ["0%", "2%"]}, "lag_max": {"value": "0.2%", "range": ["-1%", "2%"]}},
              when_all=[f"peer({peer}, close / day_open - 1) >= mkt_min", "close / day_open - 1 <= lag_max", "crosses_above(close, vwap)"],
              start=time(10, 0))
            H("relative_strength_breakout", f"{ticker} breakout while outperforming {peer} intraday",
              f"Stock-specific demand shows as strength relative to {peer}; breakouts with that demand behind them follow through.",
              f"Breakouts with positive relative strength vs {peer} do no better than breakouts without it.",
              refs=[peer], params={"rs_min": {"value": "0.5%", "range": ["0%", "3%"]}, "break_n": {"value": 20, "range": [5, 60]}},
              vars_={"hh": "highest(high, break_n)"},
              when_all=[f"(close / day_open - 1) - peer({peer}, close / day_open - 1) >= rs_min", "close > vwap", "crosses_above(close, hh[1])"],
              start=time(10, 0))
    if "daily" in kinds and risk.portfolio is not None:
        H = lambda fam, title, mech, fals, **kw: out.append({  # noqa: E731
            "kind": "daily", "family": fam, "title": title, "mechanism": mech, "falsify": fals, "origin": ORIGIN,
            "yaml": daily_file(ticker, fam, f"{title}. {mech}", [mech], risk, **kw)})
        H("trend_timing", f"Hold {ticker} only while its multi-day trend is up",
          "Trends persist (slow information diffusion); stepping aside in down-trends cuts the worst drawdowns.",
          f"Risk-adjusted return is no better than simply holding {ticker}.",
          params={"trend_n": {"value": 20, "range": [5, 60]}, "trend_floor": {"value": "0%", "range": ["-10%", "10%"]}, **_step()},
          rules=[{"when": "ret_d(trend_n) < trend_floor", "do": "exit_and_wait"}],
          reentry={"when": "ret_d(trend_n) >= trend_floor", "step": "rebuild_step"})
        H("stretch_scaling", f"Scale {ticker} against stretched moves",
          "Moves far from the 20-day average partly revert as liquidity providers lean against them: trim when stretched up with a gain, add when stretched down.",
          f"Trims and adds at stretched z-scores do not beat holding {ticker} on a risk-adjusted basis.",
          params={"z_n": {"value": 20, "range": [10, 60]}, "z_trim": {"value": 1.5, "range": [0.5, 3.0]},
                  "gain_min": {"value": "2%", "range": ["0%", "20%"]}, "z_add": {"value": -1.5, "range": [-3.0, -0.5]},
                  "trim_size": {"value": "25%", "range": ["5%", "100%"]}, "add_size": {"value": "25%", "range": ["5%", "50%"]}},
          rules=[{"when": "zscore_d(z_n) >= z_trim and gain >= gain_min", "do": {"trim": "trim_size"}},
                 {"when": "zscore_d(z_n) <= z_add", "do": {"add": "add_size"}}])
        H("vol_regime_exit", f"Step out of {ticker} in its own high-volatility regimes",
          "Volatility clusters, and high-volatility regimes carry worse risk-adjusted returns; sitting out the top of the volatility range keeps most of the return with less risk.",
          f"Exiting in high-volatility regimes lowers risk-adjusted return versus holding {ticker}.",
          params={"vol_n": {"value": 10, "range": [5, 30]}, "vol_hi": {"value": 0.8, "range": [0.5, 1.0]}, **_step()},
          rules=[{"when": "vol_rank(vol_n, 60) >= vol_hi", "do": "exit_and_wait"}],
          reentry={"when": "vol_rank(vol_n, 60) < vol_hi", "step": "rebuild_step"})
        if peer:
            H("market_risk_off", f"Exit {ticker} on a sharp {peer} down day, re-enter once {peer} recovers",
              f"Market-wide selloffs in {peer} are followed by more selling in high-beta names; waiting for {peer} to recover avoids the follow-through.",
              f"Exits after {peer} down days miss rebounds and lower risk-adjusted return versus holding.",
              refs=[peer], params={"mkt_drop": {"value": "-1.5%", "range": ["-5%", "-0.5%"]},
                                   "mkt_recover": {"value": "0%", "range": ["-2%", "5%"]}, **_step()},
              rules=[{"when": f"peer({peer}, day_change) <= mkt_drop", "do": "exit_and_wait"}],
              reentry={"when": f"peer({peer}, ret_d(3)) > mkt_recover", "step": "rebuild_step"})
            H("relative_strength_hold", f"Hold {ticker} while it outperforms {peer}",
              f"Relative strength vs {peer} persists over weeks (stock-specific momentum); exit when {ticker} falls behind.",
              f"Relative-strength exits vs {peer} do not beat holding {ticker}.",
              refs=[peer], params={"rs_n": {"value": 20, "range": [5, 60]}, "rs_cut": {"value": "-5%", "range": ["-20%", "0%"]},
                                   "rs_back": {"value": "0%", "range": ["-5%", "10%"]}, **_step()},
              rules=[{"when": f"ret_d(rs_n) - peer({peer}, ret_d(rs_n)) <= rs_cut", "do": "exit_and_wait"}],
              reentry={"when": f"ret_d(rs_n) - peer({peer}, ret_d(rs_n)) > rs_back", "step": "rebuild_step"})
    return out
