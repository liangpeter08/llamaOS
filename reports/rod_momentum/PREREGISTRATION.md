# Pre-registration: rest-of-day momentum (written before any market data was downloaded)

Date: 2026-10-15 ET (2026-10-16 UTC). Author: quant_research@v1 session.

Hypothesis (Baltussen, Da, Lammers & Martens 2021, JFE 142(1); Gao, Han, Li & Zhou 2018, JFE 129(2)): the return from
the previous close to 15:30 ET ("rest of day", r_ROD) positively predicts the 15:30-close return in US index ETFs,
because short-gamma hedgers (option dealers, leveraged-ETF rebalancers) trade in the direction of the day's move late
in the session.

Primary specification, fixed before testing:
- Universe SPY, QQQ, IWM (one position each at most; matches max_open_positions 3).
- Signal r = close of the 15:29 bar / prior close - 1; sigma = vol_d(20) (sample stdev of the last 20 daily returns).
- Long when r > 0.5 x sigma; short when r < -0.5 x sigma; otherwise no trade.
- Entry at the 15:30 bar open (next bar after the decision); exit at the 15:55 bar open (account flatten_by 15:55).
- Protective bracket: stop 0.5 x atr_d(14) from entry, target 4R; stop assumed first.
- Size: 10% of equity per position (the account max_position binds before 0.25% risk).
- Costs per side: max(measured median NBBO half-spread, floor) + 1 bp slippage; SEC fee $20.60/M and FINRA TAF
  $0.000195/share on sells.
- Period: all sessions 2016-01-04 to 2026-10-15 with Alpaca SIP 1-minute bars.

Trials that will also be reported (all count toward the deflated Sharpe): k = 0, 0.25, 1.0; Gao first-half-hour sign
rule; hold to 16:00 close (not deployable here); walk-forward choice of k from {0, 0.25, 0.5, 1.0} by prior-year Sharpe.

Decision rule fixed in advance: deploy to paper only if the primary is net positive with per-trade t >= 2, at least
30 trades in every year, positive in a majority of years, survives 2x costs, and block-bootstrap P(Sharpe <= 0) < 5%.
Otherwise: backtest-in-workspace / do not deploy, reported as such.

## Amendment 1 (2026-10-15 ET, before any result was computed or viewed)

Exit price: the close of the 15:54 bar instead of the open of the 15:55 bar. Reason: parity with the trading code.
`core/session.py` flattens open positions at the close of the bar whose close time reaches `flatten_by` (15:55),
and the backtest must fill exactly as the live/paper loop does. The two prices are one tick of time apart.
