# Algorithmic Trading: Learning Guide for llamaOS

**Goal of this guide:** make you a *shallow expert*. You should be able to name every major idea, know why it matters and what usually goes wrong, and know where to read more. Each section ends with **Go deeper** links.

**Who it's for:** someone building an event-driven, news-reading, LLM-assisted intraday system for US equities (llamaOS on Alpaca) who wants to know what is realistic before risking money.

---

## Contents

0. [The honest answer first](#0-the-honest-answer-first)
1. [The critical challenges (ranked)](#1-the-critical-challenges-ranked)
2. [Foundations: what "edge" means](#2-foundations-what-edge-means)
3. [Market microstructure: where the trades happen](#3-market-microstructure-where-the-trades-happen)
4. [Event-driven and news trading](#4-event-driven-and-news-trading)
5. [LLMs in trading: what they can and can't do](#5-llms-in-trading-what-they-can-and-cant-do)
6. [Backtesting and research methodology](#6-backtesting-and-research-methodology)
7. [Statistics you need to not fool yourself](#7-statistics-you-need-to-not-fool-yourself)
8. [Transaction costs and execution](#8-transaction-costs-and-execution)
9. [Risk management and position sizing](#9-risk-management-and-position-sizing)
10. [Operations, infrastructure and failure modes](#10-operations-infrastructure-and-failure-modes)
11. [Regulation, accounts and tax (US retail)](#11-regulation-accounts-and-tax-us-retail)
12. [Glossary (quick reference)](#12-glossary-quick-reference)
13. [Core reading list](#13-core-reading-list)
14. [Suggested learning path](#14-suggested-learning-path)
15. [Mapping to llamaOS: questions your system must answer](#15-mapping-to-llamaos-questions-your-system-must-answer)

---

## 0. The honest answer first

Your premise is: *"algo trading with Claude should generate quite feasible short-lived, event-driven edge."* That is partly right. Here is the part that holds up and the part that doesn't.

**What doesn't hold up: being fast on news.**
When a headline crosses the wire, professional firms with co-located servers and direct data feeds react in **microseconds to milliseconds**. Your path is: news vendor → Alpaca → your server → LLM call (often 1–10+ seconds) → order → exchange. By the time you act on the *obvious* reading of a headline ("earnings beat → buy"), the first, easy part of the move is usually priced in. You cannot win a speed race, and an LLM makes you slower, not faster.

**What does hold up: being better at reading the news.**
Academic research finds that prices often **under-react** to news that is complex, ambiguous, stale-looking, about small firms, or that needs domain reasoning (second-order effects, supply-chain links, nuanced guidance changes). The drift after such news can last minutes to days. That is where LLMs genuinely add something: they can interpret text at scale and with nuance that keyword models can't. Several papers (Section 5) find measurable predictive power from LLM-scored news, concentrated in smaller and less-followed stocks and shrinking over time as more people use the technique.

**The sober framing:**
- An edge, if you find one, will be **small** (a few basis points per trade, after costs), **noisy** (many losing days), and **decaying** (others find it too).
- The most likely result of a first system is that it **looks great in backtest and loses money live**. Most of this guide is about why, and how to tell real edge from noise *before* you pay for the lesson.
- Retail day traders lose money on average. Large studies (Barber et al. on Taiwan; Chague et al. on Brazil) find that the vast majority of persistent day traders lose. Automation removes emotional errors but not the underlying economics.
- The realistic win for a small operator is **capacity**: strategies too small for a fund to bother with (illiquid names, small position sizes) where the big players aren't competing.

> **Rule of thumb:** If you can't explain *who is on the other side of your trade and why they are willing to lose to you*, you don't yet have an edge, just a pattern.

**Go deeper**
- Grossman & Stiglitz (1980), *On the Impossibility of Informationally Efficient Markets*: why some edge must exist for anyone to bother collecting information. [JSTOR](https://www.jstor.org/stable/1805228)
- Chague, De-Losso & Giovannetti (2019), *Day Trading for a Living?* [SSRN 3423101](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101)
- Barber, Lee, Liu, Odean (2014), *The cross-section of speculator skill: Evidence from day trading*. [Google Scholar](https://scholar.google.com/scholar?q=The+cross-section+of+speculator+skill+evidence+from+day+trading)
- Ernest Chan, *Quantitative Trading* (2nd ed., 2021), chapters 1–2: a candid practitioner's view of what a small trader can do.

---

## 1. The critical challenges (ranked)

These are the problems that actually kill strategies, roughly from most to least common.

| # | Challenge | What it looks like | Why it's hard |
|---|---|---|---|
| 1 | **Overfitting / data snooping** | Backtest Sharpe of 3, live Sharpe of −0.5 | You (and Claude) tried many ideas; the best one is best partly by luck. The more you search, the more luck you find. |
| 2 | **Look-ahead bias** (incl. LLM training contamination) | Backtest "knows" things it couldn't have known at the time | Timestamps, revised data, survivorship, *and the LLM already read about what happened after the event*. |
| 3 | **Transaction costs and slippage** | Gross edge 8 bps, costs 10 bps → net loser | Spreads, market impact, partial fills and adverse selection are all larger than people assume, especially at news time. |
| 4 | **Latency and adverse selection** | Your fills are systematically at worse prices | When news hits, the people filling your orders know more or are faster. Fast fills often mean you were wrong. |
| 5 | **Non-stationarity / regime change** | Strategy works for 6 months, then stops | Markets change; edges decay as they get crowded (McLean & Pontiff: published anomalies lose ~1/3 to 1/2 of returns after publication). |
| 6 | **Small sample sizes** | "It won 14 of 20 trades!" | Event strategies produce few trades. 20 trades says almost nothing statistically. |
| 7 | **Data quality** | Wrong prices, missing news, bad timestamps | Free/cheap feeds (e.g. IEX-only data) see a small slice of volume; news timestamps can be vendor-received time, not publish time. |
| 8 | **Operational risk** | A bug sends 1,000 orders | Knight Capital lost $440M in 45 minutes from a deployment error. Your kill switch and guards matter more than your alpha. |
| 9 | **Risk of ruin / sizing** | One bad day wipes out months | Fat tails, gaps and halts; stop-losses don't fill at the stop price in a gap. |
| 10 | **Behavioural risk (you)** | Overriding the system after a loss, tweaking after every bad day | Constant re-tuning is just overfitting in slow motion. |

**Go deeper**
- Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*: the single best short paper on why backtests lie. [SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659)
- McLean & Pontiff (2016), *Does Academic Research Destroy Stock Return Predictability?* [SSRN 2156623](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623)
- SEC order on Knight Capital (2013). [sec.gov PDF](https://www.sec.gov/litigation/admin/2013/34-70694.pdf)

---

## 2. Foundations: what "edge" means

### Key concepts

- **Return, excess return, alpha, beta.** *Return* is what you made. *Beta* is the part explained by the market moving (if SPY rose 1% and your longs rose 1%, that's beta, not skill). *Alpha* is what's left after removing exposure to the market and known factors. A news strategy that is "always long" on good news in a bull market may be mostly beta.
- **Expected value per trade.** `EV = P(win) × avg_win − P(loss) × avg_loss − costs`. Win rate alone means nothing; a 70% win rate with small wins and big losses can be a loser.
- **Sharpe ratio.** Mean excess return ÷ standard deviation of returns, usually annualised (×√252 for daily). Rough sense: <0.5 weak, ~1 good, >2 rare for a real, scalable strategy. A backtest Sharpe >3 is more often a bug or overfit than a discovery.
- **Sortino, Calmar, max drawdown.** Variants that penalise downside or the worst peak-to-trough loss. *Drawdown duration* (how long you're underwater) matters psychologically as much as depth.
- **Information ratio and the Fundamental Law of Active Management.** `IR ≈ IC × √breadth`. *IC* (information coefficient) is the correlation between your forecast and the outcome; *breadth* is the number of independent bets. A tiny IC (0.02–0.05 is typical and good) can produce a decent IR with many independent bets. This is why news strategies need **many** trades: each one is weak.
- **Efficient Market Hypothesis (EMH).** Weak/semi-strong/strong forms. Markets are *mostly* efficient for public information in large stocks; inefficiencies live at the edges (small caps, complex info, limits to arbitrage).
- **Limits to arbitrage.** Mispricings persist because exploiting them is costly or risky (short-sale constraints, capital limits, noise-trader risk). An edge that exists is usually one that's *hard* to exploit.
- **Factors.** Systematic return drivers: market, size, value, momentum, quality, low volatility, short-term reversal. Your strategy's returns might just be a factor in disguise (e.g. buying small caps after news = size + momentum exposure).
- **Alpha decay.** How fast your signal's predictive power fades after the event (seconds? hours? days?). This sets your holding period.
- **Capacity.** How much money a strategy can trade before its own costs eat the edge. Small capacity is a *feature* for you: big funds won't compete.

**Go deeper**
- Grinold & Kahn, *Active Portfolio Management* (2nd ed.): source of the Fundamental Law. Dense; read the first few chapters.
- Fama (1970), *Efficient Capital Markets: A Review of Theory and Empirical Work*. [JSTOR](https://www.jstor.org/stable/2325486)
- Shleifer & Vishny (1997), *The Limits of Arbitrage*. [Google Scholar](https://scholar.google.com/scholar?q=Shleifer+Vishny+1997+The+Limits+of+Arbitrage)
- Antti Ilmanen, *Expected Returns* (2011): a broad map of where returns come from.
- Andrew Lo (2002), *The Statistics of Sharpe Ratios*. [doi:10.2469/faj.v58.n4.2453](https://doi.org/10.2469/faj.v58.n4.2453)
- Rishi Narang, *Inside the Black Box* (3rd ed.): the best non-mathematical overview of how quant funds are structured (alpha model, risk model, cost model, execution).

---

## 3. Market microstructure: where the trades happen

You can't model costs or news reactions without knowing how trades actually happen.

### Key concepts

- **Order book (limit order book, LOB).** Resting buy orders (bids) and sell orders (asks) at each price. **Best bid / best ask** = top of book; the difference is the **bid-ask spread**.
- **Market vs limit orders.** Market orders take liquidity and pay the spread; limit orders provide liquidity, may not fill, and get filled most often exactly when you'd rather they didn't (adverse selection).
- **Liquidity, depth, market impact.** How much you can trade without moving the price. Impact grows roughly with the **square root of size relative to daily volume** (the "square-root law").
- **Adverse selection / informed flow.** Market makers widen spreads when they suspect informed traders. At news time spreads widen sharply, so the cost of trading peaks exactly when you want to trade.
- **US market structure.** ~16 lit exchanges plus dark pools and wholesalers. **Reg NMS** requires routing to the best displayed price (**NBBO**). Retail orders are often filled by wholesalers via **payment for order flow (PFOF)**.
- **SIP vs direct feeds.** The **SIP** is the consolidated public feed; direct exchange feeds are faster and used by HFTs. **Alpaca's free data tier uses IEX only** (a single exchange with a small share of volume), so prices and volumes can differ from the true consolidated market. For research and cost modelling you want SIP-quality data.
- **Trading sessions.** Pre-market (4:00–9:30 ET), regular (9:30–16:00), after-hours (16:00–20:00). Much news (earnings!) lands outside regular hours, when liquidity is thin and spreads are wide. The **opening auction** and **closing auction** are special liquidity events.
- **Halts and LULD.** **Limit Up-Limit Down** bands pause trading in a stock that moves too fast; news-pending halts are common around big announcements. Your system must handle "can't trade right now".
- **Gaps.** Price jumps between sessions or across halts. A stop-loss at $10 fills at whatever the first trade is, maybe $8.
- **Short selling.** Requires a borrow (locate); hard-to-borrow stocks have fees; the **SSR** (short-sale restriction, Rule 201) kicks in after a 10% drop and limits shorting to upticks.
- **HFT and market making.** Speed-based strategies that profit from latency arbitrage and spread capture. You won't compete with them; you should understand that they're the counterparty to many of your fills.

**Go deeper**
- Larry Harris, *Trading and Exchanges: Market Microstructure for Practitioners* (2003): the classic. If you read one microstructure book, read this.
- Bouchaud, Bonart, Donier & Gould, *Trades, Quotes and Prices* (2018): modern, more mathematical; covers the square-root impact law.
- Kyle (1985), *Continuous Auctions and Insider Trading*: the foundational model of informed trading. [JSTOR](https://www.jstor.org/stable/1913210)
- Budish, Cramton & Shim (2015), *The High-Frequency Trading Arms Race*. [doi:10.1093/qje/qjv027](https://doi.org/10.1093/qje/qjv027)
- Michael Lewis, *Flash Boys* (2014): a readable (and one-sided) story of US market structure and HFT.
- Alpaca market data docs (IEX vs SIP). [docs.alpaca.markets](https://docs.alpaca.markets/docs/about-market-data-api)
- SEC investor bulletin on LULD and trading halts. [investor.gov](https://www.investor.gov/introduction-investing/investing-basics/glossary/limit-uplimit-down)

---

## 4. Event-driven and news trading

This is your core discipline.

### Key concepts

- **Event study.** The standard method: align many events at t=0, measure **abnormal returns** (return minus expected return, e.g. minus market or sector), and average them into **CAR** (cumulative abnormal return). Your Idea Lab already does this. Learn the method properly; it's the backbone of everything below.
- **Event taxonomy.** Different events behave differently:
  - *Scheduled*: earnings, guidance, FDA PDUFA dates, macro releases (CPI, FOMC). The market knows *when*, so it's pre-positioned; the surprise vs expectation is what matters.
  - *Unscheduled*: M&A, FDA surprises, lawsuits, CEO departures, contracts, short reports, analyst up/downgrades. Bigger surprise, but noisier and often halted.
  - *Macro*: affects everything at once (high correlation; your "diversified" news bets all move together).
- **Surprise vs expectation.** "Revenue up 20%" is meaningless without consensus. A beat that's smaller than the whisper number can fall. *Standardised unexpected earnings (SUE)* is the classic measure.
- **Under-reaction and drift.** The best-documented news anomaly. **Post-earnings announcement drift (PEAD)**: prices keep drifting in the direction of the surprise for days to weeks. Similar drift appears after other news, especially in small, less-covered stocks and for complex or soft information.
- **Over-reaction and reversal.** Price moves on *no* identifiable news tend to reverse; moves on real news tend to continue (Chan 2003). Attention-grabbing, retail-heavy moves often overshoot.
- **Stale news.** Reprinted or old news still moves prices sometimes (EntreMed case; Tetlock 2011), then reverses. Your pipeline must detect "is this actually new?" (novelty/deduplication).
- **Sentiment vs information.** Sentiment (tone) is not the same as fundamental information. Dictionary-based sentiment (Loughran-McDonald) was the pre-LLM standard; LLMs read meaning, not just tone.
- **Attention and coverage.** Stocks with fewer analysts, smaller caps and lower attention react more slowly: more edge, but also more cost and less capacity.
- **Timing granularity.** Which clock are you using: publish time, vendor receive time, or your receive time? A few seconds of mis-timing can create fake edge in backtests.
- **Novelty, relevance, entity linking.** Is the story about *this* company or just mentions it? Is it new? Professional feeds (RavenPack, Bloomberg) score these; you have to build them.
- **Second-order / spillover effects.** News about one firm affects suppliers, customers, competitors (e.g. economic links predict returns: Cohen & Frazzini 2008). This is a natural fit for LLM reasoning and less crowded than direct reactions.

**Go deeper**
- MacKinlay (1997), *Event Studies in Economics and Finance*: the standard method in 30 pages. [JSTOR](https://www.jstor.org/stable/2729691)
- Bernard & Thomas (1989), *Post-Earnings-Announcement Drift*. [JSTOR](https://www.jstor.org/stable/2491062)
- Chan, W.S. (2003), *Stock price reaction to news and no-news: drift and reversal after headlines*, J. Financial Economics. [Google Scholar](https://scholar.google.com/scholar?q=Chan+2003+Stock+price+reaction+to+news+and+no-news+drift+and+reversal+after+headlines)
- Tetlock (2007), *Giving Content to Investor Sentiment*. [doi:10.1111/j.1540-6261.2007.01232.x](https://doi.org/10.1111/j.1540-6261.2007.01232.x)
- Tetlock (2011), *All the News That's Fit to Reprint: Do Investors React to Stale Information?* [Google Scholar](https://scholar.google.com/scholar?q=Tetlock+All+the+News+That%27s+Fit+to+Reprint)
- Huberman & Regev (2001), *Contagious Speculation and a Cure for Cancer* (the EntreMed case). [Google Scholar](https://scholar.google.com/scholar?q=Huberman+Regev+Contagious+Speculation+and+a+Cure+for+Cancer)
- Boudoukh, Feldman, Kogan & Richardson (2019), *Information, Trading, and Volatility: Evidence from Firm-Specific News*. [Google Scholar](https://scholar.google.com/scholar?q=Boudoukh+Feldman+Kogan+Richardson+Information+Trading+and+Volatility+Firm-Specific+News)
- Loughran & McDonald (2011), *When Is a Liability Not a Liability?* and their finance word lists. [doi](https://doi.org/10.1111/j.1540-6261.2010.01625.x) · [Dictionary](https://sraf.nd.edu/loughranmcdonald-master-dictionary/)
- Cohen & Frazzini (2008), *Economic Links and Predictable Returns*. [Google Scholar](https://scholar.google.com/scholar?q=Cohen+Frazzini+Economic+Links+and+Predictable+Returns)
- Hong, Lim & Stein (2000), *Bad News Travels Slowly*. [Google Scholar](https://scholar.google.com/scholar?q=Hong+Lim+Stein+Bad+News+Travels+Slowly)

---

## 5. LLMs in trading: what they can and can't do

### What the evidence says

- **LLMs can extract signal from news.** Lopez-Lira & Tang (2023) found that ChatGPT's yes/no/unknown reading of headlines predicted next-day returns, with stronger results in small caps and for negative news, and that the predictive power **declined** as LLM use spread. Other papers find similar results for earnings calls and filings.
- **Text-based return prediction worked before LLMs too** (Ke, Kelly & Xiu 2019), so LLMs are a better tool for a known idea, not a new idea.
- **Most published LLM-trading backtests are contaminated by look-ahead bias.** The model was trained on text written *after* the events it's "predicting", so it may simply remember what happened. Glasserman & Lin (2023) show this "distraction"/memory effect is real. This is the #1 LLM-specific pitfall.

### Key concepts

- **Training-data contamination / knowledge leakage.** If an event happened before the model's training cutoff, the model's "prediction" can leak the outcome. **Only trust LLM backtests on events after the model's cutoff**, or anonymise the text (strip company names, dates) and check the results hold. Paper trading forward is the cleanest test.
- **Anonymisation test.** Replace "Apple" with "Company X", remove dates and numbers that identify the event. If performance collapses, the model was remembering, not reasoning.
- **Point-in-time models.** Research on "chronologically consistent" LLMs trained only on data up to a date exists for exactly this reason. Keep a note of which model version produced every historical signal.
- **Non-determinism and version drift.** The same prompt can give different answers; model updates change behaviour. Log model ID, prompt version and output for every decision (llamaOS records signals; keep doing that). Treat a model upgrade as a new strategy that needs re-validation.
- **Latency budget.** A large-model call takes seconds. Use tiered triage (cheap/fast filter → expensive deep read only when it matters), which llamaOS already does. Accept that you're trading the *drift*, not the first tick.
- **Calibration.** An LLM's "confidence 0.8" is not a probability. Measure calibration: of all the times it said 0.8, how often was the direction right? Use reliability plots.
- **LLM as feature generator vs decision-maker.** Safer pattern (and yours): the LLM produces **structured features** (event type, surprise direction, magnitude, novelty, confidence), and deterministic, testable rules make the trade. This keeps the system auditable and backtestable.
- **LLM as hypothesis generator = a multiple-testing machine.** If Claude proposes 200 hypotheses and you test them all, ~10 will look significant at p<0.05 by chance. Count every hypothesis tried (your Idea Lab "hypothesis counter" is exactly right) and correct for it (Section 7).
- **Prompt injection via news.** News text is untrusted input. A crafted press release could try to manipulate an LLM-based trader. Keep the LLM's output constrained to a schema, and the risk guard deterministic.
- **Cost.** API cost per news item × thousands of items per day adds up; it is a transaction cost and belongs in your P&L.

**Go deeper**
- Lopez-Lira & Tang (2023), *Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models*. [SSRN 4412788](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788)
- Glasserman & Lin (2023), *Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis*. [arXiv 2309.17322](https://arxiv.org/abs/2309.17322)
- Ke, Kelly & Xiu (2019), *Predicting Returns with Text Data*. [NBER w26186](https://www.nber.org/papers/w26186)
- Sarkar & Vafa (2024), *Lookahead Bias in Pretrained Language Models*. [Google Scholar](https://scholar.google.com/scholar?q=Sarkar+Vafa+Lookahead+Bias+in+Pretrained+Language+Models)
- Search: "chronologically consistent large language models" (point-in-time LLMs). [Google Scholar](https://scholar.google.com/scholar?q=chronologically+consistent+large+language+models+finance)
- Gu, Kelly & Xiu (2020), *Empirical Asset Pricing via Machine Learning*: how ML performs on return prediction in general, and why signal-to-noise is so low. [doi:10.1093/rfs/hhaa009](https://doi.org/10.1093/rfs/hhaa009)
- Stefan Jansen, *Machine Learning for Algorithmic Trading* (2nd ed.), chapters on NLP, with free code. [GitHub](https://github.com/stefan-jansen/machine-learning-for-trading)

---

## 6. Backtesting and research methodology

A backtest is a *simulation of what would have happened*. Every shortcut in it is optimistic.

### The biases (memorise this list)

| Bias | What it is | Defence |
|---|---|---|
| **Look-ahead bias** | Using information not available at decision time (closing price to decide a 10:00 trade; revised earnings; future news timestamps) | Strict point-in-time data; decide at t, fill at t+1 or later. llamaOS fills at next-bar open, which is right. |
| **Survivorship bias** | Testing only on stocks that exist today, ignoring delisted/bankrupt ones | Use a point-in-time universe that includes dead tickers. |
| **Data-snooping / selection bias** | Reporting the best of many tries | Count trials; deflate the Sharpe; keep an untouched holdout. |
| **Overfitting** | Too many parameters/rules tuned to history | Fewer parameters; simple rules; check robustness to small parameter changes. |
| **Cost/fill optimism** | Assuming fills at mid, at the close, at your limit, or with no impact | Model spread, slippage, partial fills; assume worst-case at news time. |
| **Lookback/warm-up errors** | Indicators computed with data from the future, or not warmed up | Unit tests on feature timestamps. |
| **Corporate actions** | Splits/dividends not adjusted, or adjusted using future info | Use properly adjusted, point-in-time data. |
| **Timestamp/timezone errors** | UTC vs ET; DST; vendor vs publish time | One canonical clock; test DST boundaries. |
| **Narrative bias** | You believe it because the story is good | Pre-register what result would make you drop it. |
| **LLM contamination** | See Section 5 | Post-cutoff tests, anonymisation, forward paper trading. |

### Research process concepts

- **In-sample / out-of-sample / holdout.** Develop on one period; test on another; keep a final holdout you look at **once**. Every peek at the holdout turns it into in-sample data.
- **Walk-forward analysis.** Repeatedly fit on a rolling past window and test on the next window. Closer to how you'll actually trade.
- **Cross-validation for time series.** Ordinary k-fold leaks information. Use **purged** and **embargoed** CV (López de Prado): remove training samples whose labels overlap test samples in time.
- **Labelling.** How you define "success": fixed-horizon returns vs the **triple-barrier method** (take-profit, stop-loss, time limit), which matches how bracket orders actually exit.
- **Meta-labelling.** A primary model says *which side*; a secondary model decides *whether to take the trade / how big*. A natural fit for "LLM proposes, filter decides".
- **Robustness checks.** Does it still work if you shift entry by one bar? Change the threshold by 10%? Drop the best 5 trades? Run on a different year? If it's fragile, it's probably fit to noise.
- **Paper trading / incubation.** Forward test with no money for a fixed, pre-agreed period. This is the only test immune to look-ahead and snooping (though not to fill optimism; paper fills are optimistic too).
- **Live vs backtest reconciliation.** Once live, compare each live trade with what the backtester *would* have done on the same data. The gap is your real cost of simulation error.

**Go deeper**
- Marcos López de Prado, *Advances in Financial Machine Learning* (2018): purged CV, triple barrier, meta-labelling, backtest overfitting. Essential, though opinionated and dense.
- Bailey, Borwein, López de Prado & Zhu (2017), *The Probability of Backtest Overfitting*. [SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- Harvey & Liu (2015), *Backtesting*. [SSRN 2345489](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489)
- Ernest Chan, *Quantitative Trading* (2nd ed.), chapter on backtesting pitfalls.
- Robert Carver, *Systematic Trading* (2015): excellent on avoiding overfitting by design. Blog: [qoppac.blogspot.com](https://qoppac.blogspot.com/)

---

## 7. Statistics you need to not fool yourself

You don't need a statistics degree. You need these ideas.

- **Signal-to-noise in finance is terrible.** Daily stock returns are mostly noise. A real edge might shift the mean by 0.05% while the daily standard deviation is 2%. You need **many** observations to see it.
- **How many trades do you need?** Rough guide: to be confident a strategy's Sharpe is above zero, you need roughly `n ≈ (2 / SR_per_trade)²` independent trades. With a per-trade Sharpe of 0.1 (good!), that's ~400 trades. A few dozen trades prove nothing.
- **t-statistic and p-value.** `t ≈ mean / (std / √n)`. With multiple testing, the classic `t > 2` bar is far too low; Harvey, Liu & Zhu argue new factors need `t > 3`.
- **Multiple testing correction.** Bonferroni (strict), Holm, Benjamini-Hochberg (false discovery rate). Intuition: test 100 junk ideas → expect ~5 "significant" at 5%.
- **Deflated Sharpe Ratio (DSR).** Adjusts a backtest Sharpe for how many strategies you tried, the variance across those tries, and non-normal returns. Use it on Idea Lab output.
- **White's Reality Check / Hansen's SPA test.** Formal tests of "is the best strategy better than chance, given all the ones I tried?"
- **Bootstrap and permutation tests.** Shuffle event dates or signs and re-run; if random events give similar results, your edge is fake. Very practical for event studies.
- **Fat tails, skewness, kurtosis.** Returns aren't normal. Strategies that win small and often but occasionally lose big (negative skew, e.g. selling volatility, fading spikes) look great until they don't.
- **Autocorrelation and overlapping returns.** Overlapping holding periods and clustered events (many news items on the same day) are not independent; they inflate your effective `n`. Count *independent* bets.
- **Base rates.** Before saying "the LLM is 60% accurate on direction", check what always guessing "up" gets you.
- **Regression to the mean.** Your best month will be followed by a more average one. Don't scale up right after a hot streak.

**Go deeper**
- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*. [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Harvey, Liu & Zhu (2016), *…and the Cross-Section of Expected Returns*. [SSRN 2249314](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314)
- White (2000), *A Reality Check for Data Snooping*. [doi:10.1111/1468-0262.00152](https://doi.org/10.1111/1468-0262.00152)
- Hansen (2005), *A Test for Superior Predictive Ability*. [Google Scholar](https://scholar.google.com/scholar?q=Hansen+2005+A+Test+for+Superior+Predictive+Ability)
- Nassim Taleb, *Fooled by Randomness* (2001): non-technical, about luck vs skill.
- Free stats refresher: *OpenIntro Statistics*. [openintro.org](https://www.openintro.org/book/os/)

---

## 8. Transaction costs and execution

For short-horizon strategies, **costs decide whether you make money**. Gross edges of a few basis points are common; costs of a few basis points are too.

### Key concepts

- **Basis point (bp).** 0.01%. Think about everything per trade in bps.
- **Explicit costs.** Commissions (zero at Alpaca for stocks), SEC/FINRA fees on sells (tiny), borrow fees for shorts, data fees, **LLM API fees**.
- **Implicit costs.**
  - *Spread*: you pay half the spread each way when crossing it. Large caps: ~1–3 bps. Small caps: 20–100+ bps. At news time: much wider.
  - *Slippage*: difference between the price when you decided and the fill price.
  - *Market impact*: your own order moving the price. Small for small orders in liquid names; significant in small caps.
  - *Opportunity cost*: limit orders that didn't fill on the trades that would have won (the fills you *do* get skew toward losers: adverse selection).
- **Implementation shortfall.** Total cost = decision price vs actual average fill, including unfilled parts. The right metric to track live.
- **Order types.** Market, limit, marketable limit (limit priced through the spread to cap slippage), stop, stop-limit, **bracket** (entry + take-profit + stop-loss), OCO, time-in-force (DAY, IOC, GTC). Know how each behaves in a gap or halt.
- **Execution algorithms.** TWAP, VWAP, POV, Almgren-Chriss optimal execution. Mostly relevant at larger sizes, but the ideas (trading off impact vs risk of waiting) apply.
- **Cost model in backtests.** At minimum: `cost = half_spread + slippage(volatility, size/ADV) + fees`. Make it **worse** at news time and outside regular hours. If the strategy only works with zero costs, it doesn't work.
- **Breakeven cost.** Compute the per-trade cost at which your strategy breaks even. If it's below a realistic cost, stop.

**Go deeper**
- Frazzini, Israel & Moskowitz (2018), *Trading Costs*: real institutional cost data. [SSRN 3229719](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719)
- Almgren & Chriss (2000), *Optimal Execution of Portfolio Transactions*. [Google Scholar](https://scholar.google.com/scholar?q=Almgren+Chriss+Optimal+execution+of+portfolio+transactions)
- Perold (1988), *The Implementation Shortfall: Paper versus Reality*. [Google Scholar](https://scholar.google.com/scholar?q=Perold+1988+The+Implementation+Shortfall+Paper+versus+Reality)
- Robert Kissell, *The Science of Algorithmic Trading and Portfolio Management* (2013).
- Cartea, Jaimungal & Penalva, *Algorithmic and High-Frequency Trading* (2015): mathematical; for when you want depth.
- Alpaca order types docs. [docs.alpaca.markets](https://docs.alpaca.markets/docs/orders-at-alpaca)

---

## 9. Risk management and position sizing

Edge decides *if* you make money; sizing decides *whether you survive* long enough to find out.

### Key concepts

- **Risk per trade.** Size positions so a stop-out loses a fixed small fraction of capital (often 0.25–1%). Remember gaps: the realised loss can exceed the stop distance.
- **Volatility targeting.** Size each position inversely to its volatility (e.g. ATR-based) so each trade contributes similar risk.
- **Kelly criterion.** The bet size that maximises long-run growth given your edge and odds. Full Kelly is far too aggressive when your edge estimate is uncertain (it always is); practitioners use **fractional Kelly** (¼ to ½), and because edge estimates are overstated, often less.
- **Risk of ruin.** Probability of losing enough that you can't continue. Driven by sizing and losing streaks, not average return.
- **Correlation and concentration.** Five news trades on the same sector, or on a macro day, are effectively one bet. Cap exposure per sector/theme and gross/net exposure overall.
- **Drawdown controls.** Daily loss limit, max drawdown kill switch, cool-down periods. llamaOS's `risk.yaml` + kill switch are the right shape.
- **Stop-losses: pros and cons.** They cap losses in normal conditions, but in gaps they fill badly, and tight stops in noisy intraday prices get hit by noise. Time-based exits are often as good for event strategies.
- **Tail risk.** Halts, gap-downs, flash crashes, broker outages. Ask "what's my loss if the stock opens −50% and I can't exit for an hour?"
- **VaR / expected shortfall.** Standard risk metrics: the loss you'd exceed X% of the time, and the average loss when you do. Useful as reporting, unreliable under fat tails.
- **Strategy-level risk.** Allocate across several uncorrelated strategies; retire strategies whose live performance falls outside the range the backtest predicted (pre-define this rule).

**Go deeper**
- Edward Thorp, *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market*. [Google Scholar](https://scholar.google.com/scholar?q=Thorp+The+Kelly+Criterion+in+Blackjack+Sports+Betting+and+the+Stock+Market)
- Robert Carver, *Systematic Trading*: volatility targeting and position sizing done sensibly.
- Ralph Vince, *The Mathematics of Money Management* (1992).
- Van Tharp, *Trade Your Way to Financial Freedom*: popular-level, useful on R-multiples (thinking in units of risk).
- Isichenko, *Quantitative Portfolio Management* (2021): a quant's view of portfolio construction and risk.

---

## 10. Operations, infrastructure and failure modes

A correct strategy can lose all its money through engineering failures. Professionals spend more effort here than on alpha.

### Key concepts

- **Separation of concerns.** Signal generation → portfolio/risk → execution → reconciliation. llamaOS mirrors this (models propose, deterministic code trades).
- **Pre-trade risk checks.** Max order size, max position, price-collar (reject orders far from the last price), max orders per minute, symbol allow/deny lists, buying-power check. Run them in the order path, not as a report afterwards.
- **Kill switch.** One action that cancels all orders and flattens or freezes. Test it regularly.
- **Reconciliation.** Your internal positions must match the broker's. Mismatches (missed fill events, partial fills, restarts) are a top source of real losses.
- **Idempotency.** Retrying a failed request must not send a duplicate order (use client order IDs).
- **State and restarts.** What happens if the process crashes with open orders? On restart, you must rebuild state from the broker, not from memory.
- **Clock and calendar.** Market holidays, half-days, DST, early closes, NTP-synced clocks.
- **Data feed failures.** Stale or missing data should *stop* trading, not trigger it. Detect staleness actively.
- **Deployment risk.** Knight Capital: old code reactivated on one server. Use config/feature flags with care, deploy outside market hours, and keep the ability to roll back.
- **Logging and audit trail.** Every signal, model input/output, decision, order and fill, with timestamps. Essential for debugging and for proving whether an edge was real.
- **Monitoring and alerting.** P&L vs expected, fill rates, slippage, error rates, latency, model failures.
- **Security.** API keys stored safely, 2FA, least-privilege keys, no live trading keys on dev machines.

**Go deeper**
- SEC Knight Capital order (2013): read it as a post-mortem. [sec.gov PDF](https://www.sec.gov/litigation/admin/2013/34-70694.pdf)
- CFTC-SEC (2010), *Findings Regarding the Market Events of May 6, 2010* (Flash Crash). [sec.gov PDF](https://www.sec.gov/news/studies/2010/marketevents-report.pdf)
- FIA, *Best Practices for Automated Trading Risk Controls and System Safeguards* (2024 update). [Google search](https://www.google.com/search?q=FIA+best+practices+automated+trading+risk+controls)
- SEC Rule 15c3-5 (Market Access Rule): what brokers must check pre-trade; a good checklist for your own guard. [sec.gov](https://www.sec.gov/rules-regulations/2011/06/risk-management-controls-brokers-or-dealers-market-access)
- Ernest Chan, *Algorithmic Trading* (2013), chapter on execution systems.

---

## 11. Regulation, accounts and tax (US retail)

Not legal or tax advice; check current rules, because some of these are changing.

- **Pattern Day Trader (PDT) rule, FINRA 4210.** Historically: 4+ day trades in 5 business days in a margin account requires **$25,000** minimum equity. FINRA has proposed replacing this with a different intraday-margin framework; **check the current status** before planning around it. Cash accounts avoid PDT but are limited by settlement.
- **Settlement (T+1).** US equities settle one business day after the trade. In a cash account, using unsettled proceeds can cause **good-faith violations**.
- **Margin and buying power.** Reg T (2× overnight), intraday 4× for PDT accounts. Leverage multiplies both edge and ruin.
- **Short-selling rules.** Reg SHO locate requirement; Rule 201 (SSR).
- **Market manipulation.** Spoofing, layering, wash trading and pump-and-dump are illegal regardless of whether a bot did it. An LLM strategy that "trades on" social-media hype can drift toward this territory; also never trade on material non-public information.
- **Taxes (US).** Short-term gains taxed as ordinary income. **Wash sale rule**: losses are disallowed if you rebuy substantially identical securities within 30 days, which happens constantly in day trading. **Trader Tax Status** and the **Section 475(f) mark-to-market election** can change this; talk to a tax professional.
- **Broker terms.** API rate limits, data redistribution limits, what Alpaca's paper environment does and doesn't simulate (paper fills are more optimistic than real ones).

**Go deeper**
- FINRA, *Day Trading* investor page. [finra.org](https://www.finra.org/investors/investing/investment-products/stocks/day-trading)
- IRS Topic 429, *Traders in Securities*. [irs.gov](https://www.irs.gov/taxtopics/tc429)
- IRS Publication 550 (wash sales). [irs.gov](https://www.irs.gov/publications/p550)
- SEC Regulation SHO overview. [sec.gov](https://www.sec.gov/investor/pubs/regsho.htm)

---

## 12. Glossary (quick reference)

| Term | One-line meaning |
|---|---|
| Abnormal return (AR) | Return minus expected return (e.g. minus market/sector return) |
| ADV | Average daily volume (shares or $); used to size orders vs liquidity |
| Adverse selection | Getting filled mostly when the other side knows more than you |
| Alpha | Return not explained by market/factor exposures |
| ATR | Average True Range; a volatility measure used for stops and sizing |
| Beta | Sensitivity of a stock/strategy to market moves |
| Bracket order | Entry with attached take-profit and stop-loss |
| CAR | Cumulative abnormal return over an event window |
| Capacity | Max capital before costs destroy the edge |
| Deflated Sharpe | Sharpe adjusted for number of trials and non-normality |
| Drawdown | Peak-to-trough decline in equity |
| Embargo | Gap between train and test sets to stop information leaking |
| Event window | Time range around t=0 over which you measure reaction |
| Factor | Systematic return driver (market, size, value, momentum…) |
| Fill | An executed (part of an) order |
| Gap | Price jump with no trading in between |
| Holdout | Data you never touch until the final test |
| IC | Information coefficient; correlation of forecast with outcome |
| Implementation shortfall | Decision price vs realised execution, including missed fills |
| IR | Information ratio; active return ÷ tracking error |
| Kelly | Growth-optimal bet size; use a fraction of it |
| Latency | Delay between event and action |
| LULD | Limit Up-Limit Down volatility bands / pauses |
| Market impact | Price movement caused by your own trade |
| Meta-labelling | Secondary model deciding whether/how much to act on a primary signal |
| NBBO | National Best Bid and Offer |
| Novelty | Whether a news item is genuinely new information |
| Overfitting | Fitting noise in history instead of a real pattern |
| PEAD | Post-earnings announcement drift |
| PFOF | Payment for order flow |
| Point-in-time data | Data exactly as it was known at each moment |
| Purged CV | Cross-validation that removes overlapping-label samples |
| Regime | A period with stable market behaviour (vol, trend, correlation) |
| Sharpe ratio | Mean excess return ÷ volatility |
| SIP | Consolidated US market data feed |
| Slippage | Expected vs actual fill price |
| SUE | Standardised unexpected earnings |
| Survivorship bias | Ignoring assets that died/delisted |
| Triple barrier | Label by which of TP / SL / time limit is hit first |
| VWAP / TWAP | Volume- / time-weighted average price (benchmarks and algos) |
| Walk-forward | Rolling fit-then-test evaluation |

---

## 13. Core reading list

If you read only a few things, read these, in this order.

**Start here (accessible)**
1. Ernest Chan, *Quantitative Trading* (2nd ed., 2021): small-trader perspective, practical.
2. Rishi Narang, *Inside the Black Box* (3rd ed., 2024): how a quant system is structured.
3. Bailey et al. (2014), *Pseudo-Mathematics and Financial Charlatanism* (paper, 15 pages): [SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659)
4. Lopez-Lira & Tang (2023), the ChatGPT forecasting paper: [SSRN 4412788](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788)

**Core (intermediate)**
5. Larry Harris, *Trading and Exchanges*: microstructure.
6. Robert Carver, *Systematic Trading*: design, sizing, avoiding overfitting.
7. Marcos López de Prado, *Advances in Financial Machine Learning*: research methodology.
8. MacKinlay (1997), *Event Studies*: [JSTOR](https://www.jstor.org/stable/2729691)

**Deeper (when needed)**
9. Stefan Jansen, *Machine Learning for Algorithmic Trading* (2nd ed.) + [code](https://github.com/stefan-jansen/machine-learning-for-trading)
10. Grinold & Kahn, *Active Portfolio Management*.
11. Bouchaud et al., *Trades, Quotes and Prices*.
12. Cartea, Jaimungal & Penalva, *Algorithmic and High-Frequency Trading*.

**Ongoing feeds**
- [Quantocracy](https://quantocracy.com/): aggregator of quant blogs.
- [Ernest Chan's blog](https://epchan.blogspot.com/)
- [Robert Carver's blog](https://qoppac.blogspot.com/)
- [Open Source Asset Pricing](https://www.openassetpricing.com/): replicated anomalies with data, useful for seeing how many "edges" survive.
- SSRN search for "news" + "return predictability" / "large language model" + "stock returns" for new papers.

---

## 14. Suggested learning path

Roughly 8–12 weeks part-time. Each stage has a "can you do this?" check.

| Stage | Read | Do in llamaOS | Check |
|---|---|---|---|
| **1. Mental model** (wk 1–2) | Sections 0–2; Chan ch. 1–3; Narang ch. 1–5 | Write down, for each strategy, *who's on the other side* and *why they'd lose* | Can you explain alpha vs beta and the Fundamental Law in two sentences? |
| **2. How markets work** (wk 3–4) | Section 3, 8; Harris (selected chapters) | Measure real spreads/slippage on paper fills at news times vs normal times; compare IEX vs SIP if possible | Can you estimate the round-trip cost (bps) of your typical trade? |
| **3. Event studies** (wk 4–6) | Section 4; MacKinlay; Bernard & Thomas; Chan 2003 | Run Idea Lab event studies by event type × market cap × time of day; plot CAR with confidence bands | Do you know the typical drift duration for your best event type? |
| **4. Not fooling yourself** (wk 6–8) | Sections 6–7; Bailey et al.; Harvey-Liu-Zhu; AFML ch. 7, 11–14 | Add deflated Sharpe + permutation tests to Idea Lab output; run anonymisation test on LLM signals; test only on post-cutoff events | Can you say how many hypotheses you've tested and what your DSR is? |
| **5. Survive** (wk 8–10) | Sections 9–10; Carver; Knight Capital order | Review `risk.yaml`: per-trade risk, sector caps, daily loss limit; test the kill switch and restart-reconciliation | Do you know your worst-case loss in a halted gap? |
| **6. Forward test** (wk 10+) | — | Paper trade one or two strategies for a pre-set period (e.g. 200+ trades) with pre-registered pass/fail criteria | Does live paper match backtest within the predicted range? |

---

## 15. Mapping to llamaOS: questions your system must answer

Use this as a checklist. Each "no" or "don't know" is the next thing to learn or build.

**Edge**
- [ ] For each strategy: what event, what mispricing, why it persists, who's on the other side?
- [ ] What holding period does the event study say the drift lasts? Do the strategy's exits match it?
- [ ] Is the edge concentrated in small/illiquid names, and if so, does it survive realistic costs there?
- [ ] Is the return mostly beta or a known factor (size, momentum)? Have you checked against SPY/sector?

**LLM signals**
- [ ] Are backtests restricted to events after each model's training cutoff?
- [ ] Does performance survive anonymising company names and dates?
- [ ] Is the model's confidence calibrated (reliability plot)?
- [ ] Is every historical signal tagged with model ID + prompt version, and is a model upgrade treated as a new strategy?
- [ ] Is the LLM cost per trade included in P&L?

**Research hygiene**
- [ ] How many hypotheses has Idea Lab tested in total? Is that count used in a deflated Sharpe or FDR correction?
- [ ] Is there a holdout period nobody has looked at?
- [ ] Permutation test: do random event dates produce similar results?
- [ ] Robustness: does the result survive ±1 bar entry shift, ±10% thresholds, dropping the top 5 trades?
- [ ] Do news timestamps reflect when *you* would have received the item, not when it was published?

**Costs and execution**
- [ ] Does the backtest cost model widen spreads at news time and outside regular hours?
- [ ] What's the breakeven cost per trade, and how does it compare to measured paper slippage?
- [ ] Are you using IEX-only data for anything that needs the consolidated market?

**Risk and ops**
- [ ] Per-trade risk, per-sector cap, gross exposure cap, daily loss limit, all enforced pre-trade?
- [ ] Behaviour on halts, LULD pauses, gaps through stops, broker outage, stale data?
- [ ] Restart with open orders → state rebuilt from broker? Idempotent order IDs?
- [ ] A pre-agreed rule for when to retire a strategy whose live results fall outside its backtest range?

---

*Prepared October 2026. Regulatory items (especially the PDT rule) and data-vendor details change; verify against the linked primary sources before relying on them.*
