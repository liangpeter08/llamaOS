# Backtesting workspace

Open **Backtesting** from the dashboard sidebar, or **Configure backtest** on a strategy. Existing running services need to be restarted to load the API routes and dashboard bundle. API startup applies the additive database schema automatically.

## Workflow

1. Select an exact registered strategy version. Enter an experiment name, notes and tags.
2. Choose inclusive ET session dates, starting USD equity, a symbol basket and a stored-data benchmark. Blank dates use stored sessions; blank intraday symbols resolve to the current universe and are saved explicitly when the run is created. Portfolio weights and symbols come from its strategy.
3. Set execution assumptions. One basis point is 0.01%. Full spread contributes half its value to each side's market slippage. Commission per share and commission per fill are additive. Intraday orders are eligible on the next bar, plus any additional bar latency. Limits remain bounded by their limit price; targets do not receive extra adverse slippage.
4. Check data coverage. Prior daily history is loaded strictly before each decision session. Missing symbols and unaligned bars are excluded and recorded, or make the run fail under strict policy. News gates use recorded signals at their original timestamps.
5. Select optional validation. Holdout and fixed-rule windows replay independently with fresh capital. Execution stress reruns at 2× and 3× configured slippage, spread and commissions. Bootstrap resamples observed daily returns using the saved seed; it does not simulate future prices or model correlated regimes.
6. Run and save. The background worker reports progress, supports cancellation, and saves completed, failed and cancelled experiments. You can leave the page while it runs. Two workers execute concurrently, with at most eight outstanding jobs.
7. Review results in **Saved runs**. Inspect overview, trades, diagnostics, validation and configuration tabs. Select up to four completed experiments to compare. Restore a configuration to create a new trial or save a reusable preset. JSON exports include the complete report and snapshots; CSV exports contain the trade ledger and run identity.

## Saved evidence

`backtest_runs` stores configuration, strategy YAML/hash, effective risk snapshot, notes, author, dates, progress, error and final report. The report records actual evaluated sessions, coverage exclusions, execution/metric conventions and hashes of configuration, engine source, risk and input data. A later backtest does not erase an older run. The strategy's legacy summary still points to the latest completed result.

A data fingerprint detects revisions; it is not a copy of the underlying historical dataset. Exact replay after data corrections requires retaining those original data separately. Restoring a configuration starts a new run against current stored data and effective risk controls; it does not reactivate a strategy or modify account controls. A zero-trade result is saved, but does not advance a draft to backtested status.

Run details and exports require a logged-in owner or viewer. Running, cancelling, checking preflight and saving presets require owner access and CSRF protection. Research endpoints never send broker orders. Background execution is local to the API process: interrupted workers are marked failed after a missing heartbeat and can be rerun from their saved configuration. This is not a distributed durable job queue.

## Model limitations

Intraday replay operates on matching stored one-minute timestamps, with next-bar marketable-limit entries and conservative stop-first brackets. It never fabricates missing bars or signals. Participation limits reject entire entries using the decision bar's observed volume; queue priority and partial fills are unavailable.

Portfolio replay uses same-close daily fills and is labeled as an approximation. A requested date window is honored, with required prior history outside the evaluation window. The strategy's own schedule/horizon can end it earlier. DSL 0.2 market-input bars and index history are included in data fingerprints. Macro overlays retain the daily approximation described by the portfolio engine.

Reports distinguish daily closing drawdown from intraday worst marked drawdown. Risk ratios are undefined when variance, downside or sample size is insufficient; they display as a dash. Gross P&L before commissions still includes execution price effects. Benchmark symbol returns use a first-session-open purchase and daily closing prices without benchmark fees. Cash benchmark returns zero.

The **Research & capabilities** tab and [research catalog](backtesting-research.md) identify models requiring further work: corporate-action cashflows, historical universe constituents, feed/adjustment provenance, calendar-aware half-days, quote/depth models, borrowing/margin/settlement, parameter optimization, walk-forward fitting and formal multiple-testing significance.

## Validation

The regression suite tests missing tickers, timestamp alignment, actual processed sessions, fill latency, trade-through limits, cancellation, commission/cash reconciliation, metric conventions, independent validation variants, repeatable fingerprints/bootstrap, saved run history, preflight, presets, exports, access control and portfolio date windows. Run `make test`, `make typecheck`, and `make build`.
