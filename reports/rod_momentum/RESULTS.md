# Rest-of-day momentum backtest: results

Generated 2026-10-15T18:15:26-04:00 by `scripts/research_rod_momentum_backtest.py`. Sessions 2016-01-04 to 2026-10-15 (2701), universe SPY, QQQ, IWM, Alpaca SIP 1-minute bars (split-adjusted). Data fingerprint `02907881d6f6a3c4`.

Account view: each position is 10% of equity (the account's max_position), at most one per ETF. Per-trade figures are in basis points of notional. All figures are this script's own backtest, net of the costs listed.

## Trials (every configuration evaluated)

| Configuration | Trades | Win | Gross bps | t gross | Cost bps | Net bps | t net | Ann. ret | Sharpe | Max DD |
|---|---|---|---|---|---|---|---|---|---|---|
| primary: rod k=0.5, exit 15:55 | 4588 | 41.6% | 0.55 | 1.30 | 3.56 | -3.01 | -7.06 | -1.29% | -1.42 | -13.01% |
| rod sign rule k=0, exit 15:55 | 8001 | 40.9% | 0.60 | 1.97 | 3.55 | -2.95 | -9.62 | -2.20% | -1.93 | -21.04% |
| rod k=0.25, exit 15:55 | 6126 | 41.3% | 0.63 | 1.74 | 3.56 | -2.93 | -8.13 | -1.67% | -1.60 | -16.54% |
| rod k=1.0, exit 15:55 | 2413 | 42.3% | 0.90 | 1.42 | 3.55 | -2.65 | -4.18 | -0.60% | -0.85 | -6.31% |
| gao first-half-hour sign rule, exit 15:55 | 8003 | 40.8% | 0.00 | 0.01 | 3.55 | -3.55 | -11.51 | -2.64% | -2.42 | -24.73% |
| rod k=0.5, hold to 16:00 close (paper; not deployable) | 4588 | 45.6% | 0.29 | 0.57 | 1.91 | -1.62 | -3.21 | -0.69% | -0.65 | -8.40% |
| POST HOC v2: k=0.5 + vol_rank(20,200) >= 0.67 | 1251 | 47.3% | 3.31 | 2.58 | 3.54 | -0.23 | -0.18 | -0.03% | -0.03 | -3.28% |

## Primary specification

Block bootstrap (10000 reps, 10-day blocks): Sharpe 95% CI -2.20 to -0.81; annual return 95% CI -1.76% to -0.79%; P(Sharpe <= 0) = 1.000.

Daily-return t-stat -4.66; permutation p (random side, same days) = 0.0980.

Deflated Sharpe (Bailey & Lopez de Prado 2014) over 7 trials: probability 0.000 (PSR vs 0: 0.000; daily skew 0.81, kurtosis 37.94).

Falsification gates (Mesfin 2026 style): oos_t_net_ge_2 = False, min_trades_per_year_ge_30 = True, net_positive = False, positive_years = 1/11, permutation_p_lt_0.05 = False

### By year

| Year | Trades | Win | Net bps | t (trade) | Ann. ret | Sharpe | Max DD |
|---|---|---|---|---|---|---|---|
| 2016 | 376 | 38.0% | -4.41 | -5.25 | -1.66% | -3.40 | -1.77% |
| 2017 | 393 | 33.1% | -3.82 | -5.80 | -1.52% | -4.15 | -1.58% |
| 2018 | 452 | 41.2% | -0.56 | -0.38 | -0.26% | -0.24 | -2.22% |
| 2019 | 407 | 36.9% | -5.20 | -6.03 | -2.11% | -3.98 | -2.13% |
| 2020 | 426 | 48.6% | 0.79 | 0.27 | 0.34% | 0.17 | -1.49% |
| 2021 | 433 | 47.8% | -1.53 | -1.55 | -0.66% | -1.17 | -1.54% |
| 2022 | 444 | 53.2% | -0.19 | -0.11 | -0.08% | -0.08 | -1.90% |
| 2023 | 445 | 39.3% | -3.86 | -3.83 | -1.75% | -2.90 | -1.91% |
| 2024 | 419 | 38.9% | -6.23 | -5.97 | -2.62% | -3.92 | -2.57% |
| 2025 | 431 | 39.9% | -4.54 | -3.99 | -1.97% | -2.66 | -1.99% |
| 2026 | 362 | 38.4% | -4.37 | -4.75 | -2.01% | -3.48 | -1.58% |

### Walk-forward (k chosen on prior years by net Sharpe, applied to the next year)

| Year | Chosen k | WF trades | WF net bps | WF Sharpe | Fixed k=0.5 net bps | Fixed Sharpe |
|---|---|---|---|---|---|---|
| 2018 | 1.0 | 239 | 2.75 | 0.71 | -0.56 | -0.24 |
| 2019 | 1.0 | 200 | -5.67 | -2.70 | -5.20 | -3.98 |
| 2020 | 1.0 | 233 | -2.82 | -0.42 | 0.79 | 0.17 |
| 2021 | 1.0 | 231 | -3.76 | -2.09 | -1.53 | -1.17 |
| 2022 | 1.0 | 241 | 4.23 | 1.23 | -0.19 | -0.08 |
| 2023 | 1.0 | 245 | -4.75 | -2.98 | -3.86 | -2.90 |
| 2024 | 1.0 | 244 | -7.09 | -3.50 | -6.23 | -3.92 |
| 2025 | 1.0 | 215 | -3.30 | -1.48 | -4.54 | -2.66 |
| 2026 | 1.0 | 195 | -2.57 | -1.69 | -4.37 | -3.48 |

Combined walk-forward OOS: 2043 trades, net -2.49 bps, Sharpe -0.76, ann. return -0.58%. Fixed pre-registered k=0.5 over the same years: net -2.79 bps, Sharpe -1.24.

### By side

| Side | Trades | Win | Net bps | t | Sharpe |
|---|---|---|---|---|---|
| long | 2580 | 40.0% | -3.23 | -6.48 | -1.33 |
| short | 2008 | 43.6% | -2.73 | -3.72 | -0.73 |

### By symbol

| Bucket | Trades | Win | Gross bps | t gross | Net bps | t net |
|---|---|---|---|---|---|---|
| IWM | 1597 | 40.5% | -0.14 | -0.19 | -4.36 | -5.93 |
| QQQ | 1511 | 43.8% | 1.46 | 1.88 | -1.75 | -2.25 |
| SPY | 1480 | 40.5% | 0.37 | 0.53 | -2.84 | -4.08 |

### By prior-day VIX close

| Bucket | Trades | Win | Gross bps | t gross | Net bps | t net |
|---|---|---|---|---|---|---|
| 15-20 | 1632 | 38.2% | -1.14 | -2.12 | -4.69 | -8.70 |
| 20-30 | 1151 | 50.7% | 3.00 | 3.43 | -0.56 | -0.64 |
| <15 | 1511 | 36.4% | -0.38 | -1.01 | -3.96 | -10.49 |
| >=30 | 294 | 51.4% | 5.19 | 1.17 | 1.62 | 0.36 |

### By own vol_rank(20, 200)

| Bucket | Trades | Win | Gross bps | t gross | Net bps | t net |
|---|---|---|---|---|---|---|
| calm (<1/3) | 1697 | 40.8% | 0.21 | 0.49 | -3.37 | -7.95 |
| middle | 1311 | 38.1% | -1.22 | -2.02 | -4.79 | -7.90 |
| n/a | 329 | 37.7% | -1.07 | -1.15 | -4.64 | -5.01 |
| volatile (>=2/3) | 1251 | 47.3% | 3.31 | 2.58 | -0.23 | -0.18 |

### By exit

| Bucket | Trades | Win | Gross bps | t gross | Net bps | t net |
|---|---|---|---|---|---|---|
| flatten | 4538 | 42.0% | 1.28 | 3.08 | -2.29 | -5.51 |
| stop | 50 | 0.0% | -65.17 | -13.34 | -68.57 | -14.00 |

### Cost stress (slippage, spread and fees multiplied)

| Costs | Net bps | t | Sharpe | Ann. ret |
|---|---|---|---|---|
| 1x | -3.01 | -7.06 | -1.42 | -1.29% |
| 2x | -6.57 | -15.41 | -3.09 | -2.81% |
| 3x | -10.13 | -23.73 | -4.71 | -4.33% |

### Post-hoc v2 (regime gate chosen after seeing these results; in-sample only)

| Year | Trades | Net bps | t net | Sharpe |
|---|---|---|---|---|
| 2016 | 5 | -2.83 | -0.62 | -0.66 |
| 2017 | 56 | -4.67 | -2.54 | -2.42 |
| 2018 | 223 | 2.18 | 0.82 | 0.51 |
| 2019 | 58 | -4.39 | -1.70 | -1.37 |
| 2020 | 161 | 6.25 | 0.84 | 0.54 |
| 2021 | 72 | 6.74 | 2.13 | 1.70 |
| 2022 | 269 | 1.32 | 0.55 | 0.37 |
| 2023 | 33 | -0.77 | -0.20 | -0.18 |
| 2024 | 136 | -5.75 | -2.86 | -2.12 |
| 2025 | 122 | -7.78 | -2.54 | -1.64 |
| 2026 | 116 | -2.88 | -1.62 | -1.31 |

v2 by side: long 698 trades, gross 2.65 bps (t 1.76), net -0.87 bps; short 553 trades, gross 4.14 bps (t 1.89), net 0.58 bps.
v2 with measured spreads and no slippage: net 2.80 bps (t 2.19), Sharpe 0.44. At 2x pre-registered costs: net -3.77 bps, Sharpe -0.59. Bootstrap Sharpe 95% CI -0.59 to 0.45.


### Cost sensitivity (same trades, other cost assumptions)

| Costs | Gross bps | t gross | Net bps | t net | Sharpe |
|---|---|---|---|---|---|
| zero costs (gross) | 0.55 | 1.30 | 0.55 | 1.30 | 0.26 |
| measured half-spread + fees, no slippage | 0.55 | 1.30 | 0.04 | 0.10 | 0.02 |
| pre-registered (spread floor + 1 bp slippage + fees) | 0.55 | 1.30 | -3.01 | -7.06 | -1.42 |

Out-of-universe check, DIA with the same rules: 1503 trades, net -5.20 bps, t -7.33.

Costs: {"slippage_bps_per_side": 1.0, "sec_fee_per_million": 20.599999999999998, "taf_per_share": 0.000195, "half_spread_floor_bps": {"SPY": 0.5, "QQQ": 0.5, "IWM": 1.0, "DIA": 1.4999999999999998}, "measured_spread_bps_median": {"SPY": 0.25956834314421534, "QQQ": 0.2791717074864254, "IWM": 0.34338301436827734, "DIA": 0.5597051771767099}, "spread_sample": "480 symbol-day-times, last 60 sessions, +/-10s around 15:30:00 and 15:55:00"}
