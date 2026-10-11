# Self-generating strategy discovery for a ticker or basket

Research date: 2026-10-10. Status: feasibility study and proposed design, nothing built. Scope: given a focus (one ticker, e.g. `AMD`, or a basket, e.g. the v2 software/hardware split), the system proposes strategies whose *reasoning* is rich, builds them in the DSL, tests them and reports which survive. It never promotes anything. Generated files enter the normal lifecycle as drafts.

## Status (2026-10-10): single-ticker discovery is built

`discovery/` implements §3 for one ticker, producing both intraday and daily (`kind: portfolio`) strategies:

| Step | Code |
|---|---|
| Sessions split into discovery and a sealed holdout (latest 25%) | `discovery/run.py: sessions` |
| Profile from discovery data only (trend, volatility regime, z-score stretch, peer lead-lag, opening behaviour) | `discovery/profile.py` |
| Hypotheses: 6 intraday + 5 daily template families; Claude adds 4 per kind when a key is set | `discovery/templates.py`, `services/models/discovery.py`, `prompts/discovery@v1.md` |
| Evaluation: base + 3x-cost replay, folds, ablation, parameter neighbourhood, regime split of returns, gates | `discovery/evaluate.py` |
| Weakness -> revision: remove a dead condition, loosen the blocking threshold, move a fragile param to its plateau, add a regime filter (vol, trend, market), cap trades | `discovery/mutate.py` (Claude writes free-form revisions too) |
| Loop: leaders are polished and the best gate-failing candidate is repaired each generation; a revision counts only if it beats its parent by a margin; stops after 2 generations without a better candidate | `discovery/run.py: run_kind` |
| Holdout once per finalist (max 2 per kind); deflated Sharpe over all trials; CSCV PBO | `discovery/run.py`, `discovery/stats.py` |
| Survivors written as draft strategy files with their evidence and lineage in `assumptions` | `discovery/run.py: _annotate` |
| Ledger: `discovery_runs`, `discovery_trials` (every trial, including invalid and duplicate) | `common/db.py` |

Run it with `make discover T=AMD` (`ARGS="--kinds daily --no-save --offline"`), `POST /api/discovery`, or the models service `/discovery/run`. Not built yet: a dashboard page (the API returns runs, trials and reports), multi-ticker baskets, and walk-forward re-fitting.

## 1. Short answer

It is possible, and most of the pipeline already exists. What exists:

| Need | Already in the repo |
|---|---|
| Hypotheses from measured data | Idea Lab (`services/models/idealab.py`): code computes event studies, Claude proposes hypotheses with mechanism and falsifier, holdout scored once, "tested N / survived M" counter |
| English → DSL | Architect (`prompts/architect@v1.md`, `authoring.design`) with validator auto-repair loop; deterministic `template_strategy` |
| Critique | Four-agent review panel (`authoring.review`, `prompts/panel@v1.md`) |
| Testing | Backtest workspace (`backtest/workspace.py`): background runs, holdout replay, rolling windows, cost stress, seeded bootstrap, diagnostics funnel, data download on demand (`common/history.py`) |
| Safety | Models only draft; validator, risk snapshot, hash-pinned approval |

What is missing falls into three groups. The third matters most:

1. **An orchestrator.** Something has to run *propose → compile → backtest → critique → mutate* without a human typing each turn, and keep a ledger of every trial.
2. **A focus profile.** Idea Lab only studies news groups (catalyst × direction). A ticker focus needs a code-computed profile of *that* name: its regimes, peers, lead-lag and event behaviour.
3. **DSL vocabulary.** This is the real ceiling. A strategy's reasoning can only be as complex as what the DSL can express:
   - Intraday rules see the symbol's own bars, session values, a few indicators and news signals. They cannot see another symbol, an index, multi-day returns or realized volatility.
   - Portfolio rules see four values: `day_change`, `holding`, `sessions_held` and `gain`. They also have two v2 catalog scenarios.

   A hypothesis like "AMD lags NVDA by a session after NVDA earnings, unless SOX is in a high-vol regime" can't be written today. However the generator is built, it would either be dropped or flattened into something simpler.

Recommendation: put the complexity in the **conditioning and the mechanism**, not in the number of rules. Expand the vocabulary with typed, point-in-time features computed by code, and let the LLM compose them. Then make every added condition prove that it earns its place (ablation, §6). Complex rule sets fitted to one ticker's history are the textbook way to manufacture a backtest that won't trade.

## 2. Prior art (what works, what to borrow)

- **Formulaic alpha mining.** Examples are genetic programming (gplearn-style), AlphaGen (RL over expression trees, KDD 2023) and Alpha-GPT (LLM + human-in-the-loop, 2023). The generator emits expressions over a fixed operator/feature set and an evaluator scores them. Borrow the *closed grammar*: the LLM writes within a typed vocabulary, never free code. llamaOS's DSL already is this.
- **LLM research agents.** Examples are QuantAgent (2024; an inner loop writes and tests, an outer loop builds a knowledge base from results), AlphaAgent (2025) and Microsoft's R&D-Agent-Quant (2025). AlphaAgent explicitly penalises low originality, hypothesis/implementation mismatch and excess complexity to slow alpha decay. R&D-Agent-Quant splits *research* (hypothesis) from *development* (implementation) and feeds backtest results back to the next hypothesis. Borrow: (a) separate hypothesis from implementation and check they match; (b) a complexity penalty; (c) a persistent "what we already tried and why it failed" memory.
- **Evolutionary code search.** Examples are FunSearch and AlphaEvolve (DeepMind, 2023/2025). The LLM proposes mutations of the best programs, an automatic evaluator scores them, and an island/quality-diversity archive keeps variety. Borrow: MAP-Elites style archive keyed by *mechanism family × horizon*, so the search doesn't collapse onto one overfit idea.
- **Multiple-testing statistics.** Examples are Bailey & López de Prado (Deflated Sharpe Ratio; Probability of Backtest Overfitting via CSCV), White's Reality Check / Hansen's SPA, and Harvey–Liu–Zhu (t > 3 for new factors). An automated generator is a multiple-testing machine; without these, its best output is the luckiest one. Already flagged as "later" in `docs/backtesting-research.md`; this feature makes them mandatory.

## 3. Proposed loop

```text
focus (ticker | basket | free text)
  │
  ▼
[1] Focus profile  ── code only, point-in-time, discovery window only
  │
  ▼
[2] Hypothesis generation  ── Opus, structured output, cites profile rows
  │        (mechanism, conditioning, predicted sign, horizon, falsifier, family)
  ▼
[3] Compile  ── architect/compiler → validator (auto-repair ≤2) → DSL draft
  │        + "does the file implement the hypothesis?" check (back-translation vs hypothesis)
  ▼
[4] Evaluate on DISCOVERY window  ── workspace runs: base, cost-stress, rolling, param grid in declared ranges,
  │        ablation (drop each condition), placebo (shuffled / peer transfer)
  ▼
[5] Critique  ── diagnostics funnel + metrics → Opus (and panel for finalists)
  │
  ├──► mutate (≤ K generations): LLM edits the DSL with a stated reason; code re-evaluates
  │
  ▼
[6] Archive  ── every trial in a ledger; elites per (family × horizon) niche
  │
  ▼
[7] Final holdout  ── once per finalist, never used for selection; DSR / PBO over the full ledger
  │
  ▼
drafts + research report  →  human  →  normal lifecycle (paper first)
```

Roles stay as the README states: **code measures, the model reasons, you decide.** The model never computes a statistic it reports, and never sees holdout data before the final step.

### [1] Focus profile (new, code)

Computed on the discovery window and stored as a versioned JSON blob the LLM receives:

- **Return structure.** Autocorrelation at 1/5/20 days, overnight vs intraday split, gap fill rates, day-of-week and time-of-day buckets (from 1-min bars).
- **Regimes.** Realized-vol terciles, trend vs range (e.g. 50-day slope sign), and how 1/5-day forward returns differ across them.
- **Peers and drivers.** Beta and correlation to QQQ/SPY and to the basket. Lead-lag cross-correlations with each peer (e.g. NVDA t-1 → AMD t). Residual (idiosyncratic) return series.
- **Events.** The existing Idea Lab event study filtered to the focus symbols. Earnings-window behaviour (pre-drift, gap, post-drift) once an earnings calendar exists.
- **Liquidity.** ADV, spread proxy, typical 1-min range: inputs to cost realism.

Every row carries `n` and t-stats so the generator can see what is anecdotal. Same rule as `min_events` today.

### [2] Hypothesis spec (structured output)

Extend `IDEAS_SCHEMA` rather than inventing a new one:

```yaml
family: lead_lag | event_drift | mean_reversion | momentum | regime_switch | relative_value | seasonality
mechanism: "why a counterparty would leave this on the table"
conditions:                   # each one must be justified separately
  - {feature: peer_return_1d(NVDA), op: ">", value_param: lead_min, why: "..."}
  - {feature: realized_vol_20d_pct, op: "<", value: 0.67, why: "regime filter: lead-lag breaks in panics"}
predicted: {sign: +, horizon: 1d, magnitude_hint: "from profile row lead_lag.NVDA.AMD"}
falsify: "on peer pairs (AVGO→MRVL, ...) the conditional mean is ≤ 0"
cites: [profile.lead_lag.NVDA_AMD, profile.regime.vol]
```

"Complex reasoning" lives here: multi-condition mechanisms, regime gates, cross-asset drivers, event interactions. Validation rejects specs citing profile rows that don't exist (as `_claude_ideas` already drops ideas citing missing groups).

### [3] Compile with a fidelity check

Use the existing compiler/architect path, then compare `back_translate(strategy)` to the hypothesis with one cheap model call: *does this file implement these conditions, and only these?* Mismatch → repair or drop. This is AlphaAgent's hypothesis-alignment idea. It catches the compiler quietly simplifying an idea the DSL can't express.

### [4]–[5] Evaluate and mutate

Each candidate is a family of workspace runs (`create_run` with `parent_id` linking them):

| Run | Purpose |
|---|---|
| base | discovery window, realistic costs (`slippage_bps`, `spread_bps` from the profile) |
| cost_stress | already supported |
| rolling | already supported; stability across sub-periods |
| param grid | code sweeps each param inside its declared `range` (small grid / random search, train window only) and reports the *plateau*, not the peak |
| ablation | one run per condition removed; a condition that doesn't change results materially is deleted (complexity must pay rent) |
| placebo | same rules on peers that didn't motivate the idea, and on time-shifted inputs; real mechanisms should transfer, overfits shouldn't |

The mutation prompt receives metrics, the rule funnel ("entry blocked by X 80% of the time"), ablation deltas and the ledger of prior failed variants. It returns one edit with a reason. Code, not the model, decides whether a variant enters the archive.

### [6] Ledger and archive

New tables, sketched:

- `discovery_runs(id, focus, profile_hash, created_at, budget, status, discovery_window, holdout_window)`
- `discovery_trials(id, run_id, parent_trial_id, hypothesis_json, yaml_hash, family, horizon, backtest_run_ids, score_json, verdict, reason)`

Every trial is counted, including validator failures. The trial count feeds the deflated Sharpe. The "tested N / survived M" counter generalises to discovery runs.

Selection score (discovery data only), roughly: median of rolling-window Sharpe, penalised by condition count, parameter-plateau width and the cost-stress drop. Gates are minimum trades and minimum sessions; candidates below them can't be finalists, like `anecdotal` today.

### [7] Final holdout

Each finalist replays once on the reserved window, using the same mechanism as Idea Lab's one-time scoring. Report it alongside the DSR (using the ledger's trial count) and PBO via CSCV over the trial return matrix. A finalist whose holdout result is flat is still reported, as a failure. The report is the product, not the "winner".

## 4. DSL vocabulary expansion (prerequisite)

**Status (2026-10-10): implemented** as builtins@v2: `dsl/features.py`, `peer()`, `references:`, `universe.symbols:`. Tests are in `tests/test_dsl_features.py`; the compiler/architect reference is `prompts/features@v1.md`. Not done: index instruments inside `peer()` (use ETFs such as QQQ, SMH or SPY, which have minute bars) and `days_to_earnings` (needs an earnings-calendar source). The table below is the original sketch; see the README Builtins table for the shipped names.

This is the main engineering cost and the main lever. Add typed, point-in-time features computed by code, so they're testable and shared by backtest and live:

| Feature (sketch) | Kind | Needed for |
|---|---|---|
| `ret(n)`, `ret_overnight`, `gap` (exists intraday) | own, daily | momentum, reversal |
| `rvol_d(n)` realized vol, `vol_pct(n, lookback)` | own, daily | regime gates |
| `ref(SYM).ret(n)`, `ref(SYM).day_change` | cross-symbol | lead-lag, relative value |
| `index(NASDAQ_COMPOSITE).ret(n)` | index (`index_daily` exists) | macro gates |
| `rel_ret(n, vs=BASKET)`, `zscore(rel_ret, lookback)` | relative | pairs / relative value |
| `days_to_earnings`, `days_since_earnings` | calendar | event drift (needs an earnings calendar source) |
| `sma_d`, `highest_d`, `lowest_d` | own, daily | trend |

Rules for adding them:

- each is declared in `dsl/builtins.py` with a doc string and a lookback;
- the validator checks that referenced symbols are in a declared `references:` list, so the broker subscribes and the history worker downloads them;
- missing inputs fail closed, the same policy as v2 `market_inputs`.

Portfolio `DAILY` gets the same values. v2 `market_inputs` already shows the pattern for external facts; generalising it is a natural next step.

Without this step, the generator can still run on today's vocabulary. Expect news-gated intraday variants and simple daily-move portfolio rules: useful, but not the "complex reasoning" asked for.

## 5. Feasibility with current data

- **Stored data today is simulated only**: 8 fictional tickers, about 60 sessions of 1-min bars and 85 daily bars, 422 news items. That's enough to build and test the machinery, not to discover anything.
- **Real data** comes through the paper broker's history worker. That gives daily bars (about 2 years by default; more is possible) and 1-min bars (190 days per request), and needs `BROKER_BACKEND=alpaca` with keys. Both come from Alpaca IEX/SIP; feed provenance isn't tracked per row (see `backtesting-research.md`).
- **Sample size is the binding constraint for a single ticker.**
  - Two years is about 500 daily observations. A daily rule with a 3-condition gate might fire 20–60 times.
  - Earnings events number about 8 per name.
  - 190 days of minute bars is about 130 sessions.

  So single-ticker discovery should **pool**: form the hypothesis on the focus ticker, but test it on the focus plus its peer group, with peer transfer as a required check. Otherwise nearly every candidate stays "anecdotal". More history (5+ years of daily bars) helps a lot and is cheap to request.
- **Compute.** Daily portfolio replays are cheap. Intraday replays over 130 sessions × N symbols are heavier and run on a 2-worker pool (`POOL`). A discovery run of about 20 hypotheses × 3 generations × about 6 runs each is about 360 backtests. That's fine for daily runs. For intraday it needs a larger pool or a dedicated queue (not measured yet; measure before sizing).
- **Model cost** (order of magnitude), with a daily budget key like the existing `idealab.quota`:
  - one Opus call for hypotheses;
  - about 1–2 compile calls per hypothesis;
  - one mutation call per generation per elite;
  - the panel (4 calls) for finalists only.

  That's roughly 50–100 calls per discovery run.

## 6. Guardrails specific to automated generation

1. **Holdout is sealed.** The orchestrator's discovery window ends before the holdout. The history request for the holdout happens only at step 7.
2. **Every trial counts.** Failures and validator rejects included. The DSR uses the count.
3. **Complexity must pay rent.** Ablation deletes conditions whose removal doesn't hurt. Cap conditions per rule (e.g. 4) and free params per file (e.g. 5).
4. **Plateau, not peak.** Params are chosen from the centre of a stable region of the grid, never the single best point.
5. **Transfer test.** A mechanism claimed for AMD must show the same sign on at least some declared peers, or it's labelled ticker-specific and needs a higher bar.
6. **No new authority.** Output is draft YAML plus a report. It inherits `execution.mode: manual`, the validator, the risk snapshot and paper-first promotion. The orchestrator can't touch the registry status, risk settings or the scenario catalog.
7. **Look-ahead audit.** All profile features and DSL features are computed from data ≤ the decision timestamp. News-gated rules use recorded signals only (existing rule).

## 7. Phased plan

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | Real-data history for a focus set (e.g. semis: NVDA, AMD, AVGO, MRVL, MU, plus QQQ/SOX), 3–5 years daily | Alpaca keys, history worker |
| 1 | `focus_profile(symbols, window)`: code-only profile JSON + dashboard view | 0 |
| 2 | Orchestrator v0 on *current* vocabulary: hypotheses → compile → base + holdout runs → ledger → report | 1, workspace |
| 3 | DSL vocabulary: cross-symbol, multi-day, vol regime, index; validator `references:`; evaluator + live feature parity tests | — (can run parallel to 1–2) |
| 4 | Full evaluation family (grid, ablation, placebo, peer transfer) + mutation loop + niche archive | 2, 3 |
| 5 | DSR, PBO/CSCV, discovery report page (ledger, survivors, failures, holdout) | 4 |

Phase 2 is the smallest end-to-end proof: it should come out in a few hundred lines on top of `idealab.py`, `authoring.compile_text` and `workspace.create_run`. It shows the loop works before the vocabulary investment.

## 8. Open questions for the owner

- Focus examples to target first: a single name (e.g. AMD) or a basket (semis vs software)?
- Intraday, daily portfolio, or both as the first target? Daily is cheaper to test and has more history.
- Is an earnings calendar source acceptable (a new external dependency), or are event features limited to recorded news?
- Budget per discovery run (model calls and wall-clock).
