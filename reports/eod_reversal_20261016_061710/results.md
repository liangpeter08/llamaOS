# End-of-day reversal research backtest — 20261016_061710

- Strategy: `strategies/eod_reversal_losers_long.v1.yaml` (Baltussen, Da & Soebhag 2025 long-loser side)
- Data: stored 1-minute bars, 169 sessions, 2026-02-12 .. 2026-10-15
- Symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA
- Data fingerprint (sha256): `40fb3cb5517ffd010b2b257f9cdd3a82ac62f049cdd9e191cbe0c899a85c6409`
- Costs per side: slippage 2.0 bps + half-spread 1.0 bps; commission $0.0/share + $0.0/fill

## Headline (net of costs)

- trades 320 (1.893/day), win rate 0.4781
- expectancy -3.03$/trade = -3.077 bps/trade (per-trade t -1.119)
- net P&L -969.63$ (gross 914.92$, fees 0.0$)
- daily Sharpe -1.118, daily t-stat -0.915, profit factor 0.841, max drawdown -1.861%
- exits: {'stop': 15, 'target': 0, 'flatten': 305}

## Holdout (chronological 60/40)

- in-sample 2026-02-12..2026-07-10: trades 191, expectancy -1.381 bps, Sharpe -0.457
- holdout 2026-07-13..2026-10-15: trades 129, expectancy -5.589 bps, Sharpe -2.545

## Cost stress (execution costs x1/x2/x3)

- 1x: expectancy -3.077 bps, net -969.63$, Sharpe -1.118
- 2x: expectancy -9.092 bps, net -2807.44$, Sharpe -3.267
- 3x: expectancy -15.013 bps, net -4591.6$, Sharpe -5.355

## Bootstrap of daily returns (block resampling)

- mean daily return p5/p50/p95: -0.00017 / -0.00007 / 0.00003; P(mean<=0)=0.870; Sharpe p5/p50: -3.2196947668306906 / -1.2999996775549145; P(Sharpe<=0)=0.870

## Per month

- 2026-02: trades 20, net 237.51$, expectancy 12.147 bps, win 0.75
- 2026-03: trades 43, net 223.72$, expectancy 5.304 bps, win 0.5581
- 2026-04: trades 32, net -182.96$, expectancy -5.817 bps, win 0.4375
- 2026-05: trades 35, net 107.04$, expectancy 3.306 bps, win 0.4857
- 2026-06: trades 48, net -761.3$, expectancy -16.2 bps, win 0.3542
- 2026-07: trades 45, net 20.24$, expectancy 0.577 bps, win 0.6
- 2026-08: trades 43, net -181.99$, expectancy -4.241 bps, win 0.4651
- 2026-09: trades 41, net -478.69$, expectancy -12.178 bps, win 0.3171
- 2026-10: trades 13, net 46.79$, expectancy 3.695 bps, win 0.4615

## Regimes

- volatility: {"high_vol": {"trades": 169, "expectancy_bps": 1.262, "win_rate": 0.503, "net_pnl": 205.17}, "low_vol": {"trades": 151, "expectancy_bps": -7.933, "win_rate": 0.4503, "net_pnl": -1174.8}}
- market: {"basket_down": {"trades": 217, "expectancy_bps": -3.932, "win_rate": 0.4516, "net_pnl": -844.69}, "basket_up": {"trades": 103, "expectancy_bps": -1.276, "win_rate": 0.534, "net_pnl": -124.94}}

## Per symbol

- AAPL: {"trades": 32, "expectancy_bps": -6.561, "win_rate": 0.5, "net_pnl": -204.48}
- AMD: {"trades": 48, "expectancy_bps": 4.098, "win_rate": 0.5, "net_pnl": 186.62}
- AMZN: {"trades": 38, "expectancy_bps": -1.359, "win_rate": 0.5526, "net_pnl": -51.14}
- GOOGL: {"trades": 34, "expectancy_bps": -10.472, "win_rate": 0.4706, "net_pnl": -355.16}
- META: {"trades": 35, "expectancy_bps": -6.496, "win_rate": 0.3714, "net_pnl": -218.18}
- MSFT: {"trades": 37, "expectancy_bps": -6.622, "win_rate": 0.4054, "net_pnl": -236.55}
- NVDA: {"trades": 44, "expectancy_bps": -14.005, "win_rate": 0.4091, "net_pnl": -610.11}
- TSLA: {"trades": 52, "expectancy_bps": 10.094, "win_rate": 0.5769, "net_pnl": 519.37}
