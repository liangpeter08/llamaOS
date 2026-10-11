## Cross-symbol and daily-history features (builtins@v2)

These extend both kinds of file. Use them when an idea depends on another ticker, a multi-day trend, a volatility regime or a relationship between symbols. Prefer one or two well-motivated conditions to many: every extra condition must have its own reason in `assumptions`, and each window is a param with a range, never a value tuned to a single number.

Declarations:
- `references: [TICKERS]` (top level, both kinds): every other symbol the rules read. Reading an undeclared ticker is an error. References are loaded and replayed alongside, and never traded by this strategy.
- `universe.symbols: [TICKERS]` (intraday only): the only symbols this strategy may trade. Use it whenever the idea is about specific tickers; without it the strategy scans the whole watchlist.

Daily-history functions read COMPLETED sessions only (up to yesterday's close), never today's bars, so their value is fixed for the whole session. Windows are whole-number literals or whole-number params, at most 250 sessions in total. Too little history makes the value missing, so the condition is false.
- `ret_d(n)`: return over the last n sessions (yesterday's close vs the close n sessions earlier).
- `vol_d(n)`: standard deviation of daily returns over n sessions (daily, not annualized; n >= 2).
- `vol_rank(n, m)`: share (0-1) of the previous m sessions whose vol_d(n) was at or below today's. A volatility-regime filter: `vol_rank(10, 60) <= calm_max`.
- `sma_d(n)`, `highest_d(n)`, `lowest_d(n)`: average, highest and lowest daily close of the last n sessions.
- `zscore_d(n)`: (yesterday's close - sma_d(n)) / standard deviation of those n closes (n >= 2).
- `up_days(n)`: share (0-1) of the last n sessions that closed up.
- `corr_d(SYM, n)`, `beta_d(SYM, n)`: correlation and beta of this symbol's daily returns with SYM over n common sessions (n >= 2). SYM must be under `references`.

Cross-symbol reads:
- `peer(SYM, expr)` evaluates `expr` for SYM instead of the current symbol, at the same moment.
  - Intraday: on SYM's latest 1-minute bar that closed at or before this bar. If that bar is more than 2 minutes older, the value is missing (false). Inside `peer()` use bar series, session values, indicators and daily functions; position builtins and nested `peer()` are not allowed. Example: `peer(NVDA, close / prior_close - 1) >= lead_min`.
  - Portfolio rules: inside `peer()` only `day_change` and daily functions are available (not `holding`, `gain` or `sessions_held`). Example: `peer(SPY, day_change) <= spy_drop and ret_d(trend_n) < trend_floor`.
- Relative strength is plain arithmetic: `ret_d(n) - peer(SMH, ret_d(n)) >= rel_min`.

Portfolio rules may now also use the daily functions above (for the held symbol) and `peer()`. Every other portfolio restriction is unchanged: no bar series, no signals, first matching rule wins.

Thresholds are still params with ranges. A regime or relative-strength filter is a hypothesis; say in `assumptions` what each one is meant to filter out, so a backtest can test whether it earns its place.
