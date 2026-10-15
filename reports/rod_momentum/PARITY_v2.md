# Parity: repository engine vs research script

Engine replay of `rod_momentum_long.v2` + `rod_momentum_short.v2` (allow_short on), 2705 sessions, SPY, QQQ, IWM, zero costs on both sides.

- Engine trades: 1252; script trades: 1251; matched (same day and symbol): 1251
- Only in engine: 1 [('2018-12-24', 'QQQ')]
- Only in script: 0 []
- Side mismatches among matched: 0
- Entry price difference, bps: median 0.000, max 0.000
- Exit price difference, bps: median 0.000, max 2.141
- Exit kinds (script, engine): flatten/flatten: 1240, stop/stop: 11
- Engine diagnostics: {"evaluations": 340226, "entry_conditions_false": 14100, "outside_entry_window": 324874, "cancelled": 0, "intents_generated": 1252, "intent": 1252, "fill": 2504, "trade": 1252}
