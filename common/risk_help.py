"""Setting-specific explanations for the dashboard; examples are illustrative."""
DETAILS = {
'account.max_gross_exposure': (
'Caps the sum of position notionals relative to account equity. Gross exposure adds absolute long and short values rather than offsetting them. New orders must fit under the cap; working entry orders also consume exposure in the paper engine.',
'With $100,000 equity, 1.0 allows up to $100,000 gross exposure; 0.5 allows $50,000. This is an exposure ceiling, not a target allocation.',
'Cash reserve, deployed-capital limits, per-position limits and broker buying power can impose lower ceilings. Raising this setting does not grant margin permission at Alpaca or resize existing holdings.'),
'account.allow_short': (
'Allows the intraday validator and risk guard to accept short-side strategies and intents. A short sells borrowed shares first and buys them back later. Portfolio sleeves remain long-only.',
'Off rejects a short entry even if its price and news conditions pass. On only permits evaluation; all other risk checks still apply.',
'The Alpaca account must separately permit shorting and the asset must be eligible. This switch does not change broker account permissions. Current paper account guards may still prevent shorts.'),
'account.max_open_positions': (
'Limits the number of simultaneous intraday ticker positions across strategies. The paper engine also treats outstanding bracket entries as occupied symbols to prevent multiple orders from bypassing the limit.',
'At 3, a fourth intraday entry is rejected while three symbols are already occupied. Multiple strategies cannot independently open the same ticker.',
'Portfolio sleeve holdings are excluded from this position-count limit, but still consume account capital. A strategy can specify a stricter limit. Reducing the limit does not itself liquidate existing positions.'),
'account.max_trades_per_day': (
'Caps intraday entries across the account for the trading session. It limits trading frequency rather than order notional or profit/loss. Exits remain available when the entry allowance is exhausted.',
'At 10, the next intraday entry is rejected once the account has recorded ten entries for that session.',
'Strategy-specific daily trade limits can stop that strategy earlier. Portfolio order counts use their separate setting. The counter follows the trading session, not your browser’s local midnight.'),
'account.max_daily_loss': (
'Compares intraday day P&L with session-start equity. When P&L reaches or falls below the negative threshold, the paper engine triggers its persistent kill switch. The intraday calculation excludes portfolio sleeve P&L.',
'At 2% and $100,000 session-start equity, a day P&L of −$2,000 reaches the threshold. P&L includes the engine’s realized and open intraday result.',
'The kill response cancels orders and attempts to close positions, including sleeves through their liquidation path. This is a trigger, not a guaranteed maximum realized loss: gaps, latency and fills can exceed it. Resetting the kill switch remains a host action.'),
'per_trade.default_risk': (
'Sets the equity fraction used for intraday sizing when the strategy supplies no explicit risk-per-trade value. Planned dollar risk is based on the distance between entry and protective stop, not the total purchase cost.',
'At 0.25% on $100,000 equity, planned risk is $250. With a $2 entry-to-stop distance, that corresponds to 125 shares before other sizing constraints.',
'Max risk, position size, available strategy allocation and cash limits still apply. A strategy’s explicit risk value takes precedence but must satisfy the hard cap. Stops do not guarantee execution at their stated price.'),
'per_trade.max_risk': (
'Hard ceiling on planned intraday loss between the entry price and stop, expressed as a share of account equity. An intent that exceeds this ceiling fails the risk guard.',
'At 1% and $100,000 equity, an intent with more than $1,000 planned stop-distance risk is rejected. A $10,000 position can have much less than $1,000 planned risk depending on its stop.',
'This is separate from max position notional and default risk. In paper mode approval checks use the rounded limit-entry price when calculating worst planned entry risk. Gaps and slippage at an exit can still increase actual loss.'),
'per_trade.max_position': (
'Caps one intraday position’s entry notional as a fraction of account equity. Notional is quantity multiplied by entry price; it is different from the entry-to-stop risk amount.',
'At 10% and $100,000 equity, one entry must not exceed $10,000 notional. At a $100 entry price, that is at most 100 shares before other limits.',
'The stricter account or strategy position cap wins. Gross exposure, strategy allocation, intraday budget and cash availability may permit less. Existing positions are not automatically trimmed just because this cap is lowered.'),
'per_trade.require_bracket': (
'Requires protective stop and profit-target levels on intraday entry intents. A broker bracket links the entry with contingent stop and target orders after fills.',
'An entry at $100 with a $98 stop and $104 target has both protective levels. An intent missing either level fails the bracket check when this setting is on.',
'The current Alpaca paper adapter still submits bracket entries and validates their prices even if this generic requirement is turned off. Switching it off therefore does not convert paper entries into unprotected market orders. Protection depends on broker acceptance and execution.'),
'execution.entry_order': (
'Selects the intraday entry execution style. The currently supported choice is marketable_limit: a limit order positioned near the observed price to seek a prompt fill while bounding the entry price.',
'A buy limit of $100.10 can fill at $100.10 or better; it will not buy above that limit. If the market moves higher, it may remain unfilled.',
'Max slippage determines the offset from the signal price. Approval checks, bracket requirements and unfilled-order cancellation still apply. This setting affects entries, not the emergency market-close path.'),
'execution.max_slippage': (
'Determines the entry limit offset from the strategy’s reference price. For longs it permits a buy limit above the reference; for shorts it permits a sell limit below it. It bounds the entry order price, not every source of execution loss.',
'At 0.1%, a $100 long reference produces about a $100.10 buy limit, subject to two-decimal price rounding.',
'A larger offset may improve fill probability but permits a less favorable entry. A smaller offset may leave more orders unfilled. Approval drift is a separate check. Protective or emergency exits can execute beyond this percentage.'),
'execution.cancel_unfilled_after': (
'Sets how long an uncompleted intraday entry order may remain outstanding before the broker loop requests cancellation. Age is measured from the broker order’s creation time.',
'2min means an entry still outstanding after roughly 120 seconds is eligible for cancellation at the next reconciliation pass.',
'This is separate from the manual approval timer. Cancellation is asynchronous; a fill may race with the request. Any already-filled quantity remains a real position and continues through bracket and exit management.'),
'execution.approval_expiry': (
'Limits the lifetime of a trade intent awaiting manual approval. Expired intents cannot become new entries; an approval must also pass current risk, price and session checks.',
'90s gives the operator about a minute and a half from intent creation to approve. Clicking approve does not guarantee that the broker can submit the order before expiration.',
'This timer applies before order submission. Cancel-unfilled-after applies after an order exists. Long approval windows still do not bypass drift, stale-feed, monitoring or market-hours checks.'),
'execution.max_drift': (
'Caps the absolute percentage difference between the intent’s original price and a sufficiently fresh current price at approval/recheck. Both favorable and unfavorable moves can invalidate an old signal.',
'At 0.3%, a $100 reference allows a move of about $0.30 in either direction. A move to $100.50 fails the drift check.',
'This protects against approving a stale trading setup and is separate from the limit-order offset. The paper engine uses a fresh latest-trade snapshot when available and keeps its other freshness checks. Increasing it does not bypass risk sizing.'),
'market.entries': (
'Defines the account’s allowed intraday entry window in US Eastern exchange time, using HH:MM-HH:MM. Each strategy must fit inside this window and may narrow it further.',
'09:35-15:30 allows eligible intraday entries during that exchange-time interval. Shanghai time varies with US daylight-saving time; the stored setting remains Eastern time.',
'The market must also be open according to Alpaca’s clock/calendar. This does not keep the engine trading on holidays or after an early close. It governs new intraday entries rather than protective exits or sleeve decision schedules.'),
'market.flatten_by': (
'Sets the latest intraday flatten deadline in US Eastern time. The engine attempts to close intraday positions and cancel outstanding intraday entries at that deadline.',
'15:55 means five minutes before a normal 16:00 close. On a 13:00 early close, the paper deadline is brought forward to no later than 12:55.',
'A strategy may flatten earlier. Portfolio sleeves are designed to hold overnight and are excluded from the routine intraday deadline. The independent watchdog also checks the deadline; closing fills are not guaranteed at an exact timestamp.'),
'market.min_price': (
'Rejects intraday entry intents below this dollar price per share. It is a minimum eligibility filter, not a stop-loss distance or a minimum investment amount.',
'At 5, a $4.90 entry fails this check while a $5.10 entry passes the price check alone.',
'Strategy universe filters can demand a higher price. Passing this setting does not establish sufficient liquidity: the 20-day dollar-volume check still applies. Portfolio sleeves have a separate price floor.'),
'market.min_adv': (
'Sets the minimum 20-day average daily dollar volume for intraday entries. The engine averages daily close price × daily share volume over 20 daily bars; this is an approximate historical liquidity measure.',
'10,000,000 means an average of at least $10 million traded per day. Missing the required history causes the liquidity check to fail closed.',
'This differs from today’s volume, relative volume, bid/ask spread and order-book depth. A strategy can use additional liquidity filters. Passing this threshold does not guarantee a fill or a narrow spread.'),
'market.stale_feed_halt': (
'Defines how old market bars can become before freshness protections halt entries. During market hours the watchdog also checks feed freshness, including held intraday tickers, and may trigger the kill switch when data remains stale.',
'3min means a last bar older than about 180 seconds can breach the stale-feed threshold after the opening grace period.',
'The paper engine additionally requires a latest completed bar no older than the smaller of 90 seconds and this setting for new signal evaluation. Broker-heartbeat checks are separate. Scheduled market closures are not treated as ordinary intraday feed outages.'),
'market.reconcile_every': (
'Sets the configured reconciliation interval for matching broker and local order/position state. Reconciliation detects fills, canceled orders, missing records and ownership mismatches.',
'1min expresses a 60-second reconciliation cadence in consumers that use this interval. The current Alpaca paper loop reconciles on every loop, roughly every five seconds plus request latency.',
'This value therefore does not slow the current paper loop to exactly the displayed interval. Unknown broker orders or positions block new entries; watchdog checks run independently. Reconciliation verifies account state rather than generating trading signals.'),
'portfolio.enabled': (
'Enables validation and execution support for multi-day portfolio sleeves. A sleeve is a strategy-owned allocation that can hold several stocks across trading sessions, unlike intraday strategies that flatten each day.',
'On permits eligible portfolio strategies to be validated and activated after the normal lifecycle checks. It does not automatically activate every draft portfolio file.',
'This is a feature gate rather than a universal sell-all command. Existing holdings need their normal retirement, risk or liquidation path. Portfolio allocation limits, fund controls, broker permissions and monitoring requirements continue to apply.'),
'portfolio.max_allocation': (
'Caps the combined market value allocated to portfolio sleeves as a fraction of account equity. It bounds the portfolio portion of the account rather than one strategy’s planned stop loss.',
'At 90% and $100,000 equity, aggregate sleeve market value must fit under $90,000 when new orders are checked.',
'Cash reserve, maximum deployed capital and each strategy’s fund allocation can enforce a lower practical ceiling. Intraday capital also competes for shared funds. This does not automatically rebalance existing sleeve holdings to the exact percentage.'),
'portfolio.max_position': (
'Caps portfolio exposure in one ticker as a share of account equity. This is a dollar-notional concentration limit, not a percentage of the sleeve’s own size.',
'At 15% on $100,000 equity, permitted sleeve exposure in a ticker is bounded by $15,000 during order checks.',
'Sleeve allocation, group weights, cash and the account’s gross-exposure limits still apply. Intraday positions use their separate per-trade cap. Lowering this limit does not by itself submit a trimming order.'),
'portfolio.max_symbols': (
'Limits the number of distinct ticker symbols declared by one portfolio sleeve. It constrains the strategy’s holdings universe during validation.',
'At 12, a sleeve declaring thirteen distinct symbols cannot pass the portfolio validation limit even if each proposed position is small.',
'This is separate from the account-wide number of intraday open positions and the dashboard monitoring limit. Every required portfolio ticker also needs monitoring enabled for new buys.'),
'portfolio.max_orders_per_day': (
'Caps portfolio orders across all sleeves for a trading day. Rebalancing buys, adds, trims and ordinary sells can generate separate orders, so this counts orders rather than strategies.',
'At 30, many small rebalancing changes across sleeves can use the allowance even when the number of holdings is unchanged.',
'The portfolio risk guard checks ordinary proposals against this cap. Intraday entries use their own daily trade limit. Emergency liquidation has a dedicated path; this setting is not a guarantee of the final number of broker fills.'),
'portfolio.max_drawdown': (
'Sets the maximum sleeve-specific decline from its tracked peak value before the strategy’s drawdown-stop rules sell and stop the sleeve. A strategy may choose a tighter stop.',
'At 15%, a sleeve whose tracked peak value was $20,000 reaches the threshold around $17,000.',
'This sleeve-value drawdown is different from the fund stop measured against an allocation’s base dollars and from account-wide equity drawdown. Liquidation is evaluated through the portfolio engine’s decision/risk flow and depends on successful broker execution.'),
'portfolio.max_hold_sessions': (
'Caps a portfolio strategy’s planned holding horizon in exchange trading sessions. Sessions are market trading days, not calendar days.',
'130 sessions is roughly half a trading year; weekends and exchange holidays do not consume sessions. A strategy declaring a longer horizon fails validation.',
'The strategy’s own hold_sessions can be shorter. It is not an intraday entry timer or a promise to exit at a particular wall-clock date. Early retirement and risk stops may end the sleeve before its planned horizon.'),
'portfolio.decide_at': (
'Sets the latest allowed portfolio decision time in US Eastern time. Portfolio strategies evaluate their scheduled allocation and rebalance actions at their own eligible daily decision time.',
'15:45 permits a strategy decision at 15:30 but not a declared decision after 15:45. Orders still need time to be accepted before the market closes.',
'The time must satisfy the configuration’s trading-day constraints and precede the intraday flatten setting. Actual market calendar and execution cutoffs still apply. This is a schedule ceiling, not a continuous tick-by-tick portfolio rebalance.'),
'portfolio.min_price': (
'Sets the minimum dollar share price for ordinary portfolio buys. This rejects low-priced candidates independently of portfolio weights or available cash.',
'At 5, a proposed sleeve buy below $5 per share fails the portfolio price check.',
'Portfolio liquidity, allocations, market hours, asset tradability and monitoring are also required. Sell and risk-exit paths serve to reduce exposure rather than acquire a newly eligible holding.'),
'portfolio.min_adv': (
'Sets the portfolio-buy floor for approximate 20-day average daily dollar volume: daily closing price × daily volume averaged over the required history.',
'100,000,000 requires roughly $100 million average daily trading value. Insufficient history cannot establish that the floor is met.',
'This portfolio floor is independent of the intraday liquidity floor and current-day volume. It does not measure today’s spread, available depth or likely slippage for the specific order.'),
'funds.cash_reserve': (
'Requires a minimum cash balance after a proposed buy, measured as a fraction of current account equity. Outstanding working buys are deducted as committed cash before a new buy is checked.',
'At 10% and $100,000 equity, a buy that would leave less than $10,000 available cash is refused.',
'This reserve works with max deployed capital; the tighter constraint wins. It blocks new purchases rather than forcing immediate sales to restore cash. Sells free capital and do not consume this buy allowance.'),
'funds.max_deployed': (
'Caps total deployed dollars across positions and working buys as a share of account equity. It also bounds the combined strategy allocation reservations, so strategies cannot all promise themselves the same capital.',
'At 90% and $100,000 equity, new buys must keep total deployed capital within $90,000, and reserved strategy shares must fit inside the allocation budget.',
'Cash reserve, gross exposure, strategy allocations and intraday budget can be more restrictive. Changing this limit does not grant broker buying power or silently reduce another strategy’s allocation.'),
'funds.intraday_budget': (
'Caps the combined deployed capital of intraday strategies as a fraction of account equity. Existing intraday positions and committed intraday buys consume this shared budget.',
'At 40% and $100,000 equity, all intraday strategies together can deploy at most $40,000 before other limits.',
'Each strategy must also fit its own allocation and per-position limits. Portfolio sleeves use their own allocations but still compete for shared cash and maximum deployed capital.'),
'funds.max_strategy_allocation': (
'Caps the equity fraction that can be reserved for one strategy line. Versions sharing the same strategy id share that line’s fund identity rather than receiving independent pools.',
'At 90%, an operator cannot reserve more than 90% of account equity for one strategy. Other active strategy reservations must still fit the total deployment budget.',
'Allocation is permission to use capital, not an automatic purchase instruction. Individual trades must still pass sizing, exposure, cash, liquidity and price checks. Strategy drawdown controls are measured against the allocation’s recorded base dollars.'),
'funds.default_allocation': (
'Supplies the allocation share for an intraday strategy with no owner-set allocation. Portfolio defaults are derived from the strategy file’s capital and peak-holding requirements, subject to the maximum strategy allocation.',
'At 5% and $100,000 equity, a new unconfigured intraday strategy initially receives a $5,000 capital slice.',
'This is position-capital capacity, not risk per trade. Existing owner-set allocations take precedence. A default reservation must fit alongside other strategies inside max deployed capital; it is not guaranteed just because this default is configured.'),
'funds.strategy_max_drawdown': (
'Caps a strategy fund’s allowed loss from its peak P&L, divided by the base dollar allocation recorded when the allocation was set. Reaching the strategy’s effective threshold pauses new deployment.',
'With a $5,000 base allocation and 25% threshold, a $1,250 fall from peak P&L reaches the stop. A peak P&L of $500 falling to −$750 is the same $1,250 drawdown.',
'This pauses the fund; it does not itself guarantee immediate liquidation of every position. It differs from sleeve-value drawdown and account-wide drawdown. A persistent drawdown pause needs operator action; normal exits continue.'),
'funds.strategy_daily_loss': (
'Caps intraday strategy day losses as a fraction of its recorded base allocation, rather than a fraction of the whole account. Reaching the strategy’s effective threshold pauses its new entries for that session.',
'With a $5,000 base allocation and 10% daily stop, a −$500 strategy day P&L reaches the threshold.',
'The strategy may use a tighter effective stop. The temporary daily pause can clear on the next exchange-date session, unlike a persistent drawdown pause. Existing exit/risk management continues, and account-wide daily loss can halt the account earlier.'),
'funds.account_max_drawdown': (
'Compares current account equity with its tracked peak equity across sessions. When the decline reaches the cap, the fund monitor returns a kill reason and the paper engine triggers the persistent kill switch during market hours.',
'At 20%, a tracked $100,000 peak reaching $80,000 equity breaches the account drawdown threshold.',
'This uses total account equity, including portfolio effects, and differs from the intraday daily-loss limit. The response attempts to cancel/close through the broker; it cannot guarantee a maximum realized loss. Deposits, withdrawals and the persisted peak can affect the measurement.'),
}
