# Quantitative Day Trading Strategy: Research & Specification Report

**Date:** 2026-10-16
**Report version:** v1.0
**Classification:** Quantitative Research — Internal Use

---

## Executive Summary

This report documents a from-first-principles selection of an intraday equity day-trading strategy for deployment on the llamaOS paper-trading stack (US equities via Alpaca; no futures, no options, no leverage beyond account margin settings).

**What was surveyed.** Five strategy families were assessed against a verified literature dossier of seven named works: (A) Opening Range Breakout (ORB); (B) intraday momentum including gamma-hedging demand; (C) VWAP mean reversion versus trend; (D) generic OHLCV index/futures signals, including the 2026 MNQ falsification study; and (E) factor-zoo machine-learning intraday predictability. Each family was graded on five dimensions: statistical significance, transaction-cost survivability, regime dependency, sample size/robustness, and long/short symmetry.

**What was selected.** A long-biased, news-selected 5-minute Opening Range Breakout on "Stocks in Play" — the operational family of Zarattini, Barbon & Aziz (2024). It is the lowest-infrastructure, microstructure-supported choice that fits the existing stack: a deterministic 1-minute rule engine, broker-side brackets, an EOD flatten, and a news/universe pipeline already present in llamaOS. The machine-learning factor-zoo approach (Family E) scores highest on raw academic evidence but requires a 218-factor data and modeling pipeline that does not exist here; it is deferred to a separate project.

**Honest evidence verdict.** We must be explicit. Our own rerunnable backtest of the *mechanical* ORB rule on a fixed 35-name basket (1,046 trades, 2024-01-01 to 2026-10-16, 10 bps round-turn costs) nets only **+$590** on a $100k account, a net annualized Sharpe of **0.007**, with a block-bootstrap 95% Sharpe confidence interval of **[-1.34, +1.10]** that straddles zero. On our own evidence, the fixed-basket mechanical ORB **does not reliably overcome costs**. The deployment thesis rests on the *attention-selected universe* — names with fresh company news and relative volume (RVOL >= 2.0) — which the standalone script did not simulate because it trades a static basket. That selectivity is the entire economic bet, and it must be validated in the dashboard Backtesting workspace (holdout window, 2x/3x cost stress, bootstrap) **before** any paper trading.

**Deployment stance: paper-first.** The system ships the long side only (the short mirror showed negative expectancy on our own run). Default mode is record/paper; real-money endpoints are refused by the stack. No guaranteed-return language is used anywhere; all performance figures below are labeled by provenance — authors' own backtest, our own backtest, or literature expectation.

---

## Table of Contents

- Executive Summary ........................................................ 2
- 1. Literature Survey .................................................... 4
- 2. Strategy Selection & Justification ................................. 9
- 3. Fully Specified Trading System ..................................... 12
- 4. Expected Performance ............................................. 16
- 5. Validation Methodology ............................................. 19
- 6. Infrastructure & Feature Gap Analysis ............................ 21
- 7. Improvement Roadmap ............................................... 23
- 8. Deployment Guide (DEPLOY) ........................................ 24
- References ............................................................. 26
- Risk Disclaimer ........................................................ 28

---

## 1. Literature Survey

We separate three kinds of number throughout: **(a) authors' own backtest numbers** (as reported by the papers, not independently replicated), **(b) our own backtest numbers** (Section 4), and **(c) literature expectations** used only to set realistic ranges. No number below is fabricated; every traceable claim links to the verified dossier.

### 1.1 Citation verification ledger

The prompt supplied seven named works. Each was checked against an authoritative source before being used as evidence.

| # | Work as named in the prompt | Verdict | What it actually is |
|---|---|---|---|
| 1 | arXiv 2605.04004, "Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures" | Verified (minor title correction) | Mathias Mesfin, "Structural Limits of OHLCV-Based Intraday **Momentum** Signals in MNQ Futures: A Systematic Falsification Study," submitted 5 May 2026, v3 15 Sep 2026. Single-author working paper, **not peer-reviewed**. |
| 2 | "Vortex Capital" 2026 study on long/short ORB asymmetry | **Verified as a blog post, not a study** | Vortex Capital Group blog, 13 May 2026. Sample = last 20 trading days on 9 high-beta names. A 20-day desk observation, not peer-reviewed research. |
| 3 | "Vortex Capital" "Off-Radar Reversion Illusion" VWAP paper | **Verified as a blog post, not a paper** | Vortex Capital Group, 8 Jul 2026. ~85 sessions, 29 off-radar mid-caps + 8 mega-caps, measured NBBO. Direction of finding is a **falsification** of VWAP fades. |
| 4 | "Boller et al., Duke 2025" factor-based intraday predictability | Verified, **author-name correction** | The Duke intraday-factor paper is **Aleti, Bollerslev & Siggaard (2025), "Intraday Market Return Predictability Culled from the Factor Zoo," Management Science** (peer-reviewed). There is no author named "Boller" at Duke. |
| 5 | "Notre Dame paper on gamma hedging and intraday momentum" | Verified | Baltussen, Da, Lammers & Martens (2021), "Hedging demand and market intraday momentum," *Journal of Financial Economics* 142, 407-427. Zhi Da is at Notre Dame (Mendoza). Peer-reviewed. |
| 6 | Handelshögskolan (Stockholm School of Economics) ORB thesis across volatility states | **Partially verified — institution misattributed** | The peer-reviewed ORB paper is **Holmberg, Lönnbark & Lundström (2013), Finance Research Letters 10(1), 27-33**, and the authors are at **Umeå University**, not the Stockholm School of Economics. The asset is **US crude oil futures 1983-2011**, not equities. No SSE/HHS ORB thesis on volatility states was found. |
| 7 | "SPY intraday momentum paper" RePEc 2024 | Verified | Zarattini, Barbon & Aziz, SFI Research Paper RP 24-97 (working paper). The companion ORB paper analyzed here is SSRN 4729284 / SFI RP 24-98. |

Two further named items were searched and **not found** under the stated identity: "Boller" as a single Duke author (the real author is Bollerslev), and any SSE/Handelshögskolan ORB thesis (the closest peer-reviewed paper is Umeå, not SSE). These corrections are stated plainly so the report is never cited under the wrong institution or name.

### 1.2 Family A — Opening Range Breakout (ORB)

Five-dimension assessment:

1. **Statistical significance.** Mixed. Zarattini/Barbon/Aziz (working paper): top-20 stocks-in-play authors' own Sharpe **2.81**; cost-honest QQQ-only alpha **33% p.a. (p = 0.0025)**. Holmberg et al. (peer-reviewed, oil futures 1983-2011): bootstrap-significant gross returns. Mesfin (arXiv 2026): MNQ ORB long out-of-sample **t = 0.88** — fails.
2. **Transaction-cost survivability.** Weak. Zarattini uses commission only ($0.0035/share), no slippage or borrow; the selected "stocks in play" are precisely where slippage and hard-to-borrow fees are worst. Holmberg assumes zero costs. The cost-honest number is the QQQ-only Sharpe **1.12**, not 2.81.
3. **Regime dependency.** The high-vol 2020 and meme-stock 2021 years likely carry much of the authors' edge; the Vortex 20-day blog reports a recent long-ORB hit rate of only ~30.6%.
4. **Sample size / robustness.** Zarattini: 8 years, 1,795 QQQ trades. Holmberg: 28 years of oil futures. **No independent peer-reviewed replication of the 2.81 Sharpe exists.**
5. **Long/short symmetry.** The QQQ variant ran 51% long / 49% short (near-symmetric). Our own run (Section 4) is the asymmetry that drives the deploy-long-only decision.

Key numbers: QQQ ORB Sharpe **1.12**, alpha 33% p.a. (p=0.0025), win rate 24%, 0.13R/trade; top-20 stocks-in-play Sharpe **2.81** (authors' own, cost-light); MNQ ORB long OOS t = 0.88 (falsified).

### 1.3 Family B — Intraday momentum (first/last half-hour; gamma hedging)

1. **Statistical significance.** Strongest of any family. Gao, Han, Li & Zhou (2018, JFE): first-half-hour return predicts the last half-hour, **t = 4.08**, in-sample R² = 1.6%, out-of-sample R² = 1.4%. Baltussen et al. (2021, JFE): rest-of-day predicts the last 30 minutes across 60 futures, **t = 6.46** for the overnight+first-half-hour block. Heston, Korajczyk & Sadka (2010, JF): exact time-of-day return continuation persists ~40 trading days.
2. **Transaction-cost survivability.** Good. The Gao timing strategy trades once per day (only the last 30 minutes); a ~1-2 bps SPY round trip is small against a ~6.67% annualized gross. Futures costs are low for liquid index contracts.
3. **Regime dependency.** **Strong.** Gao: high-vol tercile R² = 3.3% versus low 0.6%; NBER recessions R² = 6.6% versus expansions 1.0%; financial-crisis joint R² = 6.9%. The edge is a volatility/uncertainty phenomenon.
4. **Sample size / robustness.** The most robust base: SPY 1993-2013 (21 years, 10 other ETFs) and 60 futures over 1974-2020 (~46 years).
5. **Long/short symmetry.** Symmetric by construction (time-series sign strategy); Baltussen shows last-hour continuation on both up and down days.

Key numbers: t = 4.08 (Gao), t = 6.46 (Baltussen); timing Sharpe **1.08** versus buy-and-hold 0.29; recession R² 6.6% versus expansion 1.0%.

### 1.4 Family C — VWAP reversion versus trend

1. **Statistical significance.** No peer-reviewed standalone VWAP-fade edge exists. The genuine microstructure literature supports short-term reversal at the open (bid-ask bounce), not a "fade 2-sigma from VWAP" rule.
2. **Transaction-cost survivability.** Actively falsified. The Vortex blog (#3): naive reversion to VWAP occurs on 72.6% of days by close, but the tradable rate (reach VWAP before a 1-sigma stop) is only **35.2%**. Gross expectancy +1.7 bps/trade; net of measured half-spread, **-24 bps/trade**.
3. **Regime dependency.** Perverse: the more stretched the price (1.5 sigma to 3 sigma), the *lower* the tradable hit rate (39% to 24%).
4. **Sample size / robustness.** Vortex: 29 mid-caps + 8 mega-caps, ~85 sessions, n = 833 fades. Short but microstructure-controlled; no academic replication.
5. **Long/short symmetry.** Symmetric: both stretched-above and stretched-below fades fail.

Key numbers: naive 72.6% versus tradable 35.2%; gross +1.7 bps, net **-24 bps/trade**; off-radar median half-spread 15.0 bps versus mega-cap 0.82 bps (18x). This family is **not traded**.

### 1.5 Family D — OHLCV index/futures signals (MNQ falsification)

1. **Statistical significance.** Mesfin (2026, arXiv 2605.04004): **14 OHLCV signal families on MNQ, none passed all five walk-forward criteria.** ORB long t = 0.88, VVG classifier reversal t = 1.26, gap-continuation short t = 1.46. Two independent positive controls (RTH Confluence t = 3.11; London Session Signal B t = 4.30) confirm the framework detects real edge when present.
2. **Transaction-cost survivability.** 11 of 14 families produce gross 0.07-1.50 MNQ points against a **2.0-point friction floor** (~$100/contract). The friction floor is the binding constraint.
3. **Regime dependency.** The near-misses fail on year-to-year direction inconsistency across 2023/2024/2025.
4. **Sample size / robustness.** 947 regular-hours days, 5-minute bars, 2021-2025. Single asset, single author, not peer-reviewed — but the walk-forward protocol is stricter than most published backtests.
5. **Long/short symmetry.** Gap-continuation short nearly passes (gross +16.53 points) while ORB long fails (t = 0.88), but the short-side sample is tiny (N = 22).

Critical caveat for our decision: MNQ is a futures contract with a futures friction floor; this says "generic OHLCV intraday signals on index futures don't survive realistic futures costs," **not** that the same signals fail on selective US equities.

### 1.6 Family E — Factor-based intraday predictability (ML on the factor zoo)

1. **Statistical significance.** Aleti, Bollerslev & Siggaard (2025, Management Science, peer-reviewed): out-of-sample 15-minute R² = **0.212%** (versus the Ross bound of 0.00777%); Diebold-Mariano t = -4.315 versus the historical-mean benchmark.
2. **Transaction-cost survivability.** Uses measured SPY/QQQ/IWM spreads (0.67-1.67 bps) and stress-tests shorting at 2% (8x actual). After-cost SPY strategy Sharpe **1.37**, Fama-French-6 alpha 20.83% p.a.
3. **Regime dependency.** Most abnormal returns accrue in high-economic-uncertainty periods, with tail-risk and liquidity factors dominant — consistent with Family B.
4. **Sample size / robustness.** 2004-2020, 15-minute bars, 218 factor portfolios, multiple ML models; the ensemble beats individual models with an explicit out-of-sample protocol.
5. **Long/short symmetry.** Time-series market timing (SPY long/flat), not a cross-sectional long-short.

Key numbers: OOS R² 0.212%; after-cost Sharpe **1.37**; FF6 alpha 20.83% p.a.; SPY intraday buy-and-hold Sharpe 0.09. This family ranks first on raw evidence but is **not deployable here** because it requires rebalancing every 15 minutes and a 218-factor data pipeline that llamaOS does not build.

### 1.7 Ranked evidence summary

On after-cost evidence for an Alpaca account trading US equities/ETFs:

1. **Family E** — factor-zoo ML intraday timing (strongest evidence; infrastructure not built).
2. **Family B** — first/last-half-hour momentum on SPY/QQQ (lower-infrastructure fallback; edge concentrated in high-vol regimes).
3. **Family A** — selective ORB on high-RVOL "stocks in play" / QQQ (chosen; cost-honest QQQ Sharpe 1.12; authors' 2.81 not replicated).
4. **Family D** — generic OHLCV index/futures signals (falsified on futures).
5. **Family C** — VWAP fade (no peer-reviewed edge; actively falsified).

---

## 2. Strategy Selection & Justification

### 2.1 The selected strategy

We deploy **a long-biased, news-selected 5-minute Opening Range Breakout on "Stocks in Play"**, following the operational family of Zarattini, Barbon & Aziz (2024, SSRN 4729284). The deployable files are `strategies/orb_stocks_in_play_pro_long.v1.yaml` (long) and its gated mirror `orb_stocks_in_play_pro_short.v1.yaml` (short; held off).

### 2.2 Economic and microstructure mechanism

The thesis is **attention-driven volume leading to short-horizon momentum continuation**:

- When a stock has fresh, company-specific news, it draws disproportionate retail and institutional attention at the open. The resulting order flow is one-directional and persistent for the first hour.
- **Gao et al. (2018)** show the opening price discovery carries information that predicts the close (t = 4.08), and **Baltussen et al. (2021)** attribute cross-session intraday momentum to options-market-maker gamma hedging and leveraged-ETF rebalancing — flow that must trade *with* the move.
- **Bogousslavsky (2021, JFE)** provides the structural anchor: intraday return predictability arises from slow-moving, infrequently-rebalancing capital, so the effect is a structural feature rather than a fleeting arbitrage. That is why Baltussen et al. find it persists across ~46 years and 60 instruments.

The operational bet is therefore not "ORB works on any stock" — our own basket test disproves that — but "ORB works on the subset of names where attention and relative volume are elevated." The news pipeline (`universe.from.news_today: true`) and the RVOL >= 2.0 gate are two attempts to select that subset.

### 2.3 Direct response to Mesfin (arXiv 2605.04004)

The single most important objection to deploying *any* OHLCV breakout is Mesfin's falsification. We respond point by point:

- **Cross-sectional attention selectivity versus single-instrument index OHLCV.** Mesfin tests 14 signal families on **MNQ**, one index futures contract, with no universe selection whatsoever. We instead take a **cross-sectional attention-selected** slice of the equity universe: names with fresh news and RVOL >= 2.0. The paper's own top-20 ranking by opening relative volume is the same idea. Selection — not the breakout shape — is the difference.
- **Equity spread versus futures friction floor.** Mesfin's binding constraint is a **2.0-point (~$100/contract) futures friction floor**; 11 of 14 families produce gross 0.07-1.50 points and cannot clear it. On liquid US equities via Alpaca, commission is **$0** and our modeled round-turn is **10 bps** (half-spread 2 bps + slippage 3 bps per side), a structurally smaller drag on a dollar move.
- **Why this may not be enough — honestly.** The MNQ result is a warning, not a comfort. Our own fixed-basket test (no news selection) nets **Sharpe 0.007 with a bootstrap CI over zero**, and at 2x and 3x costs the basket loses $155k and $311k respectively. The equity spread advantage helps, but it does not by itself manufacture an edge. The news-universe selectivity is unproven in our standalone script. We are deploying a *hypothesis*, not a demonstrated edge.

### 2.4 Why not the other families

- **Why not Family E (ML factor zoo)?** It has the best peer-reviewed after-cost Sharpe (1.37), but it needs a 218-factor characteristic-sorted portfolio pipeline, 15-minute rebalancing, and a multi-model training loop. That pipeline is **not built** in llamaOS; building it is a separate project (Section 7), not a v1 deploy.
- **Why not Family C (VWAP fade)?** It is actively falsified: net -24 bps/trade off-radar once measured spreads are charged. Not traded.
- **Why not Family B as the primary?** It is the fallback. Its edge is explicitly concentrated in high-vol/recession regimes (R² 6.6% vs 1.0%), and our own data (Section 4) unexpectedly shows the mechanical ORB edge in the *low*-SPY-vol bucket — a tension we flag rather than resolve.

### 2.5 Long/short asymmetry decision: deploy long only

Our own run split the rule symmetrically 524 long / 522 short. The long side netted **+$22,047 (+2.8 bps/trade)**; the short side netted **-$21,457 (-2.8 bps/trade)**. Shorting adds borrow cost, recall risk, and the well-known upward drift of equities, and it gave no compensation in our sample. We therefore deploy **long only**; the short YAML remains in the repo but is rejected by the factory risk controls while `allow_short` is off, which is the intended default.

---

## 3. Fully Specified Trading System

All times are US Eastern (ET). Parameters below are the live values in the deployable YAML; bold values are the v1 defaults.

### 3.1 Instrument universe

- **Universe:** news-selected US equities (`universe.from.news_today: true`) — only names with fresh company news since the prior close are candidates. The news pipeline is the selectivity mechanism; the standalone backtest used a fixed 35-name basket and therefore did **not** exercise this selection.
- **Liquidity/quality filters at the prior close:**
  - **price >= $5** (`min_price = 5`)
  - **14-day average daily dollar volume >= $20,000,000** (`min_adv = 20_000_000`)
  - **14-day daily ATR >= $0.50** (`min_atr = 0.5`)
- **Reference instrument:** **SPY** (`references: [SPY]`), used only as a regime gauge — never traded. Why SPY: it is the most liquid US equity proxy, tracks the broad market risk-on/risk-off state, and its realized-volatility percentile is a cheap, real-time regime read.

### 3.2 Entry rules

- **Opening range:** the first **5 minutes** of the session (09:30-09:35 ET), `or_minutes = 5`.
- **Candle direction:** the first 5-minute candle must close **above the day open** for a long (`close_at(or_minutes) > day_open`); the short mirror requires close below the open.
- **Crossing rule:** a 1-minute bar must **close beyond the 5-minute range high** (`crosses_above(close, range_high)`). The backtest models the fill at the **next bar's open** (not the crossing-bar close) to avoid look-ahead.
- **Relative volume:** **RVOL(14) >= 2.0** (`rvol_min = 2.0`). RVOL = cumulative volume at the entry minute versus the mean of the prior 14 sessions' cumulative volume at the same minute-of-day.
- **Entry window:** **09:35-10:30 ET** only. The edge is documented as concentrated in the opening hour and decays through the day; entries after 10:30 are cut off.
- **Two default-off experimental filters** (do not treat as validated):
  - `volrank_min = 0.0` → SPY regime gate **OFF** (see 3.6).
  - `candle_atr_min = 0.0` → first-candle magnitude filter **OFF**; at 0 the rule only requires an up candle. Raise to 0.1+ as an experiment, not a prior.

### 3.3 Exit rules

- **Stop:** **0.10 x the 14-day daily ATR** (`stop_atr = 0.10`), exactly the paper's own stop. Rationale: an opening-range breakout that immediately reverses by ~10% of a daily ATR has failed its premise; the tight stop keeps risk-per-trade bounded and is what makes the fixed 0.5% risk sizing meaningful.
- **Target:** **10R** (`target_r = 10`) — a deliberate **placeholder**. It almost never triggers intraday; it exists only because the broker bracket requires a profit leg. The true exit is the flatten below.
- **Time exit:** flatten all positions at **15:50 ET**, deliberately 5 minutes before the 15:55 risk `flatten_by`, to avoid the closing auction and end the day flat (no overnight gap risk).
- **Trailing stop:** **none in v1** — exits are stop, placeholder target, or 15:50 flatten. Proposed v2 trailing rule (not active): once a trade reaches +1R, move the stop to breakeven; after +2R, trail by 0.5 x ATR14. This is a research suggestion, not a specified rule.

### 3.4 Sizing and risk

- **Risk per trade:** **0.5%** of equity (`risk_per_trade: 0.5%`). On a $100,000 account that is $500 risk; shares = $500 / (0.10 x ATR14). This is deliberately half the paper's 1%-with-4x-leverage sizing.
- **Frequency cap:** **max 3 trades per day** (`max_trades_per_day: 3`).
- **One position per symbol** per day.
- **Daily loss handling:** the account Risk page enforces `max_daily_loss = 2%` (factory default), on breach of which the kill switch fires. **Stated gap:** the standalone research script does not simulate a per-strategy intraday daily-loss circuit breaker; on the live stack this is provided per strategy (strategy daily loss cap 10% intraday) plus the account-level 2% kill switch, but the standalone numbers here are computed per-trade and do not reflect that intra-day halt.

### 3.5 Regime filter

- The strategy references SPY and requires `peer(SPY, vol_rank(20,60)) >= volrank_min`. With **`volrank_min = 0.0`** the gate is **OFF** (SPY's volatility percentile is always >= 0).
- **Our own evidence cuts against a naive high-vol prior.** In our run, trades taken when SPY vol_rank was in the upper half (high_vol_spy) netted **-$68,254 (-10.4 bps/trade, win 14.5%)**, while trades in the low-vol bucket netted **+$68,844 (+7.6 bps/trade, win 20.8%)**. We therefore set expectations honestly: the mechanical edge appeared in **low-vol** regimes in this sample, which is the opposite of the Gao et al. high-vol-momentum result and is a small-sample observation, not a rule. Keep the gate off by default; any threshold (e.g. only trade when vol_rank < 0.5) is an experiment.

### 3.6 Transaction cost assumptions

- **Commission:** **$0** (Alpaca equity commission-free).
- **Per-side friction:** **half-spread 2 bps + slippage 3 bps = 5 bps per side**, i.e. **10 bps round-turn**.
- **Justification:** the dossier's measured spreads for mega-caps are ~0.67-1.67 bps half-spread (Aleti/Bollerslev/Siggaard), and off-radar mid-caps are ~15.0 bps (Vortex). Stocks-in-play are high-RVOL liquid names, so a 2 bps half-spread is a midpoint assumption for liquid large caps — **but** these names can widen materially on the catalyst move, which is why we stress costs.
- **Cost stress (our own run, net total $):**

| Cost case | Round-turn | Net P&L on basket |
|---|---|---|
| Base (1x) | 10 bps | **+$590** |
| Stress (2x) | 20 bps | **-$155,439** |
| Stress (3x) | 30 bps | **-$311,468** |

The strategy is therefore extremely cost-sensitive: it survives only if realized round-turn stays near 10 bps. Any systematic widening flips it negative.

---

## 4. Expected Performance

This table deliberately labels provenance. **"Authors' own"** = the paper's backtest, not independently replicated. **"Our run"** = this repo's mechanical basket test. **"Literature expectation"** = a range drawn from the verified works, not a promise.

### 4.1 Headline numbers by provenance

| Source | Variant | Key result | Provenance label |
|---|---|---|---|
| Gao et al. 2018 | SPY last-30-min timing | Sharpe **1.08** (vs B&H 0.29) | Literature (peer-reviewed) |
| Aleti/Bollerslev/Siggaard 2025 | SPY 15-min ML timing | After-cost Sharpe **1.37**, FF6 alpha 20.83% | Literature (peer-reviewed) |
| Zarattini et al. 2024 | QQQ-only ORB | Sharpe **1.12**, win rate **24%**, **0.13R/trade**, alpha 33% (p=0.0025) | Authors' own (working paper) |
| Zarattini et al. 2024 | Top-20 stocks-in-play | Sharpe **2.81**, >1,600% total, alpha 36% | Authors' own (working paper, cost-light) |
| **Our run** | **Fixed 35-name basket, mechanical ORB** | **1,046 trades, win ~18%, expectancy +0.3 bps, net Sharpe 0.007, net +$590** | **Our own backtest** |

### 4.2 Our own backtest run (full detail)

Setup: Alpaca v2 `iex` feed, window 2024-01-01 to 2026-10-16, 700 trading days, 35 symbols, $100,000 account, 0.5% risk/trade, 0.10xATR14 stop, 10R placeholder, 15:50 flatten, 10 bps round-turn. Regime gate and first-candle filter OFF.

**Totals (1x costs):**

| Metric | Value |
|---|---|
| Trades | 1,046 (524 long / 522 short) |
| Gross P&L | +$156,619 |
| Net P&L | **+$590** |
| Gross annualized Sharpe | 1.793 |
| **Net annualized Sharpe** | **0.007** |
| Net t-stat | 0.011 |
| Max drawdown | $12,274 |
| Block-bootstrap 95% CI (mean daily return) | [-0.00155, +0.00147] |
| **Block-bootstrap 95% CI (Sharpe)** | **[-1.34, +1.10]** |

**By year:**

| Year | Trades | Win % | Net $ | Expectancy bps | Avg RVOL |
|---|---|---|---|---|---|
| 2024 | 339 | 20.1% | +16,557 | +3.0 | 3.7 |
| 2025 | 388 | 14.2% | -48,144 | -8.4 | 3.4 |
| 2026 | 319 | 20.1% | +32,177 | +7.5 | 3.5 |

**By side:**

| Side | Trades | Win % | Net $ | Expectancy bps |
|---|---|---|---|---|
| Long | 524 | 17.6% | +22,047 | +2.8 |
| Short | 522 | 18.2% | -21,457 | -2.8 |

**By SPY volatility regime (vol_rank 20/60):**

| Regime | Trades | Win % | Net $ | Expectancy bps |
|---|---|---|---|---|
| High-vol SPY | 489 | 14.5% | -68,254 | -10.4 |
| Low-vol SPY | 557 | 20.8% | +68,844 | +7.6 |

**By SPY daily direction:**

| Day type | Trades | Win % | Net $ | Expectancy bps |
|---|---|---|---|---|
| SPY up day | 587 | 17.4% | -1,250 | -0.1 |
| SPY down day | 459 | 18.5% | +1,840 | +0.3 |

### 4.3 Realistic expectations for the deployed version

The deployed long-only, news-selected version is **not** the basket above. Setting expectations honestly:

- **Trade frequency.** The basket generated ~1.5 trades/day (1,046 over 700 days) with a 3-trade cap. A news-selected long-only universe will trade *less* often — plausibly on the order of **0-2 trades per day when names qualify, a handful per week otherwise**. Frequency is not guaranteed; the free IEX feed is sparse and may fail volume filters.
- **Expectancy.** The only cost-honest literature anchor is the QQQ ORB variant: **24% win rate, +0.13R/trade, Sharpe ~1.12**. Our own long side shows a low ~18% hit rate with positive skew. A realistic *literature-based* expectancy for a well-selected long ORB is **small per-trade expectancy with a low win rate and positive skew** — not the 2.81 top-20 figure, which is authors'-own and cost-light.
- **What we will not claim.** We do not expect to replicate 1,600% returns. The bootstrap CI on our own run straddles zero; the news-selection edge is unvalidated until the dashboard backtest is run. Treat any early paper-trade P&L as a thin sample, not as confirmation.

---

## 5. Validation Methodology

### 5.1 Data requirements

- **Intraday bars:** 1-minute consolidated/SIP bars for the universe; minimum **5-8 years** of history for a study of this kind (our run uses ~2.8 years, which is short). The free Alpaca IEX feed streams only IEX, not the consolidated tape; **use the same feed in backtest and live** — RVOL and VWAP will differ from SIP.
- **Daily history:** at least 60 prior sessions for ATR14/ADV14 and the vol_rank(20,60) regime read; the loader caps lookback at 250 sessions.
- **Data fingerprints:** every backtest run stores a hash of the strategy YAML, risk snapshot, engine source, and input data so a result is exactly reproducible; a later data revision is detected by fingerprint and does not silently alter history.

### 5.2 Walk-forward procedure

- Yearly folds (e.g. train on 2024, test on 2025; roll forward). The strategy has **no fitted parameters beyond the paper's thresholds**, so nothing is re-fit in the base run; experimental gates (`volrank_min`, `candle_atr_min`, `rvol_min`) must be selected on the training fold only and evaluated once on the holdout.
- The dashboard Backtesting workspace supports a chronological holdout that replays with fresh capital and fixed-rule rolling windows; a holdout used repeatedly must be counted as a research trial, not treated as a permanently unseen test set.

### 5.3 Correct cost and execution modeling

- **Next-bar fills:** an entry cannot fill on its decision bar; it fills on the next bar open (plus bounded slippage), matching the standalone script.
- **Stop vs limit:** conservative stop-first brackets — on a bar that touches both stop and target, the stop is assumed filled first.
- **Borrow:** short-side borrow availability and fees are not modeled in the standalone script; this is another reason to ship long only.
- **Costs:** charge the configured half-spread, slippage, and commission on every fill; stress at 2x and 3x.

### 5.4 Pitfalls to guard against

- **Look-ahead:** never fill at the crossing-bar close; daily-history functions read completed sessions only; news-gated rules use recorded signals at their original timestamps (models know the future in-sample).
- **Survivorship:** the 35-name basket is today's list — it contains no delisted names. A point-in-time historical constituent list is a known gap.
- **Selection bias in the news universe:** the entire edge depends on *which* names the news pipeline flags; validating news selection requires the dashboard's recorded-signal replay, not a static basket.
- **Overfitting via the experimental gates:** `volrank_min` and `candle_atr_min` are switches we have not turned; tuning them on the same 700 days is how the 2.81 number gets invented. Keep all trials, including failures.

### 5.5 Statistical tests

- Report **t-stats with Newey-West adjustment** (or a block bootstrap of daily returns, as done here with 1000x 10-day blocks).
- **Permutation tests** where applicable (Mesfin uses p < 0.001 as one of five gates).
- **Deflated Sharpe Ratio** (Bailey and López de Prado): correct for the number of trials run, including failures. Do not claim significance from a short sample or a Sharpe chosen after many experiments.

---

## 6. Infrastructure & Feature Gap Analysis

Nine capability components are required to run this intraday system. The table grades Thinkorswim, TradingView, a generic Python yfinance/Alpaca script, and **this repo (llamaOS)** as **Provided / Partial / Gap**.

| Component | Thinkorswim | TradingView | Python (yfinance/Alpaca) | llamaOS (this repo) |
|---|---|---|---|---|
| Real-time 1-min feed | Partial (retail, delayed-ish) | Partial (delayed on free tier) | Partial (Alpaca REST poll) | **Provided** — Alpaca bars/news polling; IEX now, SIP upgrade path |
| Opening-range calculation | Partial (manual) | Partial (scriptable) | Partial | **Provided** — `or_high/or_low(5)` builtins, 1-min rule engine |
| Breakout order execution | Partial | Gap (alerts only) | Partial (you write it) | **Provided** — marketable-limit brackets, stop/target at broker |
| Regime filter (SPY vol_rank) | Gap | Partial | Gap | **Partial** — `peer(SPY, vol_rank(20,60))` builtin, gate default OFF/unvalidated |
| Sizing calculation (0.5% risk) | Partial | Gap | Partial | **Provided** — risk-based shares, capped by account max_position |
| Automated stop/target | **Provided** | Partial | Gap | **Provided** — broker-side brackets (`require_bracket: true`) |
| EOD flatten (15:50) | Partial | Gap | Gap | **Provided** — strategy `flatten_at: 15:50`, risk `flatten_by: 15:55`, calendar-aware |
| Trade logging / audit | Partial | Gap | Gap | **Provided** — hash-chained, advisory-locked audit log; every order/fill recorded |
| Walk-forward backtesting | Gap | Gap | Partial | **Partial** — dashboard Backtesting workspace with holdout, 2x/3x cost stress, bootstrap; formal parameter optimization/walk-forward fitting still a cataloged gap |

**Mapping to llamaOS components (from the README).** The system already supplies:

- **Broker brackets** — stop and target live at Alpaca; `require_bracket: true`.
- **EOD flatten** — 15:50 strategy flatten, clamped on early-close days; a recommended backup flatten job at 15:57 on a separate host.
- **Risk guard** — hard limits in Postgres, outside every model's reach; a strategy can only tighten them.
- **Kill switch** — fires on daily-loss breach, reconciliation mismatch, stale feed, or manual command; blocks entries, cancels orders, flattens positions; reset is host-only.
- **Audit log** — searchable, hash-chain verified, exportable.
- **Dashboard Backtesting workspace** — configurations, data preflight, holdout window, 2x/3x cost stress reruns, and seeded bootstrap of daily returns.

**Remaining gaps:** historical (point-in-time) universe constituents, row-level feed/adjustment provenance, quote/depth and partial-fill modeling, short borrow accounting, formal walk-forward parameter fitting, and a per-strategy daily-loss circuit breaker that the standalone backtest does not simulate.

---

## 7. Improvement Roadmap

Research-grounded upgrades, in priority order:

1. **Validate the news-selected universe first.** Run the actual news-gated strategy in the dashboard Backtesting workspace (holdout, 2x/3x cost stress, bootstrap). This is the single most important upgrade — it is the untested premise.
2. **Entry-quality filters.** Add breadth/VWAP/close filters documented as suggestions in the YAML: e.g. require price above the opening VWAP on a long, or confirm broad market breadth, to skip weak breakouts. Test as a separate fold.
3. **Execution optimization.** Use limit / stop-limit entry orders instead of marketable stops where possible, and **avoid the opening auction** — the opening range is formed 09:30-09:35; the first print of the day is noisy. This directly attacks the 10 bps cost floor that determines profitability.
4. **Multi-sleeve uncorrelated instruments.** Add a second, uncorrelated sleeve (e.g. the Family B last-30-min SPY momentum, or a different catalyst bucket) so daily P&L is not carried by one regime.
5. **Regime-adaptive parameters.** Our low-vol observation (edge +7.6 bps low-vol vs -10.4 bps high-vol) suggests testing a vol_rank *down*-gate rather than the assumed high-vol gate — but treat it as exploratory until confirmed out-of-sample.
6. **The 218-factor ML pipeline (Family E)** as a separate project: build the factor-portfolio data layer, a 15-minute rebalancing loop, and the ensemble training/test harness that Aleti/Bollerslev/Siggaard use. Do not fold it into this v1.
7. **Daily-loss circuit breaker.** Add an explicit per-strategy intraday daily-loss halt to the standalone research model so backtest numbers reflect the same guard the live stack enforces.

---

## 8. Deployment Guide (DEPLOY)

### 8.1 Where the strategy lives

- Long: `strategies/orb_stocks_in_play_pro_long.v1.yaml`
- Short mirror (gated): `strategies/orb_stocks_in_play_pro_short.v1.yaml`
- Rerunnable backtest: `scripts/research_orb_backtest.py`

### 8.2 Validate the YAML with the DSL loader/validator

From the repo root, using the factory risk file and the local test database:

```bash
RISK_FILE=config/risk.example.yaml \
DATABASE_URL=postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_test \
.venv/bin/python - <<'PY'
from pathlib import Path
from dsl import loader, validator
from common import config
risk = config.load_risk()
for f in ["orb_stocks_in_play_pro_long.v1.yaml",
          "orb_stocks_in_play_pro_short.v1.yaml"]:
    text = Path("strategies/" + f).read_text()
    loader.load_text(text)          # strict parse; raises on anchors/aliases/tags/dupes
    v = validator.validate(text, risk)
    print("===", f, "ok=", v.ok)
    for e in v.errors: print("  ERROR:", e)
PY
```

Expected result (per `reports/validation_output.txt`): the **long** file validates `ok=True` (side long, window 09:35-10:30, flatten 15:50, risk 0.5%, references SPY); the **short** file validates `ok=False` with `entry.side 'short' is not allowed: allow_short is off on the Risk page`. That rejection is the intended default, not a bug.

### 8.3 Backtest via the dashboard Backtesting workspace

1. Open **Backtesting** in the dashboard and select the exact registered strategy version.
2. Set inclusive ET session dates, starting USD equity, the symbol basket, and a benchmark.
3. Set execution assumptions (half-spread, slippage, commission).
4. **Check data coverage**; news gates use recorded signals at their original timestamps.
5. Run the required validations: a **holdout window**, **2x and 3x cost stress**, and the **seeded bootstrap** of daily returns.
6. Review Saved runs: overview, trades, diagnostics, validation; compare up to four configurations.

Do not promote a strategy to paper until the news-selected universe clears these tests on the holdout.

### 8.4 Enabling the short side

Shorting is controlled by `allow_short` on the dashboard **Risk** page (factory default `false`, mirrored at Alpaca). To run the short mirror: enable `allow_short` there with a recorded reason, confirm borrow availability, and re-run the validator. **Our own evidence does not support it** (short net -$21,457, -2.8 bps/trade); enabling it is a deliberate, documented experiment.

### 8.5 Going to Alpaca paper

- Put paper credentials in `secrets/broker.env` against `https://paper-api.alpaca.markets`.
- Run `make init`, then `make paper-setup` (loads real IEX history, validates/backtests strategies, enables paper execution).
- Enroll the `paper-owner` account from `secrets/paper-owner.txt` in your authenticator.
- Run `make paper` and open the dashboard. Set individual strategies to `manual` if you want per-trade approval.
- Keep `MODE=record_only` initially while the news pipeline builds point-in-time signal history.

**What the system already provides:** broker-side brackets (stop/target), EOD flatten at 15:50 (15:55 risk deadline, calendar-aware), the risk guard, the kill switch, and the hash-chained audit log.

**Remaining gaps before live:** the dashboard Backtesting validation of the news-selected universe (the standalone script only tested a fixed basket), and an explicit per-strategy daily-loss circuit breaker in the research model. Real-money execution is disabled by design; paper-first is mandatory.

---

## References

All URLs below are from the verified dossier section 5.

1. Mesfin, M. (2026). "Structural Limits of OHLCV-Based Intraday Momentum Signals in MNQ Futures." arXiv:2605.04004. https://arxiv.org/abs/2605.04004
2. Vortex Capital Group (2026). "ORB Statistical Framework: Why the Long-Short Asymmetry Has Inverted." (Blog post.) https://www.vortexcapitalgroup.com/insights/opening-range-breakout-a-statistical-framework-for-active-equity-day-traders
3. Vortex Capital Group (2026). "The Off-Radar Reversion Illusion: The 73% Fade That's Really 35%." (Blog post.) https://www.vortexcapitalgroup.com/insights/the-off-radar-reversion-illusion-the-73-fade-thats-really-35
4. Aleti, S., Bollerslev, T., Siggaard, M. (2025). "Intraday Market Return Predictability Culled from the Factor Zoo." Management Science. https://scholars.duke.edu/publication/1573580 ; http://public.econ.duke.edu/~boller/Papers/MS_2025.pdf
5. Baltussen, G., Da, Z., Lammers, S., Martens, M. (2021). "Hedging demand and market intraday momentum." JFE 142, 407-427. https://www3.nd.edu/~zda/intramom.pdf ; https://tinbergen.nl/publication/168044
6. Holmberg, U., Lönnbark, C., Lundström, C. (2013). "Assessing the profitability of intraday opening range breakout strategies." Finance Research Letters 10(1), 27-33. http://www.usbe.umu.se/digitalAssets/102/102002_ues845.pdf
7. Zarattini, C., Barbon, A., Aziz, A. "Beat the Market: An Effective Intraday Momentum Strategy for SPY." SFI RP 24-97. https://ideas.repec.org/p/chf/rpseri/rp2497.html
8. Zarattini, C., Barbon, A., Aziz, A. (2024). "A Profitable Day Trading Strategy for the U.S. Equity Market." SSRN 4729284 / SFI RP 24-98. https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf
9. Gao, L., Han, Y., Li, S., Zhou, G. (2018). "Market Intraday Momentum." JFE 129(2), 394-414. https://ideas.repec.org/a/eee/jfinec/v129y2018i2p394-414.html
10. Novy-Marx, R., Velikov, M. (2016). "A Taxonomy of Anomalies and Their Trading Costs." RFS 29(1), 104-147. http://www.nber.org/papers/w20721.pdf
11. Heston, S., Korajczyk, R., Sadka, R. (2010). "Intraday Patterns in the Cross-section of Stock Returns." JF 65(4), 1369-1407. https://arxiv.org/pdf/1005.3535
12. Bogousslavsky, V. (2021). "The Cross-Section of Intraday and Overnight Returns." JFE 141(1), 172-194. https://ideas.repec.org/a/eee/jfinec/v141y2021i1p172-194.html
13. Barbon, A., Buraschi, A. (2021). "Gamma Fragility." https://www.abarbon.com/assets/Barbon_Buraschi_2021_Gamma_Fragility.pdf
14. Crabel, T. (1990). Day Trading with Short Term Price Patterns and Opening Range Breakout. (Trading book, origin reference.) https://crabel.com/who-we-are
15. QuantConnect ORB replication discussion. https://www.quantconnect.com/forum/discussion/19456/opening-range-break-paper/p1

---

## Risk Disclaimer

**Educational and research use only.** This document is an internal quantitative research report. It is not investment advice, not an offer or solicitation, and not a recommendation to buy or sell any security.

**No guaranteed profits.** Nothing in this report promises, guarantees, or implies any specific return. Intraday trading is speculative and can result in the loss of some or all of invested capital.

**Past and backtested performance.** Backtested and simulated results are hypothetical, have known limitations, and do not represent actual trading. The authors'-own figures cited from the literature (e.g. Sharpe 2.81, >1,600% returns) have **not** been independently replicated and were produced with cost-light assumptions. Our own backtest nets approximately zero after costs and has a confidence interval that includes negative Sharpe. Past performance, whether backtested or live, does not guarantee future results.

**Shorting, leverage, and market risk.** Short selling carries unlimited loss potential and borrow/recall risk. Even with no leverage, equity prices can gap against positions; the news-selected universe concentrates in volatile, high-RVOL names that can move sharply and unpredictably.

**Data and execution risk.** Historical data may be incorrect, delayed, or subject to revisions; the free IEX feed differs from the consolidated tape. Live fills may differ materially from backtested assumptions due to slippage, partial fills, halts, order-book depth, and network or broker outages. No software can guarantee an exit while an external API or network is unavailable.

**Validation first.** The deployed strategy's core premise — the news-selected universe — has **not** yet been validated out-of-sample. Do not trade with real money until the strategy has passed the dashboard Backtesting holdout and cost-stress tests and a sufficient paper-trading period.

**Consult a licensed advisor.** Before committing capital, consult a properly licensed financial professional. Start in paper-trading mode. You are solely responsible for any trading decisions and their outcomes.
