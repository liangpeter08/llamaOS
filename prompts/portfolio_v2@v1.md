## Portfolio files, DSL 0.2: market inputs and overlays

Use `dsl: "0.2"` (quoted) only when a portfolio request needs something 0.1 can't express: a reaction to an index (for example "if the Nasdaq falls 2% in a session"), to a basket of tickers the sleeve doesn't hold (for example "when chip stocks rise"), a sleeve-wide buy pause and rebuild, or proceeds kept as cash. Otherwise keep 0.1. A 0.2 file keeps every 0.1 section and adds:

- `authoring`: `status: proposed_requires_review` and the defaults you chose (`proposed_defaults`) versus what the trader confirmed (`confirmed_choices`). The owner must confirm the review before paper.
- `market_inputs`: named returns computed by code. An index: `instrument` (one of NASDAQ_COMPOSITE, NASDAQ_100, SP_500, DOW_JONES_INDUSTRIAL, RUSSELL_2000), vs the previous regular-session close, from the account's index feed, with no proxy. A basket: `symbols`, the equal-weight mean of member returns at synchronized completed 1-minute bars; members need not be held (they are monitored, never bought). An ETF such as QQQ is a different instrument: declare it as a one-symbol basket and say so in an assumption.
- `portfolio_overlays`: `precedence` and `resolution` exactly as in the example; `rules` bind a catalog scenario: `portfolio_macro_session_decline` (index or basket at or below a negative threshold param; response `trim_sleeve_and_rebuild` or `liquidate_sleeve`) or `hardware_rally_software_cash_trim` (positive basket return; response `trim_group_to_cash` on one holdings group, sized `return / anchor_return x anchor_trim`, capped). Thresholds, trim sizes and steps are params with ranges.
- `monitoring`: `mode: shadow` (overlays only log what they would do; start here unless the trader asks to trade the overlays) or `mode: enforce` (they trade once the owner enables enforcement), the required tickers (holdings plus basket members), the index feeds, model tiers, budget and audit.
- `assumptions` may be structured: `{id, kind, statement, monitored}`; a monitored one adds `evaluation`, typed `questions`, an `assessment` and `responses` from log, notify_dashboard, request_t2 and (critical only) block_new_entries. Models give context; they never sell, size or veto a numeric overlay.

Many 0.2 fields are fixed declarations of what the engine does (for example `missing_member: unknown_do_not_reweight`); copy them exactly from the example, because any other value is rejected. Text values that restate numbers must agree with the params (for example `retrigger: verified_minus_2_percent_on_new_session` with `macro_decline_return: -2%`). Read `on:` keys as written; the validator handles them.

Portfolio 0.2 reference example:
```yaml
{portfolio_v2_example}
```
