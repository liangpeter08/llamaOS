# Intraday Equity Strategy Literature — Evidence Verification Dossier

**Prepared:** 2026-10-16
**Scope:** Verification of named works + extraction of exact statistics from genuine peer-reviewed / working-paper literature, for five intraday strategy families. Audience: a skeptical quant who checks every citation.
**Tradeability filter applied throughout:** US equities / ETFs via Alpaca (no futures, no options, no leveraged-product shorting constraints beyond broker borrow).

---

## 0. How to read this dossier

Every claim below carries a traceable URL. Sources are tiered:

- **[PEER-REVIEWED]** = published in JFE / JF / RFS / Management Science / Finance Research Letters.
- **[WORKING PAPER]** = SSRN / arXiv / SFI Research Paper series, not yet peer-reviewed.
- **[SELL-SIDE / BLOG]** = bank note or vendor blog; labeled as such, never upgraded to evidence.
- **[TRADING BOOK]** = practitioner monograph (Crabel, Williams); folklore, not evidence.

"Authors' own backtest" vs "third-party replication" is flagged explicitly. Where a number could not be reproduced from an authoritative page, it is not stated.

---

## 1. Verification ledger — every named work

| # | Work as named by user | Verdict | What it actually is | URL / evidence |
|---|---|---|---|---|
| 1 | arXiv 2605.04004, "Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures" | **VERIFIED** (minor title correction) | Mathias Mesfin, "Structural Limits of OHLCV-Based Intraday **Momentum** Signals in MNQ Futures: A Systematic Falsification Study," submitted 5 May 2026, v3 15 Sep 2026, q-fin.TR. Single-author working paper, CC-BY 4.0. **Not peer-reviewed.** | https://arxiv.org/abs/2605.04004 ; DOI 10.48550/arXiv.2605.04004 |
| 2 | "Vortex Capital" 2026 study on long/short ORB asymmetry | **VERIFIED AS BLOG POST, not a study** | Vortex Capital Group (vortexcapitalgroup.com) blog post "ORB Statistical Framework: Why the Long-Short Asymmetry Has Inverted," 13 May 2026. Sample = **last 20 trading days on 9 high-beta names** (NVDA, AMD, AVGO, SOXL, MSTR, COIN, META, PLTR, TSLA). Long-ORB hit rate 30.6% baseline, 31.0% with VWAP+QQQ filter. This is a 20-day desk observation, not a peer-reviewed or SSRN working paper. | https://www.vortexcapitalgroup.com/insights/opening-range-breakout-a-statistical-framework-for-active-equity-day-traders |
| 3 | "Vortex Capital" "Off-Radar Reversion Illusion" VWAP paper | **VERIFIED AS BLOG POST, not a paper** | Vortex Capital Group, "The Off-Radar Reversion Illusion: The 73% Fade That's Really 35%," 8 Jul 2026. Desk research on 29 off-radar mid-caps + 8 mega-caps, ~85 sessions Mar–Jul 2026, 1-min SIP bars with measured NBBO. **Direction of finding is falsification of VWAP fades, not advocacy.** | https://www.vortexcapitalgroup.com/insights/the-off-radar-reversion-illusion-the-73-fade-thats-really-35 |
| 4 | "Boller et al., Duke 2025" factor-based intraday predictability | **VERIFIED** (author name correction: **Bollerslev**, not "Boller") | Aleti, S., Bollerslev, T., Siggaard, M. (2025). "Intraday Market Return Predictability Culled from the Factor Zoo." *Management Science*, Articles in Advance, Sep 2025, pp. 1–21, © INFORMS. **[PEER-REVIEWED]** | https://scholars.duke.edu/publication/1573580 ; working PDF http://public.econ.duke.edu/~boller/Papers/MS_2025.pdf |
| 5 | "Notre Dame paper on gamma hedging and intraday momentum" | **VERIFIED** | This is Baltussen, G., Da, Z., Lammers, S., Martens, M. (2021). "Hedging demand and market intraday momentum." *Journal of Financial Economics* 142, 407–427. Zhi Da is at Notre Dame (Mendoza). **[PEER-REVIEWED]** Companion Notre Dame working paper: Da et al., "Intraday Option Return: A Tale of Two Momentum." | https://www3.nd.edu/~zda/intramom.pdf ; https://academicweb.nd.edu/~zda/IntraOption.pdf ; Tinbergen record https://tinbergen.nl/publication/168044 |
| 6 | Handelshögskolan (Stockholm School of Economics) ORB thesis across volatility states | **PARTIALLY VERIFIED — institution misattributed** | The peer-reviewed ORB paper is Holmberg, U., Lönnbark, C., Lundström, C. (2013). "Assessing the profitability of intraday opening range breakout strategies." *Finance Research Letters* 10(1), 27–33. Authors are at **Umeå University** (Umeå Economic Studies 845), **not** the Stockholm School of Economics. Asset = **US crude oil futures 1983–2011**, not equities. No SSE/HHS ORB thesis on volatility states was found in searches of hhs.se, GU, or UU. | http://www.usbe.umu.se/digitalAssets/102/102002_ues845.pdf ; RePEc https://ideas.repec.org/a/eee/finl ec/v10y2013i1p27-33.html (indexed as FRL 10(1):27-33) |
| 7 | "SPY intraday momentum paper" RePEc 2024 | **VERIFIED** | Zarattini, C., Barbon, A., Aziz, A. "Beat the Market: An Effective Intraday Momentum Strategy for S&P 500 ETF (SPY)." Swiss Finance Institute Research Paper Series **RP 24-97** (RePEc:chf/rpseri/rp2497). **[WORKING PAPER]** Same research group as #Z below. | https://ideas.repec.org/p/chf/rpseri/rp2497.html |

### Named works searched and NOT found under the stated identity

- **"Boller" as a single author at Duke 2025** — no such author; the Duke intraday-factor paper is Aleti/Bollerslev/Siggaard. Flagging to prevent citation under the wrong name.
- **Any SSE (Handelshögskolan i Stockholm) ORB thesis** — searches of hhs.se, DiVA, GU, UU returned no such thesis. The closest peer-reviewed paper is Umeå, not SSE.
- **Kolannovic/Popper peer-reviewed 0DTE gamma paper** — JPM "Day Trading the 0DTE Market" / "Gamma and the Market" notes exist as **sell-side research notes** (labeled SELL-SIDE, not peer-reviewed). No Andrei et al. 0DTE intraday paper was found in arXiv/SSRN under that name. The closest peer-reviewed treatment remains Baltussen et al. 2021 (#5 above) and Barbon & Buraschi, "Gamma Fragility" (https://www.abarbon.com/assets/Barbon_Buraschi_2021_Gamma_Fragility.pdf).

---

## 2. Genuine core literature — exact statistics

### 2.1 Gao, Han, Li & Zhou (2018), "Market Intraday Momentum," *JFE* 129(2), 394–414 — **[PEER-REVIEWED]**

DOI: 10.1016/j.jfineco.2018.05.009. RePEc: https://ideas.repec.org/a/eee/jfinec/v129y2018i2p394-414.html. Author manuscript (SSRN 2440866): https://assets.super.so/e46b77e7-ee08-445e-b43f-4ffd88ae0a0e/files/ee7dac49-530b-4950-b5d0-e0b5eee08f2e.pdf

- **Sample:** SPY, 1 Feb 1993 – 31 Dec 2013. Half-hour bars.
- **Core regression** (last-half-hour return r₁₃ on first-half-hour return r₁, coefficients scaled ×100):
  - β(r₁) = 6.94, Newey–West **t = 4.08**, **in-sample R² = 1.6%**.
  - β(r₁₂, second-to-last half hour) = 11.8, t = 2.62, R² = 1.1%.
  - Joint R² = 2.6%.
- **Out-of-sample** (expanding window, initialized 1997): OOS R² = 1.4% (r₁ alone), 2.0% (joint).
- **Regime dependence (the headline caveat):**
  - Financial crisis only (Dec 2007–Jun 2009): β(r₁) = 13.6, R² = 4.1%; joint R² = 6.9%.
  - Ex-crisis: β(r₁) = 4.45, R² = 0.8%; joint R² = 1.1%.
  - First-half-hour volatility terciles (joint R²): low = 0.6%, medium = 1.0%, **high = 3.3%** (>5× low).
  - Volume terciles: low = 1.1%, medium = 2.3%, high = 3.1%.
  - NBER expansions: joint R² = 1.0%. NBER recessions: joint R² = 6.6%.
- **Economic value:** timing strategy trading only the last 30 min on sign of r₁ → 6.67% p.a., SD 6.19%, **Sharpe 1.08**, vs SPY buy-and-hold Sharpe 0.29 (6.04% p.a., SD 20.57%).
- **Long/short symmetry:** the paper is a *time-series* sign strategy (long if r₁>0, flat/short if r₁<0); not a cross-sectional long-short. Symmetry not separately tested.
- **Cost note:** the timing strategy trades **once per day** (only the last 30 min), so even a 5–10 bps round trip on SPY is small relative to the ~6.67% annualized gross. Authors do not report a net-of-cost Sharpe in the manuscript, but the cost burden is structurally low because of one-trade-per-day turnover.

### 2.2 Baltussen, Da, Lammers & Martens (2021), "Hedging demand and market intraday momentum," *JFE* 142, 407–427 — **[PEER-REVIEWED]**

Author manuscript (Notre Dame): https://www3.nd.edu/~zda/intramom.pdf. Tinbergen record: https://tinbergen.nl/publication/168044.

- **Sample:** >60 futures across equities, bonds, commodities, FX; **Dec 1974 – May 2020 (~45 years)**.
- **Finding:** last-30-min return is positively predicted by the rest-of-day return; predictability reverts over subsequent days.
- **Regression on US equity index futures:** r_overnight+first-half-hour (r_ONFH) predicts last-half-hour with **t = 6.46**; second-to-last half-hour (r_SLH) with **t = 4.82**; the middle-of-day block (r_M) also significant.
- **Mechanism (the paper's contribution):** gamma-hedging demand of option market makers (negative gamma → market makers must trade *with* the move into the close) + leveraged ETF rebalancing. Cross-asset evidence: momentum is strongest where gamma/LETF demand is largest.
- **Costs:** futures-based; authors note transaction costs are modest for the most liquid index futures. Not an equity-ETF tradeable strategy directly, but the *mechanism* predicts the same last-hour effect in SPY/QQQ.
- **Long/short:** symmetric — last-hour continuation occurs whether the day is up or down.

### 2.3 Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy for the U.S. Equity Market," SSRN 4729284 / SFI Research Paper 24-98 — **[WORKING PAPER, not peer-reviewed]**

PDF mirror (Wealth-Lab): https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf. Companion earlier paper "Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from ORB … vs Benchmark," SSRN 4416622: https://static1.squarespace.com/static/5983d931579fb366729580d8/t/643ed6765176b45506e41a01/1681839734183/SSRN-id4416622.pdf. Won Quantpedia 2024 Award (FSI-HSG Jahresbericht 2024, https://fsi.unisg.ch/fileadmin/user_upload/HSG_ROOT/Institut_FSI/Dokumente/Jahresberichte/HSG_FSI_Jahresbericht_2024_01.pdf).

**Authors' own backtest — "top 20 Stocks in Play" variant (SSRN 4729284):**
- Universe: top 20 stocks each morning ranked by opening relative volume ("Stocks in Play").
- Entry: 5-min opening-range breakout. Stop: 0.10×ATR. Exit: close at 16:00.
- Sample: 2016–2023. Starting capital $25,000.
- **Commission assumption: $0.0035/share (Interactive Brokers Pro-Tiered, as of 31 Dec 2023).** No slippage, no short-borrow cost, no market-impact modeled.
- Reported: total net performance **>1,600%**, **Sharpe 2.81**, annualized **alpha 36%** vs S&P 500 (which returned 198% over the same window).

**Companion QQQ-only variant (SSRN 4416622, 2016–2023):**
- $25,000 → $192,806 = **+675% net of commissions** vs QQQ buy-and-hold +169%.
- Annualized alpha **33% net of commissions**, p = 0.0025, β ≈ 0 (not statistically different from zero).
- **Annualized Sharpe 1.12**; annualized return 31%.
- 1,795 trades: **51% long / 49% short** (near-symmetric).
- **Win rate 24%**, average PnL = **0.13R per trade** (low hit rate, positive skew).
- TQQQ-leveraged variant: +1,484%; unconstrained-leverage ORB: +1,630%.
- Broker leverage cap (4× intraday) meant ~60% of trades ran at 40% below optimal size — authors themselves flag this as a real-world constraint.

**Third-party replication status:**
- QuantConnect forum thread 19456 ("Opening Range Break paper"): community members attempting to reproduce the TQQQ variant report that code does not reproduce out-of-the-box; one poster ran `ConstantFeeModel(0)` + `ConstantSlippageModel(0.01)` (https://www.quantconnect.com/forum/discussion/19456/opening-range-break-paper/p1).
- A separate embedded QuantConnect backtest cache (https://www.quantconnect.com/terminal/cache/embedded_backtest_08e2eee67980c93d972a27512d98def2.html) shows 87,181 orders and **$9.74M in total fees** — illustrative of what happens when a high-turnover ORB variant is run without the authors' selective universe filter; **not a verified replication of the paper's top-20 result**.
- **No independent peer-reviewed replication of the 2.81 Sharpe exists as of this dossier.** Treat the 2.81 as authors' own, cost-light, in-sample-universe-selection result.

### 2.4 Novy-Marx & Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs," *RFS* 29(1), 104–147 — **[PEER-REVIEWED]**

DOI: 10.1093/rfs/hhv063. NBER w20721: http://www.nber.org/papers/w20721.pdf. Penn State record: https://pure.psu.edu/en/publications/a-taxonomy-of-anomalies-and-their-trading-costs.

- 23 anomalies; effective-spread cost model by size bucket and turnover.
- **Low-turnover anomalies (one-sided turnover <50%/month):** round-trip costs often <10 bps/month; survive costs after a buy/hold-spread mitigation rule.
- **High-turnover anomalies (no cost mitigation):** costs routinely exceed **1% per month**, and exceed the gross anomaly premium — i.e., gross edge is entirely eaten by costs.
- Key mitigation: buy/hold spread (don't trade unless a stock moves more than one extra decile) cuts costs ~50% without sacrificing much premium.
- **Relevance to intraday:** any strategy that turns over a full portfolio more than once per day (e.g., a 15-min rebalanced ML strategy, or a VWAP fade on 20 names) is in the high-turnover bucket. Cost assumptions in the papers below must be judged against this benchmark: a 5 bps round-trip on SPY is ~1% per month at full daily turnover.

### 2.5 Heston, Korajczyk & Sadka (2010), "Intraday Patterns in the Cross-section of Stock Returns," *JF* 65(4), 1369–1407 — **[PEER-REVIEWED]**

arXiv preprint: https://arxiv.org/pdf/1005.3535. RePEc: https://ideas.repec.org/p/arx/papers/1005.3535.html.

- Return continuation at **half-hour intervals that are exact multiples of a trading day** (e.g., 10:00 today predicts 10:00 tomorrow) persists for **at least 40 trading days**.
- Short-term reversal (bid-ask bounce) also present, as expected.
- Volume, order imbalance, volatility, and bid-ask spreads exhibit the same intraday periodicity but **do not explain** the return pattern — i.e., it is not a microstructure artifact.
- Implication for intraday traders: the *time-of-day* effect is real and cross-sectional, but it is a slow-moving periodicity, not a tradeable 5-min signal.

### 2.6 Bogousslavsky (2021), "The Cross-Section of Intraday and Overnight Returns," *JFE* 141(1), 172–194 — **[PEER-REVIEWED]**

DOI: 10.1016/j.jfineco.2020.07.020. RePEc: https://ideas.repec.org/a/eee/jfinec/v141y2021i1p172-194.html. Boston College profile: https://www.bc.edu/bc-web/schools/carroll-school/faculty-research/faculty-directory/vincent-bogousslavsky.html.

- Cross-sectional return variation systematically differs between intraday and overnight periods.
- Theoretical mechanism (from the 2016 version, "Infrequent Rebalancing, Return Autocorrelation, and Seasonality"): institutions rebalance infrequently and at predictable times of day, generating the periodic autocorrelation documented by HKS 2010 and Gao et al. 2018.
- Implication: intraday momentum is a **structural** feature of slow-moving capital, not a fleeting arbitrage — which is why Baltussen et al. find it persists across 45 years and 60 futures.

### 2.7 Aleti, Bollerslev & Siggaard (2025), "Intraday Market Return Predictability Culled from the Factor Zoo," *Management Science* — **[PEER-REVIEWED]**

Author PDF: http://public.econ.duke.edu/~boller/Papers/MS_2025.pdf. Duke scholars record: https://scholars.duke.edu/publication/1573580.

- **Predictors:** 218+ characteristic-sorted factor portfolios (Jensen et al. 2023; Chen & Zimmermann 2022; Aleti 2023).
- **Forecast target:** 15-min Fama-French market return. Sample 2004–2020.
- **Models:** ridge, LASSO, elastic net, neural net, random forest, gradient boosting, and an ensemble.
- **Out-of-sample 15-min R²:** best (Ensemble, All regressors, All observations) = **0.212%**. For reference, the Ross (2004) bound on 15-min R² with γ=5 is 0.00777% — the ensemble is ~27× that bound, implying the predictability is not risk-premium variation but slow information incorporation.
- Diebold–Mariano t-stats: Ensemble beats historical-mean benchmark at t = −4.315, beats AR(1) at t = −2.544 (Table 3 of paper).
- **Trading strategy (SPY, after transaction costs):** annualized intraday **Sharpe 1.37**, annualized Fama-French-6-factor **alpha 20.83%**. SPY buy-and-hold over the same OOS window has intraday Sharpe 0.09.
- **Cost assumptions:** time-weighted average spreads 2004–2020 — SPY 0.0067%, QQQ 0.0167%, IVV 0.0156%, IWM 0.008%. Shorting stress-tested at 2% (8× the actual IB 0.25% for highly liquid names).
- **Regime:** most abnormal returns accrue in **high-economic-uncertainty periods**; key factors are tail-risk and liquidity clusters.
- **Tradeability:** SPY/QQQ/IWM/IVV — all Alpaca-eligible. But the strategy requires rebalancing every 15 minutes and a 218-factor data pipeline; turnover is the cost risk flagged by Novy-Marx & Velikov.

### 2.8 Mesfin (2026), arXiv 2605.04004 — **[WORKING PAPER, falsification result]**

https://arxiv.org/abs/2605.04004.

- 14 OHLCV signal families on MNQ (Micro E-mini Nasdaq) 5-min bars, **947 RTH days, 2021–2025**, expanding-window walk-forward.
- Five pass criteria: (i) OOS net-return t ≥ 2.0; (ii) ≥30 trades per OOS fold; (iii) positive net return after instrument-appropriate friction; (iv) consistent direction across 2023, 2024, 2025; (v) permutation p < 0.001 where applicable.
- **Result: none of the 14 passed all five.**
- 11 families fail on friction: gross return 0.07–1.50 points vs a **2.0-point friction floor** for MNQ.
- 3 clear friction but fail elsewhere:
  - Opening Range Breakout **long**: OOS t = 0.88, year-unstable.
  - VVG classifier reversal: t = 1.26, year-unstable.
  - Gap-continuation **short**: gross +16.53 points, t = 1.46, fails year stability and per-fold sample minimum.
- Two **positive controls** (from a separate research program) do pass: RTH Confluence Signal (OOS t = 3.11, mean net +11.82 pts, N = 196) and London Session Signal B (OOS t = 4.30, mean net +4.09 pts, N = 247, p = 0.000025).
- **Critical caveat:** MNQ is a futures contract; the friction floor (2.0 points ≈ $100/contract) reflects futures execution, not Alpaca equity costs. The result says "generic OHLCV intraday signals on index futures don't survive realistic costs," not that the same signals fail on SPY.

### 2.9 Holmberg, Lönnbark & Lundström (2013), *Finance Research Letters* 10(1), 27–33 — **[PEER-REVIEWED]**

http://www.usbe.umu.se/digitalAssets/102/102002_ues845.pdf.

- Asset: **US crude oil futures**, 30 Mar 1983 – 26 Jan 2011 (6,976 daily observations).
- Method: bootstrap test (Brock et al. 1992) on ORB thresholds derived from normally distributed opening-close returns.
- **Explicit assumption: zero spread, zero commissions, perfect fill at threshold.** Authors state "real-life trading produce slightly different results."
- Finding: ORB long and short produce significantly positive average returns; as the threshold tightens further into the tail, both success rate and average return increase.
- **Relevance:** peer-reviewed evidence that ORB gross edge exists in 1983–2011 oil futures — but (a) it is futures, not equities; (b) zero-cost assumption means net-of-cost profitability is unproven; (c) 2011 end-date predates the rise of ETF intraday liquidity and HFT.

### 2.10 Crabel (1990) and Larry Williams ORB — **[TRADING BOOK, not evidence]**

- Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout*, Traders Press, 1990. Origin of NR7 / contraction-expansion / ORB. Publisher/history: https://crabel.com/who-we-are ; StockCharts summary: https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7.
- Larry Williams' ORB percentage (typically 0.5% / 1% of prior range) is practitioner folklore; no peer-reviewed Williams ORB paper was located.
- These are the origin stories cited by Zarattini et al., not evidence themselves.

### 2.11 VWAP mean-reversion literature

- **No peer-reviewed paper establishes a tradeable standalone VWAP-fade edge on US equities.** The genuine microstructure literature supports short-term reversal at the open (bid-ask bounce, HKS 2010) and inventory effects (Madhavan-Richardson-Roomans) but does not validate "fade ≥2σ from VWAP."
- The strongest *recent* evidence on VWAP fades is actually the **Vortex falsification blog** (#3 in ledger): on 29 off-radar mid-caps, naive "reversion" to VWAP occurs 72.6% of days by close-of-day measure, but the tradable rate (reach VWAP before a 1σ stop) is only **35.2%**. Gross expectancy +1.7 bps/trade (n = 833); net of measured half-spread, **−24 bps/trade**. Off-radar median half-spread 15.0 bps vs mega-cap 0.82 bps (18×). 76% of "reversion" decomposes as VWAP drifting up to meet a stationary price, not price returning to VWAP.
- Pomegra wiki (https://pomegra.io/wiki/vwap-reversion-intraday/) summarizes the academic consensus: VWAP reversion is a benchmark-anchored execution effect, not a standalone alpha.

---

## 3. Five-family synthesis — five-dimension assessment

Legend for dimensions: **(1)** statistical significance; **(2)** transaction-cost survivability; **(3)** regime dependency; **(4)** sample size / robustness; **(5)** long-vs-short symmetry.

### Family A — Opening Range Breakout (ORB)

| Dimension | Assessment |
|---|---|
| (1) Significance | Zarattini/Barbon/Aziz (working paper): top-20 stocks-in-play Sharpe 2.81, QQQ-only alpha 33% p.a. (p=0.0025). Holmberg et al. (peer-reviewed, oil futures 1983–2011): bootstrap-significant gross returns. Mesfin (arXiv 2026): MNQ ORB long OOS t = 0.88 — **fails**. Mixed direction. |
| (2) Cost survivability | Zarattini uses $0.0035/share commission only, no slippage/borrow. Top-20 "stocks in play" are precisely the names where slippage and HTB borrow are worst. QQQ-only variant (Sharpe 1.12) is the cost-honest number. Holmberg assumes zero costs. |
| (3) Regime dependency | Zarattini sample 2016–2023 includes two bear markets; authors claim robustness, but the high-vol 2020 and meme-stock 2021 years likely carry much of the edge. Vortex 20-day blog: long-ORB hit rate only 30.6% in the recent tape. |
| (4) Sample / robustness | Zarattini: 8 years, 1,795 QQQ trades; Holmberg: 28 years, oil futures; Mesfin: 4 years, MNQ, 14 families. No independent peer-reviewed replication of the 2.81 Sharpe. |
| (5) Long/short symmetry | QQQ variant: 51% long / 49% short — near-symmetric. Vortex blog claims the asymmetry has "inverted" recently (long hit rate ~31%), but on a 20-day sample. |

**Key numbers:** QQQ ORB Sharpe 1.12, alpha 33% p.a. (p=0.0025), win rate 24%, 0.13R/trade; top-20 stocks-in-play Sharpe 2.81 (authors' own, cost-light); MNQ ORB long OOS t=0.88 (falsified).

### Family B — Intraday momentum (first/last half-hour; cross-session; gamma hedging)

| Dimension | Assessment |
|---|---|
| (1) Significance | Gao et al. 2018: r₁ → r₁₃ t = 4.08, R² = 1.6%, OOS R² 1.4%. Baltussen et al. 2021: r_ONFH t = 6.46 across 60 futures. HKS 2010: 40-day periodic continuation. Strongest peer-reviewed significance of any family. |
| (2) Cost survivability | Gao timing strategy trades once per day (last 30 min only); SPY round-trip ~1–2 bps. Net Sharpe likely still >0.8. Baltussen futures: liquid index futures costs low. |
| (3) Regime dependency | **Strongly.** Gao: high-vol tercile R² 3.3% vs low 0.6%; recession R² 6.6% vs expansion 1.0%; crisis R² 6.9%. The edge is a volatility/uncertainty phenomenon. |
| (4) Sample / robustness | Gao: SPY 1993–2013 (21 years), 10 other ETFs. Baltussen: 60 futures, 1974–2020 (46 years). APAC out-of-sample evidence (Monash working paper). The most robust sample base. |
| (5) Long/short symmetry | Time-series sign strategy; symmetric by construction. Baltussen shows last-hour continuation in both up- and down-days. |

**Key numbers:** t = 4.08 (Gao), t = 6.46 (Baltussen r_ONFH); in-sample R² 1.6%, OOS R² 1.4%; timing Sharpe 1.08 vs B&H 0.29; recession R² 6.6% vs expansion 1.0%.

### Family C — VWAP mean reversion vs trend

| Dimension | Assessment |
|---|---|
| (1) Significance | No peer-reviewed standalone VWAP-fade edge. HKS 2010 / Bogousslavsky document reversal at the open but not a ≥2σ VWAP fade. Vortex blog: naive reversion 72.6%, tradable 35.2%, gross +1.7 bps/trade. |
| (2) Cost survivability | Net of measured spread: **−24 bps/trade** off-radar. In mega-caps, gross ~0 so it's a fair fight you lose slowly. Round-trip spread on off-radar names 30 bps vs gross edge 2 bps. |
| (3) Regime dependency | Perversely, the more extreme the stretch (≥1.5σ → ≥3σ), the *lower* the tradable hit rate (39% → 24%). |
| (4) Sample / robustness | Vortex: 29 mid-caps, 8 mega-caps, ~85 sessions Mar–Jul 2026, n = 833 fades. Short but microstructure-controlled. No academic replication. |
| (5) Long/short symmetry | Symmetric — both stretched-above and stretched-below VWAP fades fail symmetrically. |

**Key numbers:** naive reversion 72.6% vs tradable 35.2%; gross +1.7 bps, net −24 bps/trade; off-radar half-spread 15.0 bps vs mega 0.82 bps; 76% of "reversion" = VWAP drifting to price.

### Family D — OHLCV index/futures signals (incl. MNQ falsification)

| Dimension | Assessment |
|---|---|
| (1) Significance | Mesfin 2026: 14 signal families on MNQ, **none** pass all five walk-forward criteria. ORB long t=0.88, VVG reversal t=1.26, gap-cont short t=1.46. Two positive controls (RTH Confluence t=3.11, London Signal B t=4.30) confirm the framework detects real edge when present. |
| (2) Cost survivability | 11 of 14 families produce gross 0.07–1.50 MNQ points vs a 2.0-point friction floor. The friction floor is the binding constraint. |
| (3) Regime dependency | Failure mode: direction inconsistent across 2023/2024/2025 years for the three near-misses. |
| (4) Sample / robustness | 947 RTH days, 5-min bars, 2021–2025. Single asset (MNQ), single author, not peer-reviewed. But the walk-forward protocol is stricter than most published backtests. |
| (5) Long/short symmetry | Gap-continuation short nearly passes (gross +16.53 pts) while ORB long fails (t=0.88) — asymmetry exists, but on the short side the sample is too small (N=22). |

**Key numbers:** 0/14 signals pass; friction floor 2.0 MNQ points; ORB long OOS t=0.88; positive controls t=3.11 and t=4.30.

### Family E — Factor-based intraday predictability (ML on the factor zoo)

| Dimension | Assessment |
|---|---|
| (1) Significance | Aleti/Bollerslev/Siggaard 2025: OOS 15-min R² = 0.212% (vs Ross bound 0.00777%); DM t = −4.315 vs historical mean. After-cost SPY strategy Sharpe 1.37, FF6 alpha 20.83%. Peer-reviewed in *Management Science*. |
| (2) Cost survivability | Uses real measured SPY/QQQ/IWM spreads (0.67–1.67 bps), stress-tests shorting at 2% (8× actual). Turnover every 15 min is the Novy-Marx-Velikov risk, but the edge per 15-min bar (~0.212% R² on ~0.05% typical 15-min move) is large enough to clear it. |
| (3) Regime dependency | Most alpha accrues in high-economic-uncertainty periods; key factors are tail-risk and liquidity clusters. Same regime profile as Family B, which is consistent. |
| (4) Sample / robustness | 2004–2020, 15-min bars, 218 factors, multiple ML models; ensemble beats individual models. OOS protocol explicit. |
| (5) Long/short symmetry | Time-series market-timing (SPY long/flat), not cross-sectional long-short. |

**Key numbers:** OOS R² 0.212%; after-cost Sharpe 1.37; FF6 alpha 20.83% p.a.; SPY intraday B&H Sharpe 0.09; SPY spread 0.67 bps.

---

## 4. Ranked recommendation — after-cost evidence on Alpaca-tradeable US equities/ETFs

### Ranking

1. **Family E — Factor-zoo ML intraday timing on SPY/QQQ** (Aleti/Bollerslev/Siggaard 2025, *Management Science*).
2. **Family B — First/last-half-hour intraday momentum on SPY/QQQ** (Gao et al. 2018 *JFE*; Baltussen et al. 2021 *JFE*).
3. **Family A — Selective ORB on liquid RVOL "stocks in play" / QQQ** (Zarattini/Barbon/Aziz SSRN 4729284, working paper; Holmberg et al. 2013 peer-reviewed but oil futures).
4. **Family D — Generic OHLCV index/futures signals** (Mesfin 2026 arXiv: falsified; only idiosyncratic positive controls survive).
5. **Family C — VWAP fade / mean reversion** (no peer-reviewed edge; actively falsified on measured-spread data).

### Justification (one paragraph)

On an Alpaca account trading US ETFs (no futures, no options), the strongest after-cost evidence comes from **Family E**: Aleti, Bollerslev & Siggaard (2025) report a peer-reviewed, out-of-sample, after-spread annualized Sharpe of **1.37** and Fama-French-6-factor alpha of **20.83%** trading SPY on 15-minute forecasts built from 218 factor portfolios — using actual measured spreads (SPY 0.67 bps) and stress-tested short costs. The economic mechanism is slow-moving capital and gradual information incorporation, which is the same microstructure story that makes **Family B** work: Gao et al. (2018) show the first half-hour predicts the last half-hour at **t = 4.08** with OOS R² 1.4%, and Baltussen et al. (2021) attribute the effect to options-market-maker gamma hedging (r_ONFH **t = 6.46** across 60 futures, 1974–2020). Family B is the lower-infrastructure fallback — a once-per-day last-30-minute trade on SPY/QQQ with a timing Sharpe of **1.08** — but its edge is explicitly concentrated in high-volatility / recession regimes (R² 6.6% vs 1.0% in expansions). **Family A** (ORB) has eye-popping authors' numbers (Sharpe 2.81 on top-20 stocks-in-play) but the cost assumptions are commission-only and the names selected are exactly where slippage and borrow are worst; the cost-honest QQQ-only Sharpe is **1.12**, and the peer-reviewed futures evidence (Holmberg 2013) assumes zero costs while the recent arXiv falsification (Mesfin 2026) has MNQ ORB long at OOS **t = 0.88**. **Families C and D should not be traded as generic rules**: VWAP fades net to **−24 bps/trade** once measured spreads are charged (Vortex 2026), and 14 generic OHLCV signal families on MNQ fail walk-forward after a 2.0-point friction floor. The microstructural unifier: an intraday edge is only harvestable on names where the round-trip spread is a small fraction of the move being captured — which on Alpaca means SPY/QQQ/IWM (families E and B), not off-radar mid-caps (family C) or futures-only OHLCV patterns (family D).

---

## 5. Source URL index

- arXiv 2605.04004: https://arxiv.org/abs/2605.04004
- Vortex ORB asymmetry: https://www.vortexcapitalgroup.com/insights/opening-range-breakout-a-statistical-framework-for-active-equity-day-traders
- Vortex Off-Radar Reversion: https://www.vortexcapitalgroup.com/insights/the-off-radar-reversion-illusion-the-73-fade-thats-really-35
- Aleti/Bollerslev/Siggaard Duke: https://scholars.duke.edu/publication/1573580 and http://public.econ.duke.edu/~boller/Papers/MS_2025.pdf
- Baltussen et al. Notre Dame manuscript: https://www3.nd.edu/~zda/intramom.pdf
- Da "Intraday Option Return": https://academicweb.nd.edu/~zda/IntraOption.pdf
- Holmberg et al. Umeå: http://www.usbe.umu.se/digitalAssets/102/102002_ues845.pdf
- SPY intraday momentum SFI RP24-97: https://ideas.repec.org/p/chf/rpseri/rp2497.html
- Gao et al. JFE 2018 RePEc: https://ideas.repec.org/a/eee/jfinec/v129y2018i2p394-414.html
- Gao et al. manuscript: https://assets.super.so/e46b77e7-ee08-445e-b43f-4ffd88ae0a0e/files/ee7dac49-530b-4950-b5d0-e0b5eee08f2e.pdf
- Zarattini/Barbon/Aziz SSRN 4729284 PDF: https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf
- Zarattini/Aziz SSRN 4416622 QQQ ORB: https://static1.squarespace.com/static/5983d931579fb366729580d8/t/643ed6765176b45506e41a01/1681839734183/SSRN-id4416622.pdf
- Quantpedia award FSI-HSG: https://fsi.unisg.ch/fileadmin/user_upload/HSG_ROOT/Institut_FSI/Dokumente/Jahresberichte/HSG_FSI_Jahresbericht_2024_01.pdf
- Novy-Marx/Velikov NBER w20721: http://www.nber.org/papers/w20721.pdf
- HKS 2010 arXiv: https://arxiv.org/pdf/1005.3535
- Bogousslavsky 2021 RePEc: https://ideas.repec.org/a/eee/jfinec/v141y2021i1p172-194.html
- Barbon/Buraschi Gamma Fragility: https://www.abarbon.com/assets/Barbon_Buraschi_2021_Gamma_Fragility.pdf
- Crabel Capital history: https://crabel.com/who-we-are
- Pomegra VWAP reversion: https://pomegra.io/wiki/vwap-reversion-intraday/
- QuantConnect ORB replication thread: https://www.quantconnect.com/forum/discussion/19456/opening-range-break-paper/p1

---

*End of dossier. Every numeric claim above is traceable to a URL in §5. Where a number is described as "authors' own," it has not been independently replicated. Where a source is labeled blog / sell-side / trading book, it has not been upgraded to peer-reviewed evidence.*
