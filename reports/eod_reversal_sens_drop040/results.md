# End-of-day reversal research backtest — 20261016_061834

- Strategy: `strategies/eod_reversal_losers_long.v1.yaml` (Baltussen, Da & Soebhag 2025 long-loser side)
- Data: stored 1-minute bars, 169 sessions, 2026-02-12 .. 2026-10-15
- Symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA
- Data fingerprint (sha256): `40fb3cb5517ffd010b2b257f9cdd3a82ac62f049cdd9e191cbe0c899a85c6409`
- Costs per side: slippage 2.0 bps + half-spread 1.0 bps; commission $0.0/share + $0.0/fill

## Headline (net of costs)

- trades 258 (1.527/day), win rate 0.4884
- expectancy -3.15$/trade = -3.184 bps/trade (per-trade t -1.027)
- net P&L -811.72$ (gross 707.67$, fees 0.0$)
- daily Sharpe -1.061, daily t-stat -0.869, profit factor 0.839, max drawdown -1.552%
- exits: {'stop': 14, 'target': 0, 'flatten': 244}

## Holdout (chronological 60/40)

- in-sample 2026-02-12..2026-07-10: trades 156, expectancy -2.315 bps, Sharpe -0.703
- holdout 2026-07-13..2026-10-15: trades 102, expectancy -4.513 bps, Sharpe -1.854

## Cost stress (execution costs x1/x2/x3)

- 1x: expectancy -3.184 bps, net -811.72$, Sharpe -1.061
- 2x: expectancy -9.12 bps, net -2268.69$, Sharpe -2.988
- 3x: expectancy -15.069 bps, net -3733.26$, Sharpe -4.889

## Bootstrap of daily returns (block resampling)

- mean daily return p5/p50/p95: -0.00015 / -0.00006 / 0.00004; P(mean<=0)=0.847; Sharpe p5/p50: -3.3061213690688955 / -1.2254803197399062; P(Sharpe<=0)=0.847

## Per month

- 2026-02: trades 15, net 136.51$, expectancy 9.404 bps, win 0.8
- 2026-03: trades 41, net 281.91$, expectancy 6.981 bps, win 0.561
- 2026-04: trades 24, net -151.17$, expectancy -6.373 bps, win 0.4167
- 2026-05: trades 26, net 43.51$, expectancy 2.006 bps, win 0.5
- 2026-06: trades 40, net -846.57$, expectancy -21.625 bps, win 0.325
- 2026-07: trades 36, net 291.6$, expectancy 8.345 bps, win 0.6667
- 2026-08: trades 32, net -109.11$, expectancy -3.404 bps, win 0.4688
- 2026-09: trades 33, net -447.9$, expectancy -14.065 bps, win 0.3636
- 2026-10: trades 11, net -10.51$, expectancy -0.939 bps, win 0.3636

## Regimes

- volatility: {"high_vol": {"trades": 135, "expectancy_bps": 0.223, "win_rate": 0.5111, "net_pnl": 26.34}, "low_vol": {"trades": 123, "expectancy_bps": -6.923, "win_rate": 0.4634, "net_pnl": -838.06}}
- market: {"basket_down": {"trades": 196, "expectancy_bps": -4.596, "win_rate": 0.4541, "net_pnl": -889.17}, "basket_up": {"trades": 62, "expectancy_bps": 1.281, "win_rate": 0.5968, "net_pnl": 77.46}}

## Per symbol

- AAPL: {"trades": 28, "expectancy_bps": -6.469, "win_rate": 0.5357, "net_pnl": -177.32}
- AMD: {"trades": 41, "expectancy_bps": -3.465, "win_rate": 0.4634, "net_pnl": -147.02}
- AMZN: {"trades": 33, "expectancy_bps": 1.902, "win_rate": 0.5758, "net_pnl": 61.11}
- GOOGL: {"trades": 24, "expectancy_bps": -11.936, "win_rate": 0.4167, "net_pnl": -286.5}
- META: {"trades": 29, "expectancy_bps": -11.269, "win_rate": 0.3448, "net_pnl": -313.5}
- MSFT: {"trades": 28, "expectancy_bps": -5.19, "win_rate": 0.4286, "net_pnl": -142.34}
- NVDA: {"trades": 35, "expectancy_bps": -11.265, "win_rate": 0.4286, "net_pnl": -391.54}
- TSLA: {"trades": 40, "expectancy_bps": 14.796, "win_rate": 0.65, "net_pnl": 585.38}
