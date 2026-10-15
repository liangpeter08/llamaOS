# Parity: repository engine vs research script

Engine replay of `rod_momentum_long.v1` + `rod_momentum_short.v1` (allow_short on), 2705 sessions, SPY, QQQ, IWM, zero costs on both sides.

- Engine trades: 4595; script trades: 4588; matched (same day and symbol): 4588
- Only in engine: 7 [('2018-12-24', 'QQQ'), ('2019-07-03', 'SPY'), ('2020-12-24', 'QQQ'), ('2021-11-26', 'IWM'), ('2021-11-26', 'QQQ'), ('2021-11-26', 'SPY'), ('2025-07-03', 'QQQ')]
- Only in script: 0 []
- Side mismatches among matched: 0
- Entry price difference, bps: median 0.000, max 0.629
- Exit price difference, bps: median 0.000, max 2.141
- Exit kinds (script, engine): flatten/flatten: 4538, stop/stop: 50
- Engine diagnostics: {"evaluations": 178751, "entry_conditions_false": 8872, "outside_entry_window": 165284, "cancelled": 0, "intents_generated": 4595, "intent": 4595, "fill": 9190, "trade": 4595}
