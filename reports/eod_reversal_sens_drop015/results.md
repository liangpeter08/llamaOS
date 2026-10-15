# End-of-day reversal research backtest — 20261016_061818

- Strategy: `strategies/eod_reversal_losers_long.v1.yaml` (Baltussen, Da & Soebhag 2025 long-loser side)
- Data: stored 1-minute bars, 169 sessions, 2026-02-12 .. 2026-10-15
- Symbols: AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA
- Data fingerprint (sha256): `40fb3cb5517ffd010b2b257f9cdd3a82ac62f049cdd9e191cbe0c899a85c6409`
- Costs per side: slippage 2.0 bps + half-spread 1.0 bps; commission $0.0/share + $0.0/fill

## Headline (net of costs)

- trades 362 (2.142/day), win rate 0.4696
- expectancy -3.4$/trade = -3.476 bps/trade (per-trade t -1.375)
- net P&L -1229.64$ (gross 900.91$, fees 0.0$)
- daily Sharpe -1.367, daily t-stat -1.12, profit factor 0.82, max drawdown -2.233%
- exits: {'stop': 15, 'target': 0, 'flatten': 347}

## Holdout (chronological 60/40)

- in-sample 2026-02-12..2026-07-10: trades 218, expectancy -1.751 bps, Sharpe -0.641
- holdout 2026-07-13..2026-10-15: trades 144, expectancy -6.086 bps, Sharpe -2.876

## Cost stress (execution costs x1/x2/x3)

- 1x: expectancy -3.476 bps, net -1229.64$, Sharpe -1.367
- 2x: expectancy -9.488 bps, net -3311.69$, Sharpe -3.722
- 3x: expectancy -15.417 bps, net -5320.71$, Sharpe -6.013

## Bootstrap of daily returns (block resampling)

- mean daily return p5/p50/p95: -0.00019 / -0.00008 / 0.00002; P(mean<=0)=0.901; Sharpe p5/p50: -3.510421773383541 / -1.5311243306201352; P(Sharpe<=0)=0.901

## Per month

- 2026-02: trades 24, net 246.21$, expectancy 10.454 bps, win 0.7083
- 2026-03: trades 47, net 306.05$, expectancy 6.641 bps, win 0.5532
- 2026-04: trades 37, net -146.41$, expectancy -4.072 bps, win 0.4595
- 2026-05: trades 43, net 18.24$, expectancy 0.625 bps, win 0.4651
- 2026-06: trades 52, net -954.37$, expectancy -18.668 bps, win 0.3269
- 2026-07: trades 51, net 40.33$, expectancy 0.878 bps, win 0.5686
- 2026-08: trades 48, net -256.61$, expectancy -5.449 bps, win 0.4583
- 2026-09: trades 46, net -503.52$, expectancy -11.565 bps, win 0.3478
- 2026-10: trades 14, net 20.45$, expectancy 1.574 bps, win 0.4286

## Regimes

- volatility: {"high_vol": {"trades": 201, "expectancy_bps": -1.035, "win_rate": 0.4726, "net_pnl": -199.39}, "low_vol": {"trades": 161, "expectancy_bps": -6.523, "win_rate": 0.4658, "net_pnl": -1030.25}}
- market: {"basket_down": {"trades": 221, "expectancy_bps": -4.472, "win_rate": 0.448, "net_pnl": -969.79}, "basket_up": {"trades": 141, "expectancy_bps": -1.914, "win_rate": 0.5035, "net_pnl": -259.85}}

## Per symbol

- AAPL: {"trades": 36, "expectancy_bps": -7.907, "win_rate": 0.4722, "net_pnl": -278.16}
- AMD: {"trades": 54, "expectancy_bps": 2.807, "win_rate": 0.5, "net_pnl": 144.67}
- AMZN: {"trades": 42, "expectancy_bps": -1.512, "win_rate": 0.5238, "net_pnl": -61.93}
- GOOGL: {"trades": 40, "expectancy_bps": -12.61, "win_rate": 0.425, "net_pnl": -500.39}
- META: {"trades": 40, "expectancy_bps": -9.917, "win_rate": 0.35, "net_pnl": -380.45}
- MSFT: {"trades": 44, "expectancy_bps": -4.672, "win_rate": 0.4318, "net_pnl": -197.86}
- NVDA: {"trades": 49, "expectancy_bps": -9.659, "win_rate": 0.4286, "net_pnl": -468.8}
- TSLA: {"trades": 57, "expectancy_bps": 9.094, "win_rate": 0.5789, "net_pnl": 513.28}
