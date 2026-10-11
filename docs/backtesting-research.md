# Backtesting framework research and specification

Research date: 2026-10-10. Scope: llamaOS US equities, intraday rules and daily portfolio sleeves. This is an engineering requirements catalog, not a recommendation to trade.

## Findings from primary sources

1. [QuantConnect reality modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts) separates fills, slippage, fees, buying power, settlement and brokerage behavior. Our implementation should save these assumptions with each run, rather than present execution as a single invisible default.
2. [Backtrader slippage](https://www.backtrader.com/docu/slippage/slippage/) makes next-open handling, limit eligibility and price-range capping explicit. OHLC bars cannot tell us the path within a bar; ambiguous stop/target bars need a declared policy. llamaOS retains conservative stop-first brackets and next-bar entry eligibility.
3. [NautilusTrader behavioral models](https://nautilustrader.io/docs/latest/concepts/behavioral_models/) separates fill, fee, latency and account models. These should be independently testable. Bar latency is feasible now; quote-level queues and partial fills require finer data.
4. [NautilusTrader run configuration](https://nautilustrader.io/docs/latest/concepts/backtesting/apis-and-runs/) uses explicit run configurations and fresh state for independent runs. Save a complete configuration and strategy snapshot; never reuse a portfolio's final state for another test.
5. [Alpaca historical bars](https://docs.alpaca.markets/us/reference/stockbars) exposes feed, resolution, date boundaries, adjustment and symbol mapping, and requires pagination. IEX and SIP differ; price/volume adjustments must match. Our existing database does not track feed/adjustment provenance per row, so expose that uncertainty rather than invent it.
6. [QuantConnect warm-up](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods) initializes indicators using prior data and prevents trading during warm-up. IPOs, illiquidity and unavailable history can leave indicators unready. In llamaOS, daily history must precede the decision session and the intraday entry warm-up must not read future bars.
7. [Backtrader analyzers](https://www.backtrader.com/docu/analyzers-reference/) covers returns, drawdown, trade analysis, exposure and Sharpe with configurable risk-free rate and annualization. Save metric conventions; use null for undefined statistics, not an artificial zero or infinity.
8. [QuantConnect results](https://www.quantconnect.com/docs/v2/local-platform/backtesting/results) combines performance, equity, drawdown, benchmark, trades, fees and logs. A zero-trade result needs a diagnostic explanation, not just a successful completion banner.
9. [QuantConnect walk-forward optimization](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization) distinguishes fitting on a past window from evaluating later data, with an optimization schedule. Fixed-rule rolling windows are useful stability analysis, but must not be called walk-forward optimization unless parameters are fitted using training data only.
10. [Bailey and López de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) explains performance inflation from multiple testing and non-normal returns. Keep every trial, including failures. Do not claim statistical significance from a short sample or a Sharpe ratio chosen after many trials.

## Repository audit

The old strategy button executes synchronously, has fixed capital and slippage, overwrites `strategies.backtest`, and returns a short message. Intraday replay takes the minimum bar count across the universe, so one missing ticker can silently erase a session. Differing timestamps are joined by array index, potentially combining unrelated bars. `days` reports requested sessions even when no session was processed. There is no funnel for failed universe/entry rules, missing signals, sizing failures or unfilled orders. Portfolio replay ignores a supplied date window, restricts itself to the last strategy horizon, and fills decisions at the same daily close. Basic metrics omit fees and risk-free assumptions.

## Full feature catalog

| Area | Configuration / behavior | Result and acceptance criteria |
| --- | --- | --- |
| Experiment | Name, notes, tags, parent run, immutable strategy version/hash/YAML; later typed parameter overrides | Historical runs survive strategy edits and later runs; configuration can be restored without activating a strategy |
| Data | Inclusive start/end sessions, symbols/universe, source/feed, resolution, timezone, calendar, adjustment, point-in-time symbol mapping, corporate actions | Coverage by symbol, actual vs requested dates, fingerprint, missing/stale/invalid data; never fabricate bars or signals |
| Universe | Static basket now; later historical constituents, delisted symbols, IPO dates, liquidity filters, sector constraints | Freeze membership per run; warn that today's universe introduces selection/survivorship bias |
| Initialization | Starting equity, USD base currency, prior-history requirements, warm-up bars; later cash/margin accounts, settlement and FX | No entries during warm-up, no future daily history, no undefined indicators silently converted to prices |
| Strategy | Exact rules, entry windows, stops, targets, sizing, allocation and holding horizon | Same deterministic rule implementation as trading; display YAML and a hash |
| Risk | Snapshot all account/per-trade/portfolio controls, drawdown/loss/exposure caps, position count and short permissions | Replay uses frozen controls; sandbox experiments never alter running risk settings |
| Execution | Slippage bps, spread bps, commission per share/per fill, latency bars, cancellation bars, touch vs trade-through limit fills, stop-first ambiguity | Costs change cash during replay, affect sizing/risk checks, reconcile to final equity; entry cannot fill on its decision bar |
| Capacity | Current-bar volume participation cap; later ADV impact, depth, queues, partial fills, auction/halt behavior | Reject infeasible entries; clearly identify whole-order approximation and unavailable depth |
| Carry | Portfolio overnight positions; later dividends, splits, borrow availability/fees, funding and interest | No total-return claim without corporate-action/carry accounting |
| Benchmark | Cash or stored symbol buy-and-hold over the identical evaluated sessions | Curve, benchmark return, excess return; missing benchmark data is explicit |
| Statistics | Net/gross P&L, final equity, return, annualized return/volatility, Sharpe, Sortino, Calmar, drawdown and duration, win rate, profit factor, expectancy, avg R, best/worst trade, fees, exposure, turnover | Defined formulas and denominator; null for insufficient samples; daily vs intrabar drawdown distinguished |
| Attribution | Per-session, monthly, per-symbol and exit-reason breakdowns; later sector, regime, long/short and rolling statistics | Drill from aggregate to trade, order and decision records; chart return and drawdown |
| Diagnostics | Rule funnel, failed predicates, missing signals, missing prior history, rejected risk checks, submitted/unfilled/cancelled orders, bounded example records | Explain zero trades using observed counts; never loosen rules just to produce trades |
| Validation | Chronological holdout, fixed-rule rolling windows, execution-cost stress reruns, seeded bootstrap of daily returns | Holdout replays independently; stress reruns change execution; bootstrap labeled resampling, not a price simulation |
| Optimization | Later typed parameter grids/random search, objective/constraints, trial budget, train-only selection, walk-forward fitting, purge/embargo for overlapping labels | Keep all trials and split lineage; no optimization against the final holdout |
| Statistical robustness | Later confidence intervals, probabilistic/deflated Sharpe, correlated-trial counts, PBO, block bootstrap and regime stress | Insufficient inputs return unavailable; do not fabricate significance |
| Run lifecycle | Background run, progress/status, durable completed/failed records, cancellation, bounded concurrency, repeatability hash | Browser reload retains the run; exceptions and cancellation are reviewable; no broker commands |
| Review | Search/filter history, compare saved runs, restore configurations, save presets, JSON/CSV exports | Every report carries configuration, strategy/risk/data snapshots and actual dates; exports remain accessible to authenticated users |
| Operations | Access control, CSRF, audit, limits on range/records/parallel work; later durable distributed queue, retention, artifact backups | Owners run/save; viewers review; no credentials in reports; process failures identified on subsequent inspection |

## Implementation boundaries

Implement a usable research workspace now with stored-data preflight, configurable date range/capital/symbols/costs/execution, presets, durable background runs, cancellation, history, comparison, charts, trades, rule diagnostics, snapshot exports, holdout replay, rolling stability, cost stress reruns and deterministic bootstrap. Both intraday and existing daily portfolio strategies remain supported.

Some capabilities need additional infrastructure: historical universe membership, row-level provider provenance, exchange holiday/early-close calendars, quote/order-book reconstruction, volume-based partial fills, corporate-action cashflows, margin/settlement/borrow, arbitrary strategy parameter optimization and formal multiple-testing significance. The workspace must disclose these gaps with a capability list. Unsupported controls must not be offered as if they work.

Daily portfolio replay remains a same-close approximation and is explicitly labeled as such. A true next-session-open portfolio model requires accounting and rule-state changes that should be validated separately against the paper broker. A chronological holdout or rolling fixed-rule report does not establish a permanently unseen test set: repeated access must be counted as research trials.

## Metric conventions and verification

Use 252 trading sessions for annualization; annual risk-free rate converted geometrically to a daily rate; daily Sharpe uses sample standard deviation. Sortino uses root mean squared negative excess returns across all sessions. Drawdowns use the initial capital as the first peak; daily reports are close-to-close and intraday runs also report worst marked equity per bar. Total return derives from final marked equity, not rounded trade sums. Profit factor is gross winning net-trade P&L divided by absolute losing net-trade P&L and is undefined with no losses. Costs are charged on every fill. CAGR is an annualized extrapolation and unreliable on short samples. Bootstrap samples whole observed daily returns with replacement and a saved seed; it assumes independent sessions and does not represent execution or future price paths.

Regression coverage must include missing symbols, unequal bar timestamps, actual vs requested sessions, insufficient warm-up, point-in-time signals, fill latency, trade-through limits, cash/fee reconciliation, portfolio date boundaries, out-of-sample isolation, persisted multiple runs, cancellation/failure, owner/viewer permissions and CSRF. Existing trading tests must continue to pass.
