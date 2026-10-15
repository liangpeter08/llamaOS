# End-of-day reversal research backtest — 20261016_061000

- Strategy: `strategies/eod_reversal_losers_long.v1.yaml` (Baltussen, Da & Soebhag 2025 long-loser side)
- Data: stored 1-minute bars, 169 sessions, 2026-02-12 .. 2026-10-15
- Symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA
- Data fingerprint (sha256): `40fb3cb5517ffd010b2b257f9cdd3a82ac62f049cdd9e191cbe0c899a85c6409`
- Costs per side: slippage 2.0 bps + half-spread 1.0 bps; commission $0.0/share + $0.0/fill

## Headline (net of costs)

- trades 41 (0.243/day), win rate 0.4634
- expectancy -5.85$/trade = -6.01 bps/trade (per-trade t -0.682)
- net P&L -239.67$ (gross 1.6$, fees 0.0$)
- daily Sharpe -0.713, daily t-stat -0.584, profit factor 0.774, max drawdown -0.49%
- exits: {'stop': 5, 'target': 0, 'flatten': 36}

## Holdout (chronological 60/40)

- in-sample 2026-02-12..2026-07-10: trades 41, expectancy -6.01 bps, Sharpe -0.916
- holdout 2026-07-13..2026-10-15: trades 0, expectancy None bps, Sharpe None

## Cost stress (execution costs x1/x2/x3)

- 1x: expectancy -6.01 bps, net -239.67$, Sharpe -0.713
- 2x: expectancy -11.639 bps, net -466.24$, Sharpe -1.368
- 3x: expectancy -17.615 bps, net -707.58$, Sharpe -2.018

## Bootstrap of daily returns (block resampling)

- mean daily return p5/p50/p95: -0.00005 / -0.00002 / 0.00002; P(mean<=0)=0.758; Sharpe p5/p50: -2.953230969581578 / -0.8782105702269513; P(Sharpe<=0)=0.758

## Per month

- 2026-02: trades 4, net -8.46$, expectancy -2.328 bps, win 0.75
- 2026-03: trades 14, net 14.5$, expectancy 0.968 bps, win 0.3571
- 2026-04: trades 11, net -14.4$, expectancy -1.4 bps, win 0.4545
- 2026-05: trades 1, net -47.34$, expectancy -47.731 bps, win 0.0
- 2026-06: trades 11, net -183.98$, expectancy -17.047 bps, win 0.5455

## Regimes

- volatility: {"high_vol": {"trades": 18, "expectancy_bps": 12.619, "win_rate": 0.6667, "net_pnl": 222.95}, "low_vol": {"trades": 23, "expectancy_bps": -20.589, "win_rate": 0.3043, "net_pnl": -462.63}}
- market: {"basket_down": {"trades": 31, "expectancy_bps": 0.996, "win_rate": 0.5161, "net_pnl": 31.08}, "basket_up": {"trades": 10, "expectancy_bps": -27.729, "win_rate": 0.3, "net_pnl": -270.75}}

## Per symbol

- AAPL: {"trades": 3, "expectancy_bps": -20.9, "win_rate": 0.3333, "net_pnl": -61.59}
- AMD: {"trades": 5, "expectancy_bps": 3.016, "win_rate": 0.4, "net_pnl": 14.67}
- AMZN: {"trades": 5, "expectancy_bps": -1.646, "win_rate": 0.6, "net_pnl": -6.75}
- GOOGL: {"trades": 1, "expectancy_bps": -11.681, "win_rate": 0.0, "net_pnl": -11.51}
- META: {"trades": 7, "expectancy_bps": -17.347, "win_rate": 0.4286, "net_pnl": -120.63}
- MSFT: {"trades": 2, "expectancy_bps": -22.334, "win_rate": 0.0, "net_pnl": -43.46}
- NVDA: {"trades": 5, "expectancy_bps": -48.276, "win_rate": 0.2, "net_pnl": -237.85}
- TSLA: {"trades": 13, "expectancy_bps": 17.585, "win_rate": 0.6923, "net_pnl": 227.45}
