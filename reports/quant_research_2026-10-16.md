# Quantitative Day Trading Strategy: Research & Specification Report

**Report version:** 1.0
**Date:** 2026-10-16
**Author:** Senior quantitative research (automated), prompt `quant_research@v1`
**Target system:** llamaOS — news-driven day-trading system on Alpaca paper trading (DSL v0.1, 1-minute bars, flat by close)
**Deliverable strategy:** `strategies/eod_reversal_losers_long.v1.yaml` — End-of-Day Reversal, long the intraday losers (Baltussen, Da & Soebhag 2025)
**Backtest artifact:** `scripts/research_eod_reversal_backtest.py`; saved runs under `reports/eod_reversal_*/`

---

## 0. Executive summary

- **Selected strategy:** buy, at ~15:30 ET, mega-cap stocks whose return from the previous close to 15:00 ET ("ROD3") is at least 0.25 × their 14-day ATR **down**, hold to the 15:55 forced flatten. This is the long side of the **end-of-day reversal** documented by Baltussen, Da & Soebhag (April 2025, Erasmus/Notre Dame working paper) on TAQ/CRSP 1993–2019: cross-sectionally, intraday **losers bounce in the last half hour** (value-weighted loser-minus-winner spread 3.78 bps/day, t = 10.69; among the largest-quintile stocks 3.41 bps/day, t = 10.61). The mechanism is end-of-day retail buy-the-dip buying plus reduced short-selling into the close. The effect is **entirely on the loser side**, which makes a long-only implementation evidence-aligned rather than a compromise.
- **Why this one:** it is the strongest verified, mechanism-based, untapped literature result that this system can express. ORB (Zarattini et al. 2024) and market intraday momentum (Gao et al. 2018) are already deployed; VWAP fades have weak-to-negative verified evidence; factor-zoo ML (Aleti, Bollerslev & Siggaard 2025) is not expressible in DSL 0.1; overnight-clientele strategies (Lou, Polk & Skouras 2019) require holding overnight, which this system forbids.
- **Own backtest result (net of realistic costs, 169 sessions 2026-02-12 → 2026-10-15, 8 mega-caps, 320 trades):** gross edge **+2.9 bps/trade** — remarkably close to the paper's 3.41 bps/day for mega-caps — but the realistic round-trip cost of ~6 bps turns it into **−3.08 bps/trade net (t = −1.12), daily Sharpe −1.12, net −$969.63 on $100,000**. The chronological holdout is worse than in-sample (−5.59 vs −1.38 bps). Cost stress at 2×/3× gives −9.1/−15.0 bps. Block bootstrap: P(mean daily return ≤ 0) = 0.87. Three pre-specified parameter variants give −3.48/−3.08/−3.18 bps — flat, no sweet spot.
- **Conclusion:** the literature effect appears to be present in our data at the predicted magnitude, but **it does not survive realistic retail transaction costs on an 8-name universe with marketable-limit entries and market flattens**. This independently reproduces, on US equities, the central finding of the verified falsification study arXiv:2605.04004 (14 OHLCV signal families on MNQ futures, none passing all five net-of-cost criteria). **Recommendation: do not approve for paper trading as a return-seeking strategy.** The YAML is shipped as a validated, honest artifact; §11 lists the specific evidence that would change this decision.

---

## 1. Literature survey and verification

Every work below was fetched and checked this session. Verification status is explicit; literature figures are the authors' own and are **not** my backtest results (those are in §4, clearly separated).

### 1.1 Verification table

| # | Work | Status | Where verified |
|---|------|--------|----------------|
| 1 | Gao, Han, Li & Zhou (2018), "Market Intraday Momentum", JFE 129(2), 394–414 | **Verified** | Multiple independent bibliographies incl. doi:10.1016/j.jfineco.2018.05.009 |
| 2 | Baltussen, Da, Lammers & Martens (2021), "Hedging Demand and Market Intraday Momentum", JFE 142(1), 377–403 | **Verified** | Reference lists quoting JFE 142(1), 377–403; Zhi Da is at Notre Dame — this is the "purported Notre Dame paper on gamma hedging and intraday momentum"; the attribution in the brief was imprecise, the paper is real |
| 3 | Baltussen, Da & Soebhag (2025), "End-of-Day Reversal", working paper (April 2025) | **Verified, full text fetched** | https://academicweb.nd.edu/~zda/EOD.pdf |
| 4 | Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy for the U.S. Equity Market", SSRN 4729284 / SFI RP 24-98 | **Verified** | EconPapers RePEc:chf:rpseri:rp2498 (abstract with headline figures) |
| 5 | Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)", SSRN 4824172 / SFI RP 24-97 | **Verified** | SSRN listing; this is the "SPY intraday momentum paper (RePEc, 2024)" — real |
| 6 | Zarattini & Aziz (2023), "Can Day Trading Really Be Profitable?", SSRN 4416622 | **Verified** | SSRN listing |
| 7 | Heston, Korajczyk & Sadka (2010), "Intraday Patterns in the Cross-Section of Stock Returns", JF 65(4), 1369–1407 | **Verified** | Multiple journal reference lists |
| 8 | Bogousslavsky (2016), "Infrequent Rebalancing, Return Autocorrelation, and Seasonality", JF 71(6), 2967–3006; and Bogousslavsky (2021), "The Cross-Section of Intraday and Overnight Returns", JFE 141(1), 172–194 | **Verified** | Journal reference lists |
| 9 | Lou, Polk & Skouras (2019), "A Tug of War: Overnight Versus Intraday Expected Returns", JFE 134(1), 192–213 | **Verified, abstract fetched** | personal.lse.ac.uk/polk/research/TugOfWar.pdf; ScienceDirect S0304405X19300650 |
| 10 | Berkman, Koch, Tuttle & Zhang (2012), "Paying Attention: Overnight Returns and the Hidden Cost of Buying at the Open", JFQA | **Verified** (existence and finding as cited within fetched full texts) | Cited in [3] and in repo strategy files |
| 11 | Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs", RFS 29(1), 104–147 | **Verified** | Multiple journal reference lists; doi:10.1093/rfs/hhv042 |
| 12 | Aleti, Bollerslev & Siggaard (2025), "Intraday Market Return Predictability Culled from the Factor Zoo", Management Science 71(9), 7731–7751 | **Verified, abstract fetched** | public.econ.duke.edu/~boller/Papers/MS_2025.pdf; RePEc:inm:ormnsc:v:71:y:2025:i:9:p:7731-7751. The brief's "Boller et al. (Duke 2025)" — real; lead author is Aleti |
| 13 | arXiv:2605.04004 (Mesfin, 2026), "Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures" (v3, Sep 2026) | **Verified, abstract fetched** | https://arxiv.org/abs/2605.04004 |
| 14 | "Handelshögskolan (Stockholm School of Economics) ORB papers" | **Misattributed institution — real papers, different university.** The works are Holmberg, Lönnbark & Lundström, "Assessing the profitability of intraday opening range breakout strategies", **Umeå Economic Studies 845**, and Lundström's thesis "On the Returns of Trend-Following Trading Strategies" (Umeå Economic Studies 948, Paper II: ORB returns across volatility states). They are hosted on the Swopec server at hhs.se (swopec.hhs.se/umnees/abs/umnees0845.htm), which explains the confusion | swopec.hhs.se |
| 15 | "2026 Vortex Capital study on long/short ORB asymmetry" | **Exists but is prop-desk marketing content, not research.** vortexcapitalgroup.com publishes "ORB Statistical Framework: Why the Long-Short Asymmetry Has Inverted" (2026-05-13) and "Opening Range Breaks That Actually Pay" (60-day study of SPY/QQQ/IWM/NVDA/TSLA, 2026-05-14). No methodology section sufficient for replication at study level; treat as practitioner claims | vortexcapitalgroup.com/insights |
| 16 | Vortex Capital "The Off-Radar Reversion Illusion: The 73% Fade That's Really 35%" (2026-07-11) | **Exists; practitioner research note with documented methodology** (29 off-radar mid-caps vs 8 mega-caps, ~85 sessions Mar–Jul 2026, SIP 1-minute bars, measured NBBO half-spreads). Not peer-reviewed; used here only as practitioner evidence | vortexcapitalgroup.com |
| 17 | Cboe research, "0DTE Index Options and Market Volatility: How Large is the Impact?" | **Verified** | cdn.cboe.com research publication |
| 18 | Ni, Pearson, Poteshman & White (2021), "Does Option Trading Have a Pervasive Impact on Underlying Stock Prices?", RFS 34, 1952–1986 | **Verified** | Reference lists (Moerke dissertation) |
| 19 | Barbon, Beckmeyer, Buraschi & Moerke (2021), "Liquidity Provision to Rebalancing Flows from Leveraged ETFs and Equity Options" | **Verified** | abarbon.com PDF |
| 20 | Bailey, Borwein, López de Prado & Zhu (2014), "The Deflated Sharpe Ratio", JPM 40(5), 94–107 | **Verified** | davidhbailey.com/dhbpapers/deflated-sharpe.pdf (already cited in repo docs) |

No cited work was found to be wholly fabricated. Two attributions in the brief were wrong in institution/name (#2, #12, #14) and two "studies" are commercial content rather than research (#15, #16); all are handled above.

### 1.2 Family assessment

**(a) Opening Range Breakout (ORB).**
- *Evidence:* Zarattini, Barbon & Aziz (2024): 5-minute ORB on 7,000+ US stocks 2016–2023, restricted to "Stocks in Play" (RVOL > 1, price > $5, ATR(14) > $0.50); top-20 portfolio net of costs: Sharpe 2.81, 36% annualized alpha, > 1,600% total net return vs 198% for SPY. QuantConnect's replication: Sharpe ≈ 2.4 (universe-size robust, 97% probabilistic Sharpe). Umeå studies: ORB returns differ by volatility state by ~150 bps/day (S&P futures) to ~200 bps/day (crude oil) between highest and lowest vol states — strong regime dependence. Holmberg et al.: significantly positive average returns concentrated in the most volatile periods.
- *Counter-evidence:* the verified falsification study (arXiv:2605.04004) finds the MNQ **ORB-long family clears its friction floor but fails statistical criteria (OOS net T = 0.88, year-unstable)**. A 12-session mechanical ORB-retest test on 5 liquid names (whatdoesntwork.com, June 2026): −4.89 bps/trade after 2 bps costs, inconclusive but weak.
- *Verdict:* real but regime-dependent; concentrated in news-driven small/mid "stocks in play" with 4× leverage in the paper — neither the leverage nor the 7,000-name universe is available here. **Already deployed** in this repo (`orb5_stocks_in_play_long/short.v1.yaml`). Long/short asymmetry claims (Vortex) are practitioner-grade only.

**(b) Intraday momentum (market level).**
- *Evidence:* Gao et al. (2018): previous-close→10:00 return predicts the last half hour on SPY 1993–2013; stronger on volatile, high-volume, recession and macro-news days; holds in 10 other liquid ETFs. Baltussen et al. (2021): the same across asset classes, driven by **hedging demand** (gamma hedging of index options and leveraged-ETF rebalancing flows into the close). Cboe's 0DTE study confirms dealer gamma is large and time-varying (typically vol-dampening; max impact +3.3 pp on annualized daily vol, +6.4 pp on 30-min vol on negative-gamma days).
- *Verdict:* the best-established market-level intraday effect. **Already deployed** (`intraday_momentum_last_half_hour_long/short.v1.yaml`). Effect size is small (R² ≈ 2%), cost-sensitive.

**(c) VWAP-based strategies.**
- *Evidence:* no peer-reviewed study supports mechanical VWAP fading. Zarattini & Aziz's VWAP paper is a working paper on QQQ/TQQQ trend-following, not reversion. The verified practitioner study ("Off-Radar Reversion Illusion", VCG 2026) is directly on point and negative: stretched off-radar names "revert" 72.6% of the time by the naive end-of-day measure, but only **35.2% actually reach VWAP before a 1σ stop**; gross +1.7 bps/trade, **net −24 bps/trade** after measured spreads; 76% of apparent reversion is VWAP drifting to the price, not price returning. Off-radar half-spreads (15.0 bps vs 0.82 bps mega-cap) are the killer.
- *Verdict:* weakest family; fades fail net of costs; **already deployed** in several variants here; no new VWAP file warranted.

**(d) The falsification study (arXiv:2605.04004).** Real (v3, Sep 2026; author Mathias Mesfin). Claims from the abstract (full HTML was not retrievable in this environment — arXiv returned 404 for the HTML render; abstract-level results quoted): 14 signal families, 947 trading days of 5-minute MNQ data 2021–2025, expanding-window walk-forward; five pass criteria (OOS net T ≥ 2.0; ≥ 30 trades/fold; positive net after instrument friction; direction stable across 2023/2024/2025; permutation p < 0.001 where applicable). **None passed all five.** Eleven families die on friction (gross 0.07–1.50 points vs a 2.0-point floor). Three clear friction but fail elsewhere: ORB long (T = 0.88, year-unstable), VVG classifier reversal (T = 1.26, year-unstable), gap continuation short (gross +16.53 pts, T = 1.46, fails stability and sample minimums). Two positive controls (RTH Confluence T = 3.11; London Session B T = 4.30) show the methodology can detect genuine edge.
- *Implication for this project:* raw OHLCV momentum/breakout signals on index futures do not survive honest costing. Any candidate must (i) target a **documented flow** rather than a shape on the tape, (ii) trade where friction is smallest (mega-cap cash equities, ~1 bp half-spreads), and (iii) still expect the gross edge to be single-digit bps — so costs decide everything.

**(e) Factor-based intraday predictability.** Aleti, Bollerslev & Siggaard (2025, Management Science): sizeable OOS Sharpe and alphas net of costs trading liquid ETFs on ML-regularized predictions from the lagged high-frequency cross-section of the factor zoo; performance concentrated in high-uncertainty periods; key factors are tail-risk and liquidity; mechanism = slow-moving capital. **Not implementable** in DSL 0.1 (needs the cross-section of factor portfolios and a trained regularized regression; no such primitives exist, and adding them belongs in `dsl/`, not in a strategy file).

**(f) Verified canon (as needed).** Heston et al. (2010): same-interval cross-day return persistence (needs same-minute cross-day statistics — not expressible here). Bogousslavsky (2016, 2021): infrequent rebalancing; arbitrageur unwinding into the close. Lou, Polk & Skouras (2019): persistent overnight/intraday return clienteles with cross-period reversal (needs overnight holding — forbidden here). Berkman et al. (2012): retail attention inflates opening prices. Novy-Marx & Velikov (2016): high-turnover anomalies die on costs — the general theorem behind this report's conclusion. Bailey et al. (2014): deflated Sharpe / multiple-testing discipline, already embedded in the repo's research docs.

**(g) End-of-day reversal (selected family).** Baltussen, Da & Soebhag (2025), full text fetched:
- ROD3 (prev close → 15:00) **negatively** predicts the 15:30–16:00 return cross-sectionally; panel t-stats typically > 10; present in almost every 3-year rolling window.
- Quintile sorts: VW loser-minus-winner **3.78 bps/day (t = 10.69)**, FF6 alpha 3.71 (t = 10.61); EW **6.38 bps/day (t = 17.30)**; ≈ 9.5%/17.3% annualized (positions held only 30 min/day).
- **Present among the largest, most liquid stocks**: within the largest-size quintile, L−S alpha **3.41 bps/day (t = 10.61)**.
- **Robust to skipping the last 5 minutes** (15:30–15:55 horizon: coefficient −0.92, t = −8.76) — exactly this system's flatten time.
- **Asymmetry: the effect is entirely driven by negative ROD3** (losers bounce; winners do not reliably fall) — long-only is the evidence-based side.
- Mechanism, tested with three retail-trade classifiers and proprietary short-position data: end-of-day **buy-the-dip retail purchases** (attention-driven, stronger for losers) plus a **> 3× stronger drop in new short positions on losers** into the close (overnight risk management). The price pressure is **transitory** (reverts next day) — it is liquidity provision, not information.
- The authors' own cost caveat: "the strategy as presented might not be exploitable by many investors after accounting for transaction costs."
- Reconciliation with Gao et al.: individual stocks show time-series momentum but **cross-sectional** reversal (cross-stock autocovariance dominates) — the two are complementary, so this file does not contradict the deployed momentum file.

---

## 2. Strategy selection and justification

**Selected: End-of-Day Reversal, long the intraday losers, on 8 mega-cap tech names.** Rationale, and why each alternative lost:

1. **Strength of evidence.** TAQ/CRSP 1993–2019, t > 10, robust across rolling windows, subsamples, quote-midpoint returns, and closing-price effects; the loser side (what we trade) is where the effect lives. Only Gao et al. / Baltussen et al. (already deployed) and Zarattini et al. (already deployed) are comparably strong.
2. **Microstructure rationale (who is on the other side, and why it persists).** Retail traders buy attention-grabbing dips into the close and short-sellers stop opening new shorts on losers into the close; both flows are one-directional on the loser side and predictably recur because they are behavioral and risk-management driven, not informational. We are paid for **providing liquidity into a temporary, reverting price pressure** — a mechanism, not a pattern.
3. **Instrument fit.** The effect holds in the largest, most liquid names; those are exactly the names with ~1 bp half-spreads where a 3–4 bps gross effect has any chance. This system trades only cash equities/ETFs, no futures/options/leverage — the strategy needs none of those.
4. **System fit.** 25-minute hold, flat by 15:55 (the paper's own robustness window), entries inside the account's 09:35–15:30 window, no signals required (price-only → clean point-in-time backtests), long-only matching the evidence.
5. **Not duplicative.** Distinct from the deployed Gao-style file (market-level continuation vs stock-level reversal; complementary per the paper's decomposition) and from the deployed ORB files (morning breakout vs close liquidity provision).

**Why it would survive where the MNQ falsification study's families failed — and where it does not.** In its favor: it is a cross-sectional, flow-based anomaly with t > 10 in 27 years of data, traded in instruments with ~40× tighter spreads than the study's friction assumptions, on the side (long losers) where the flow is documented. Against it, and decisive in my own test: the per-trade gross edge in mega-caps (~3 bps) is the same order as the study's "cleared friction but failed statistics" families, and my round-trip cost (~6 bps) is about twice the gross edge. My backtest (§4) reproduces the study's outcome on equities: gross ≈ +2.9 bps, net ≈ −3.1 bps. I concede the point: **mechanism alone does not create capacity to pay the toll.**

---

## 3. The fully specified trading system (exact numbers)

File: `strategies/eod_reversal_losers_long.v1.yaml` (passes the repo validator with zero errors/warnings against the effective account risk controls; hash prefix `22795fd3401d2cb7`).

| Component | Specification |
|---|---|
| Universe | `AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA` (fixed list); filters: prior close ≥ $5, 20-day average daily dollar volume ≥ $1B. Justification: top-decile retail attention, deepest books in the US market, typical quoted spreads ≈ $0.01 (≈1 bp), and the only symbols with full 1-minute history stored in this system (169 sessions each) |
| Predictor | `rod3 = close_at(330) / prior_close − 1` — previous close → 15:00 ET bar close; exactly the paper's ROD3, skipping 15:00–15:30 against microstructure noise |
| Entry trigger | `rod3 ≤ −0.25 × (atr_d(14) / prior_close)` — the day's move to 15:00 must be at least a quarter of the stock's own 14-day daily ATR down. Volatility-scaled, so the threshold self-adjusts across regimes (≈ −0.6% for a 2.5%-ATR name) |
| Entry window | decision on the 15:29/15:30 ET bar close; fill at the next bar's open (system fill model: marketable limit, next-bar open + slippage) |
| Selection/cap | at most 3 positions and 3 trades/day (account caps 50/500, factory 3/10); when more than 3 qualify, the backtest takes the **lowest rod3 first** (the paper's extreme-decile intent); the live engine takes symbols in session order — a documented live/backtest difference |
| Position sizing | risk 0.25% of equity per trade; qty = floor(0.0025 × equity / (0.3 × ATR_d(14))), capped at 10% of equity notional (mirrors `core/engine.py`); average deployed notional in the backtest ≈ $9.8k per trade on $100k |
| Stop | entry − 0.3 × ATR_d(14) (protective only; ≈ 0.75–1%; hit in 15 of 320 backtest trades) |
| Target | entry + 3 × risk (protective; never hit in the backtest) |
| Time exit | forced flatten 15:55 ET at market (account's `flatten_by`); the trade's intended exit; 305 of 320 trades exit this way |
| Regime filter / do-not-trade | the ATR-scaled threshold itself is the regime adaptation (no trade on quiet days); explicit no-trade conditions: rod3 above the threshold, missing 15:00 bar, missing prior history (21+ sessions), price < $5, ADV < $1B, outside 15:29–15:30, or any risk-control state (kill switch, daily-loss breach) |
| Transaction costs (backtest) | commission $0 (Alpaca retail API: commission-free US equities; sell-side regulatory fees ≈ 0.14 bp/side, negligible and noted), slippage 2 bps/side + half-spread 1 bp/side ⇒ **~6 bps round trip**, matching the repo workspace convention "full spread contributes half per side"; stress at 2×/3×. The 1 bp half-spread is the VCG-measured mega-cap median (0.82 bps); 2 bps slippage is conservative for ~$10k orders in these names |

Live-system vs paper differences (all declared in the file's `assumptions`): absolute threshold instead of a cross-sectional decile rank (no rank builtin in DSL 0.1); next-bar-open fills instead of a resting 15:30 order; 15:55 flatten instead of 16:00 (paper's robustness check covers this); 0.25% risk vs the paper's portfolio construction; no leverage; no short side (evidence-based, and the account currently allows shorts — the file is long-only by choice).

---

## 4. Expected performance — sourced, literature vs own backtest

**Literature-reported (authors' figures, gross unless noted, TAQ/CRSP 1993–2019):** VW loser-minus-winner 3.78 bps/day (t = 10.69); EW 6.38 bps/day (t = 17.30); largest-quintile L−S alpha 3.41 bps/day (t = 10.61); annualized 9.5% (VW) / 17.3% (EW) on capital deployed 30 min/day; bottom-decile losers ≈ +400% cumulative over 27 years in the 30-min window. The long side alone is not separately reported; the L−S being loser-driven implies roughly half the spread, i.e. ~2–4 bps/day for a mega-cap long-loser sleeve.

**Own backtest (this repo's stored bars, 169 sessions 2026-02-12 → 2026-10-15; saved run `reports/eod_reversal_20261016_061710/`; data fingerprint `40fb3cb5…`):**

| Metric | Value (net) | Note |
|---|---|---|
| Trades | 320 (1.89/day) | 8 symbols, ≤ 3/day cap |
| Win rate | 47.8% | |
| Expectancy | **−3.08 bps/trade (−$3.03)**; gross **+2.9 bps/trade** | per-trade t = −1.12 |
| Net P&L | −$969.63 on $100k (gross +$914.92, fees $0) | ≈ −0.97% over 8 months |
| Daily Sharpe / t | −1.12 / −0.92 | profit factor 0.84 |
| Max drawdown | −1.86% (daily closes) | |
| In-sample (to 2026-07-10) | 191 trades, −1.38 bps, Sharpe −0.46 | |
| Holdout (from 2026-07-13) | 129 trades, **−5.59 bps, Sharpe −2.55** | OOS worse, not better |
| Cost stress ×2 / ×3 | −9.09 / −15.01 bps per trade | linear in costs, as expected |
| Block bootstrap (10k, block 5, seed 7) | mean daily p5/p50/p95 = −1.7/−0.7/+0.3 bps; **P(mean ≤ 0) = 0.87** | |
| Regimes | high-vol days **+1.26 bps** (169 trades) vs low-vol −7.93 bps (151) | directionally consistent with the mechanism; descriptive only — turning it into a filter now would be in-sample selection |
| Symbol dispersion | TSLA +10.1 (52), AMD +4.1 (48); NVDA −14.0 (44), GOOGL −10.5 (34) | with ~40 trades/name this is noise-grade; reported, not acted on |

**Pre-registered sensitivity (3 declared values from the file's `suggestions`):** drop_atr 0.15 → −3.48 bps (t = −1.38, 362 trades); 0.25 → −3.08 (t = −1.12, 320); 0.40 → −3.18 (t = −1.03, 258). Flat neighborhood: the negative result is not a tuned artifact, and there is no nearby rescuer.

**Trial log (multiple-testing honesty):** 4 strategy variants evaluated (the 3 above + the initial rvol-filtered structure, −6.01 bps on 41 trades, abandoned for the data-provenance reason in §5.4), each with 3 cost-stress reruns. Best |t| across everything = 1.38 « 2.0 even before any deflation; with 4 trials a Bonferroni/deflated-Sharpe adjustment only widens the gap. Annualized figures are deliberately not quoted: 8 months, one year, one regime.

**Reading:** the measured gross edge (+2.9 bps) matches the paper's mega-cap magnitude (3.41 bps/day) surprisingly well; the strategy fails exactly where the falsification study says such strategies fail — the friction floor. Realistic expectation if deployed unchanged: **slightly negative net, ~0% gross capacity after costs**.

---

## 5. Validation methodology (as executed)

1. **Data.** Stored 1-minute bars ingested by this repo's paper broker from Alpaca (same feed the system trades), 169 sessions × 8 symbols, 390 bars/day; daily bars for prior history (6+ years). No Alpaca Market Data keys exist in this checkout's `.env`, so direct download was unavailable; yfinance was not used (its 1-minute history covers only roughly the last 30 days, ~8 days per request — it cannot span the stored window; the brief's "~730 days" figure overstates yfinance's 1-minute limit). Data fingerprint: sha256 over (symbol, day, bar count, first/last ts, close) = `40fb3cb5517ffd01…` (saved in each run's results.md).
2. **Execution accounting.** Next-bar-open fills (no decision-bar fills), marketable-limit entry + 2 bps, half-spread 1 bp/side, stop-first on ambiguous bars, fresh-fill-bar rule, targets without extra adverse slippage, 15:55 flatten at bar close − costs, commissions $0 with sell-side regulatory fees noted (~0.14 bp/side, immaterial). Same conventions as `docs/backtesting.md` and `core/exchange.py`.
3. **Walk-forward / holdout.** Multi-year walk-forward is infeasible — the stored intraday data spans a single year (2026); stated plainly instead of being faked. Chronological 60/40 split (in-sample to 2026-07-10, holdout from 2026-07-13) plus a per-month table. Result: holdout worse than in-sample.
4. **Statistics.** Per-trade t-stat on net bps; daily-return t-stat; daily Sharpe (√252); block bootstrap of daily returns (block 5, 10,000 resamples, saved seed 7) with percentile intervals and P(≤ 0); profit factor; expectancy in $ and bps; max drawdown on daily closes; regime splits (day-level ATR percentile vs trailing 60 sessions; equal-weight basket direction); per-symbol and per-month attribution; cost stress ×1/×2/×3.
5. **Pitfall handling.** No look-ahead (all values as of bar close; daily history strictly prior sessions; the 15:00 anchor never moves); no survivorship issue within the fixed 8-name list (current mega-caps — noted as a selection bias of the universe itself); multiple testing disclosed via the trial log (4 variants) with flat sensitivity; no regime monoculture claim (one year only — the strongest remaining limitation); signal selection was pre-committed to the paper's predictor and the file's declared suggestion grid, not searched.
6. **Data-provenance finding (infrastructure-grade).** Stored bar **volume changes units on/after 2026-06-13**: per-day sums of stored 1-minute volume drop from 65–80% of the `daily` table's volume to ~3% (e.g., AAPL 2026-06-12: 30.4M vs 39.0M; 2026-06-15: 1.57M vs 45.9M). Prices are continuous and unaffected (390 bars/day throughout; bars-derived closes match `daily` closes). Consequences: (i) the DSL's `rvol()` — today's bar volume vs the `daily`-table average — degenerates to ≈ 0.03 after that date, so any rvol-gated entry silently stops firing (this is why the initial rvol variant produced zero trades after July); (ii) this affects the **live** system identically, since the broker computes rvol from the same tables. The repo's own research catalog already lists missing feed/adjustment provenance; this is a live instance of it. The volume filter was removed from the strategy (the paper does not condition on volume; the ATR-scaled move is itself the attention trigger) and the break is flagged here for repair.

---

## 6. Infrastructure & feature gap analysis

| Capability | Typical retail setup (ToS / TradingView / Python+yfinance/Alpaca SDK) | This repository |
|---|---|---|
| 1. Real-time data at required frequency | ToS/TV: streaming 1-min native. Python: Alpaca SDK websocket/REST minute bars | `services/broker/paper.py` polls Alpaca bars/news into Postgres; rule engine runs at each bar close. **Supported.** Gap found: no per-row feed/units provenance (§5.6) |
| 2. Automatic opening-range calculation | Native in all three | `or_high(n)/or_low(n)/close_at(n)` builtins; **supported** (used here as the 15:00 anchor) |
| 3. Break-level execution (stop vs limit) | Stop/stop-limit native | Entry is **marketable limit at next bar open** with 2-bar cancel; broker-side bracket for exits. No resting stop-entry orders — a documented difference vs ORB papers |
| 4. Real-time regime filtering (VIX/ATR gate) | TV/ToS trivial; Python trivial | `vol_rank(n,m)`, `atr_d`, `beta_d` builtins provide ATR/vol-regime gates. **No VIX series** — would need a market-input feed (DSL 0.2 market inputs are listed as future work in the research catalog) |
| 5. Position-sizing calculator | Manual/scripted | Deterministic: `core/engine.py` floor(equity × risk / risk-per-share), capped by `max_position`; **supported** |
| 6. Automated stop/target brackets | Native (OCO/bracket) | Broker-side brackets held at Alpaca, stop assumed first in backtests; **supported** |
| 7. End-of-day forced liquidation | Broker flat-by-close flags | `flatten_by` 15:55 risk control + `flatten_at` per strategy, clamped on early closes; **supported** |
| 8. Trade logging / performance tracking | CSV exports, TV strategy tester | Hash-chained audit log, per-trade decision records, trades/fills tables, equity history, dashboard Trade review; **supported, better than retail** |
| 9. Walk-forward backtesting | Manual in Python; TV limited | Backtest workspace: holdout replay, rolling fixed-rule windows, cost stress ×2/×3, seeded bootstrap, data fingerprints, durable runs (`docs/backtesting.md`). Formal walk-forward *fitting* and multiple-testing significance are listed as future work; this report's script adds a standalone chronological split + block bootstrap |
| — Cross-sectional ranking | pandas groupby | **Missing in DSL 0.1** (no rank/quantile builtin) — the paper's decile sort is approximated by an ATR-scaled threshold; adding `rank_xs(expr)` to `dsl/` is the precise gap to close |
| — Volume provenance | vendor-tagged feeds | **Missing**: bar volumes changed units on 2026-06-13 without any marker; add feed/units columns to `bars` (research catalog already anticipates this) |

---

## 7. Improvements beyond the academic baseline (ranked by expected edge per added degree of freedom)

1. **Execution cost reduction (no added parameters):** route the 15:55 exit as a limit order at/inside the bid instead of market, or participate in the closing auction (MOC) — the paper's effect is defined to 16:00 and its robustness check suggests 15:55 capture is fine; saving 1–2 bps/side moves the strategy from −3 to ≈ 0 bps/trade. Highest value, zero parameters. Requires executor changes (`execution.entry_order`/exit order types), not a strategy-file change.
2. **Widen the cross-section (no new parameters):** add ORCL, NFLX, AVGO and other high-attention large caps (daily history already stored) once intraday ingestion covers them; the anomaly is cross-sectional — more names ⇒ more days with a genuine extreme loser ⇒ the cap binds on better candidates.
3. **Repair volume provenance, then re-test an attention filter (1 parameter, previously tested):** rvol ≥ 1.0 is currently untestable; after fixing the units break, re-run the pre-registered grid rather than inventing new filters.
4. **Trade only high-vol regimes (1 parameter, NOT adopted):** descriptively +1.26 bps on high-vol days here; adopting it now would be in-sample selection. Pre-register `vol_rank(10,60) ≥ 0.5` and evaluate on *future* stored data only.
5. **Deprioritized:** time-stop variants (VCG's time-stop note: exit shape changes the tail, not the edge — consistent with my bracket barely binding); retest/pullback entries (discretionary, rejected); short side (no evidence — the paper's asymmetry finding).

---

## 8. Artifacts created (this session)

| File | Purpose | Verification |
|---|---|---|
| `strategies/eod_reversal_losers_long.v1.yaml` | Deployable strategy (DSL 0.1) | Validator: `ok: True, errors: [], warnings: []`, hash `22795fd3401d2cb7…` (command in §9) |
| `scripts/research_eod_reversal_backtest.py` | Standalone rerunnable backtest (stdlib + psycopg; reuses `dsl.evaluator.VOLUME_CUM` for identical rvol semantics) | Runs end-to-end; outputs below |
| `reports/eod_reversal_20261016_061710/` | Headline run: `trades.csv` (320), `daily.csv` (169), `summary.json`, `config.json`, `results.md` | numbers quoted in §4 |
| `reports/eod_reversal_sens_drop015/`, `reports/eod_reversal_sens_drop040/` | Pre-registered sensitivity runs | §4 |
| `reports/eod_reversal_20261016_061000/` | Abandoned rvol-filtered structure (trial log) | §4 |
| `reports/quant_research_2026-10-16.md` | This report | — |
| `reports/quant_research_2026-10-16.pdf` | Rendered PDF of this report | page-verified |

No existing files were modified; no secrets read or printed; no Docker, no orders.

---

## 9. Verification gates — evidence

- **Validator command & output:**
  `DATABASE_URL=postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_paper_utf8 .venv/bin/python -c "from common.config import load_risk; from dsl.validator import validate; v = validate(open('strategies/eod_reversal_losers_long.v1.yaml').read(), load_risk()); print(v.ok, v.errors, v.warnings, v.hash[:16])"`
  → `True [] [] 22795fd3401d2cb7`
- **Fast tests:** `.venv/bin/python -m pytest tests/test_dsl.py tests/test_dsl_features.py tests/test_dsl_open_builtins.py -q` → **56 passed**.
- **Backtest:** `.venv/bin/python scripts/research_eod_reversal_backtest.py --dsn postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_paper_utf8` → completes; artifacts saved; results match §4.
- **Parameter consistency YAML ↔ script ↔ report:** rod_minutes 330; window 15:29–15:30; flatten 15:55; drop_atr 0.25; stop 0.3×ATR_d(14); target 3R; risk 0.25%; max 3 positions/3 trades; min price $5; ADV $1B; costs 2+1 bps/side, $0 commission. Identical in all three.
- **Citations:** §1.1; two misattributions and two commercial items named; nothing fabricated.

---

## 10. Deploy instructions

- **Where the file lives:** `strategies/eod_reversal_losers_long.v1.yaml`. It enters the normal lifecycle at **Draft** (validator already passing).
- **How it was validated:** repo validator against the *effective* account risk controls (Postgres overrides over `config/risk.yaml`); 56 DSL unit tests; standalone backtest (§4–§5).
- **How to run the research backtest:** `python scripts/research_eod_reversal_backtest.py --dsn <paper DB DSN>` (defaults = the YAML; `--drop-atr`, `--rvol-min`, `--cost-stress`, `--bootstrap`, `--outdir` for variants). The dashboard **Backtesting** workspace can also replay the registered version with holdout + cost stress once imported.
- **How to approve for Alpaca paper:** dashboard → Strategies → select this version → review assumptions and the independent back-translation → approve the file's SHA-256; the registry starts it at `paper_manual`; orders then require per-trade approval unless the owner switches mode.
- **Recommendation: DO NOT approve now** (negative net expectancy OOS, §4). If approved anyway for observation, run it `paper_manual`, zero allocation expectation, and compare paper fills vs the 6 bps cost model before drawing any conclusion.
- **Before any real-money consideration (all must be true):** (i) execution improvement (§7.1) measured live at ≤ 3 bps round trip over ≥ 60 paper sessions; (ii) a pre-registered retest on ≥ 2 years of clean, provenance-tagged data showing net per-trade t ≥ 2 with holdout ≥ in-sample; (iii) the volume-units break repaired and rvol re-validated; (iv) risk controls at factory settings (the current paper account's 5% max risk / 100% max position are observation settings, not production settings); (v) owner review of this report's risk disclaimer.

---

## 11. Risk disclaimer

This research is for informational and educational purposes and is not investment advice or a solicitation to buy or sell any security. Intraday trading carries substantial risk of loss; simulated and historical results do not guarantee future performance; backtests are vulnerable to overfitting, regime change, and transaction-cost error. Any strategy must be validated out-of-sample and run on paper before real capital is considered.
