# ORB Stocks-in-Play Pro — backtest evidence

- Source: Zarattini, Barbon & Aziz 2024, SSRN 4729284 / SFI RP 24-98
- Data: Alpaca v2 feed `iex`, window **2024-01-01 → 2026-10-16**, 700 trading days, 35 symbols.
- Trades: **1046** (524 long / 522 short).
- Sizing: $100,000 account, risk 0.50%/trade, stop 0.1·ATR14, target 10.0R (placeholder), flatten 15:50.
- Costs: 10 bps round-turn (half-spread 2.0 + slippage 3.0/side).
- Regime gate & first-candle filter: **OFF** (volrank_min=0, candle_atr_min=0).

## Totals (1x costs)
- Net P&L: **$590.14**  (gross $156,619.40)
- Net annualized Sharpe: **0.007** (gross 1.793); t-stat 0.011; max drawdown $12,274.18.
- Block bootstrap (1000×, 10-day blocks) 95% CI — mean daily return [-0.00155, 0.00147], Sharpe [-1.34, 1.10].

## By year

| group | n | win% | net$ | exp$/trade | exp bps | avg RVOL |
|---|---|---|---|---|---|---|
| 2024 | 339 | 20.1% | 16,557 | 48.8 | 3.0 | 3.7 |
| 2025 | 388 | 14.2% | -48,144 | -124.1 | -8.4 | 3.4 |
| 2026 | 319 | 20.1% | 32,177 | 100.9 | 7.5 | 3.5 |

## By side

| group | n | win% | net$ | exp$/trade | exp bps | avg RVOL |
|---|---|---|---|---|---|---|
| long | 524 | 17.6% | 22,047 | 42.1 | 2.8 | 3.4 |
| short | 522 | 18.2% | -21,457 | -41.1 | -2.8 | 3.6 |

## By SPY vol regime (vol_rank 20/60)

| group | n | win% | net$ | exp$/trade | exp bps | avg RVOL |
|---|---|---|---|---|---|---|
| high_vol_spy | 489 | 14.5% | -68,254 | -139.6 | -10.4 | 3.5 |
| low_vol_spy | 557 | 20.8% | 68,844 | 123.6 | 7.6 | 3.6 |

## By SPY daily direction

| group | n | win% | net$ | exp$/trade | exp bps | avg RVOL |
|---|---|---|---|---|---|---|
| spy_up | 587 | 17.4% | -1,250 | -2.1 | -0.1 | 3.4 |
| spy_down | 459 | 18.5% | 1,840 | 4.0 | 0.3 | 3.7 |

## Cost stress (net total $)
| multiplier | round-turn bps | net $ |
|---|---|---|
| 1x | 10 | 590 |
| 2x | 20 | -155,439 |
| 3x | 30 | -311,468 |

_Headline figures from the paper are the authors' own backtests; these are this mechanical version on the curated basket._