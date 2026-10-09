<img src="services/dashboard/public/logo.svg" alt="llamaOS logo" width="88">

# News-Driven Day Trading System (Alpaca)

**Version:** 0.3 (working prototype)
**Status:** runs end to end against a **market simulator**: news triage, signals, strategies, risk guard, approvals, fills, backtests, Idea Lab and the dashboard. It isn't connected to Alpaca yet; see [Prototype status](#prototype-status).

An automated intraday trading system for US equities on Alpaca. It turns news, real market events, and your own notes and assumptions into small, auditable trading strategies. Models (Jev, a lightweight Gemini, Claude) interpret news and propose ideas. Deterministic code evaluates the rules, enforces the hard risk limits, and places orders, with you approving strategies and, optionally, every trade.

> **Core idea:** models write features, ideas and rules; deterministic code makes trades.

---

## Contents

1. [Prototype status](#prototype-status)
2. [Principles](#principles)
3. [Architecture](#architecture)
4. [Services (Docker)](#services-docker)
5. [Model layers](#model-layers)
6. [Idea Lab: event-driven discovery](#idea-lab-event-driven-discovery)
7. [Strategy lifecycle](#strategy-lifecycle)
8. [Dashboard](#dashboard)
9. [Trade approval](#trade-approval)
10. [Login and security](#login-and-security)
11. [Hard risk rules](#hard-risk-rules-riskyaml)
12. [Strategy DSL](#strategy-dsl-v01)
13. [Quick start](#quick-start)
14. [Configuration](#configuration)
15. [Repository layout](#repository-layout)
16. [Known constraints](#known-constraints)
17. [Roadmap](#roadmap)

---

## Prototype status

**Run it locally**

Needs Python 3.12+, Node 20.9+, and PostgreSQL 16 on `127.0.0.1:5432` with user `llamaos`, password `llamaos` and a UTF-8 database `llamaos` (override with `DATABASE_URL` in `.env`).

```bash
make init        # venv + pip, npm install, config/risk.yaml, .env with a SESSION_SECRET
make demo        # 60 simulated sessions of history, recorded signals, dev owner, example strategies in paper
make replay      # rebuild the equity history: replay the running strategies over stored sessions
make dev         # broker + models + api + Next.js dashboard -> http://localhost:3000
make dev-code    # current 2FA code for the dev owner (credentials in secrets/dev-owner.txt)
make test        # pytest against the llamaos_test database
```

| Area | State |
|---|---|
| Market data and execution | **Simulated.** `common/sim.py` replays one synthetic session (fictional tickers) bar by bar, with news whose price impact depends on the catalyst. Fills go through `core/exchange.py`: next-bar open, marketable limit, broker-side bracket, stop assumed first. No Alpaca adapter yet. |
| News triage | T0 code filters, then the T1/T2 tiers. With no API keys, an offline keyword model (`heuristic@v1`) answers both tiers, so the whole pipeline runs for free. Claude Haiku 5.5 (T1) and Sonnet 5.5 (T2) take over when `ANTHROPIC_API_KEY` is set. The Gemini adapter is written but untested. Jev isn't implemented. |
| DSL | Parser, validator (all 8 rules) and evaluator are done, with code-generated back-translation. |
| Risk guard, approvals, kill switch | Done. Approvals expire and re-check the risk guard and price drift. The kill switch is reset only on the host. Reconciliation runs every minute. |
| Backtester | Uses the same `TradingSession` as the live loop. News-gated rules read recorded signals only. |
| Idea Lab | Event studies, holdout scoring, the hypothesis counter, theses, and an offline rules generator. Claude Opus 5.5 hypotheses need a key. Accepted ideas compile into draft strategies. |
| Claude authoring | Strategy chat, the English→DSL compiler and diff acceptance are written against the Anthropic SDK (Opus 5.5, adaptive thinking, server-side refusal fallback). They haven't been exercised without a key; the dashboard says when they're unavailable. |
| Dashboard | Next.js 15 / React 19 (TypeScript) on Node. It proxies `/api/*` to a Python JSON API that holds auth (argon2 + TOTP), CSRF and every write. |
| Storage | PostgreSQL (psycopg 3), with an advisory-locked, hash-chained audit log. |
| Not built | Alpaca adapter and `make broker-guard`, notifications, backups, record-only mode, market calendar and holidays, post-trade narratives by Claude (a deterministic narrative is written instead). |

---

## Principles

1. **Models are advisory.** Model outputs are typed, bounded, timestamped values: signals, idea drafts, and strategy drafts. A signal can block an entry or trigger an exit. It can never size a position, move a stop, or place an order.
2. **The live loop has no LLM.** Bars → rule engine → risk guard → (optional) your approval → executor. If every model is down, signals go stale, gated entries stop, and exits and broker-side stops keep working.
3. **Hard rules sit outside every model's reach.** `risk.yaml` is mounted read-only into every container. The dashboard can display it but can't edit it, and key limits are mirrored in Alpaca's account settings.
4. **Credentials follow trust.**
   - Only `broker` holds Alpaca keys.
   - Only `models` holds model API keys.
   - `api` and `dashboard` hold neither.
5. **Everything is replayable and logged.** Model outputs are recorded once and never recomputed. Every news item, signal, idea, strategy version, chat, approval, order and fill is written to a hash-chained audit log.
6. **The edge isn't speed.** Interpretation of real events plus your context, confirmed by price. Holding periods run from minutes to hours, and positions are flat by the close.

---

## Architecture

```text
[Alpaca]  bars WS · news WS · Trading API
   ^  |
   |  v
[broker]  ingest -> features -> RULE ENGINE -> RISK GUARD -> APPROVAL GATE -> EXECUTOR
   |  ^   (no LLM; holds the only Alpaca keys)
   v  |
[PostgreSQL]  raw news · bars · signals · ideas · strategies · approvals · audit
   ^  |                                       ^  |
   |  v                                       |  v
[models]  T0 code -> T1 quick layer           [api]  login · 2FA · approvals · trading rules (JSON)
                                                ^
                                                |  /api/* proxied
                                              [dashboard]  Next.js + React (Node)
          (Jev | Gemini lite) -> T2 Claude           strategy chat · idea lab · kill switch
          Idea Lab · compiler · chat API  <-------- internal HTTP (chat, compile, ideas)
          (holds model keys only)                    (holds no API keys)
```

There are three lanes:

| Lane | Speed | What runs | Can a model act here? |
|---|---|---|---|
| **Authoring** | offline | Idea Lab, strategy compiler, validator, backtester | Models draft. You approve |
| **Slow path** | seconds–minutes | News triage, note parsing, signal writing | Models write advisory signals only |
| **Fast path** | every bar close | Rule engine, risk guard, approval gate, executor | No |

---

## Services (Docker)

All services run on one small VPS via Docker Compose and share one PostgreSQL database. The broker, models and API talk only through the database, plus the internal models HTTP API.

| Service | Image base | Holds | Responsibilities |
|---|---|---|---|
| `broker` | python:3.12-slim | Alpaca keys | Bars and news WebSockets, feature calculation, rule engine, risk guard, approval gate, executor, reconciliation, EOD flatten, kill switch |
| `models` | python:3.12-slim | Anthropic, Jev, Gemini keys | Tiered news triage, note/thesis parsing, Idea Lab, strategy compiler, back-translation, strategy chat API (internal only) |
| `postgres` | postgres:16-alpine | DB password | Bars, news, signals, registry, intents, orders, trades, audit chain |
| `api` | python:3.12-slim | Session secret only | JSON API for the dashboard: login + TOTP, CSRF, approvals, lifecycle, backtests, Idea Lab reads |
| `dashboard` | node:22-alpine | Nothing | Next.js / React UI. Proxies `/api/*` to `api`; the only service Caddy talks to |
| `caddy` | caddy:2-alpine | TLS certs | HTTPS reverse proxy. The only container with a published port |
| `backup` | alpine | Backup target creds | Nightly `pg_dump` + audit log copy off-host (not built yet) |

See [`docker-compose.yml`](docker-compose.yml). `risk.yaml` is mounted read-only into every service, model keys live only in `secrets/models.env`, and only Caddy publishes a port.

The whole stack is designed to fit in about 2 GB RAM and 1–2 vCPUs. Nothing heavy runs locally: all models are API calls, and there's no vector DB. A Postgres full-text index covers "recent headlines for this ticker".

---

## Model layers

| Tier | Provider | Used for | Output |
|---|---|---|---|
| **T0** | Code | Universe filter, dedupe (normalized-headline hash over 6h), recap/roundup removal | keep / drop |
| **T1 quick layer** | **Jev** (typed decisions) | Fixed typed questions per story: new info? catalyst? direction? materiality? | Enum answers + confidence |
| | **Gemini lightweight** (configurable model ID) | Quick decisions that need short text: note and thesis parsing, one-line summaries, routing | Schema-checked JSON |
| | Claude Haiku 5.5 (fallback) | Used when the other T1 providers are unavailable | Schema-checked JSON |
| **T2** | **Claude Sonnet 5.5** | Deep read of escalated stories with recent headlines and your notes | Signal contract + short rationale |
| **Authoring** | **Claude Opus 5.5** | Idea Lab hypotheses, strategy compilation, strategy chat | Ideas, DSL drafts, answers |

**Escalation from T1 to T2** happens when any of these is true:
- a key answer comes back with low confidence;
- materiality is high;
- the ticker has an active note or thesis from you;
- the T1 providers disagree.

**Routing.** All T1 calls go through one `QuickLayer.decide(item, questions)` interface. `config/models.yaml` maps each question set to a provider, so Jev and Gemini are swappable per task without code changes.

**Budget targets.** Alpaca's news stream carries roughly 600–900 items/day. The targets are no more than about 30 T2 calls/day and a capped number of Idea Lab runs. Per-provider call counts and cost appear on the dashboard.

**Untrusted input.** News text is data, never instructions. Defenses are structural:
- typed outputs;
- signals can only gate entries or trigger exits;
- every entry needs a price confirmation that no text can supply.

---

## Idea Lab: event-driven discovery

Claude discovers patterns and proposes strategies grounded in **real recorded events**. It works together with **your own assumptions**.

### Inputs

- **Events:** every news item with its signals (catalyst, direction, materiality, fundamental flag), from the point-in-time store.
- **Market reactions:** code-computed event studies. These are forward returns at 5/15/60 min and to the close after each event, grouped by catalyst, sector, gap size, and time of day.
- **Your theses (your assumptions):** beliefs you enter on the dashboard. Examples:
  - "FDA approvals in small caps fade after the first hour"
  - "Downgrades of mega-caps rarely hold below VWAP"

  Each thesis carries tags, a confidence, and an expiry. Claude must test your theses and may not override them silently.

### Loop

1. **Code measures.** Event studies, base rates and sample sizes are all computed deterministically. Claude does not crunch numbers.
2. **Claude interprets.** It reads the event studies and your theses, then proposes hypotheses. Each one cites:
   - the event IDs it is based on;
   - the sample size;
   - a mechanism ("why would this happen?");
   - what would falsify it.
3. **You curate.** On the dashboard you can accept, reject, edit, or attach your own thesis to an idea.
4. **The compiler drafts the DSL.** An accepted idea becomes a draft strategy with `assumptions` listed. Your theses appear as named assumptions so you can see where they shaped the rules.
5. **It enters the normal [strategy lifecycle](#strategy-lifecycle).**

### Overfitting guards

- Every hypothesis Claude generates is counted. The dashboard shows "tested N hypotheses, M survived", so you can discount lucky finds.
- Each discovery run reserves a time-based holdout. Ideas are scored on it only once.
- An idea backed by fewer than the minimum number of events is labelled *anecdotal* and can't be promoted. The minimum is set in `config/idealab.yaml`.
- Ideas must be general rules, not one-off bets on today's story. One-off views go in notes.

### Schedule

| Run | When (ET) | Purpose |
|---|---|---|
| Pre-market brief | 08:30 | Overnight events, active theses, and which approved strategies are likely in play |
| Post-close review | 16:30 | Today's events vs reactions, idea updates, and drift in live strategies |
| On demand | any time | "Look for patterns in X" from the dashboard |

---

## Strategy lifecycle

```text
idea (Claude or you) -> DSL draft -> validator -> backtest -> paper -> APPROVED -> live
                              ^                                         |
                              +---- new version (chat, edits, drift) ---+
```

| Stage | Gate |
|---|---|
| Draft | Validator passes: schema, declared names, signal fencing, limits within `risk.yaml` |
| Backtested | Price-only logic uses full history. News-gated logic uses recorded signals only, to avoid model look-ahead |
| Paper | Minimum paper period (configurable). Live results compared with the backtest |
| Live | You approve the file's SHA-256 hash after reviewing its assumptions and an independent back-translation |
| Retired | Manually, or auto-paused on drift or breach of a strategy limit |

Status and approvals live in a registry keyed by the file's hash, so promoting a strategy never changes it. Any edit creates a new version that starts again at Draft.

---

## Dashboard

The dashboard is a Next.js app (`services/dashboard`) behind HTTPS. Pages:

| Page | What you see / do |
|---|---|
| **Overview → Portfolio** | The whole account over time: return % with a per-strategy overlay (each strategy's P&L as a % of the account, summing to Total) or equity $, presets 1W/1M/3M/YTD/All plus a custom date range, drawdown, best/worst day, per-strategy contribution table, per-session table |
| **Overview → Today** | The current session: equity chart, day P&L vs the daily loss limit, exposure, open positions, pending approvals, system health, kill switch |
| **Portfolio** | Positions, open orders and bracket legs, fills, P&L by strategy/day/symbol, exposure vs caps |
| **Strategies** | Every strategy and version with its status, live vs paper vs backtest metrics, drift alerts, assumptions awaiting your OK, and a diff between versions |
| **Strategy detail → Ask Claude** | A fresh Claude chat seeded with the strategy file, back-translation, backtest report, paper/live results and recent decision records. Ask "does this make sense?", challenge assumptions, request what-ifs |
| **Idea Lab** | Proposed ideas with evidence (events, sample sizes, reactions), the hypothesis counter, your theses, and accept/reject/edit actions |
| **Theses** | Add, edit and expire your own assumptions. Shows which ideas and strategies each thesis influenced |
| **Trade approvals** | The queue of order intents awaiting your decision (see below) |
| **Trade review** | Each closed trade: decision record (every condition and its value), signals and rationale used, risk checks, fills, outcome, and Claude's post-trade narrative. Per-trade chat is available too |
| **News & signals** | Live feed with tier decisions (dropped / T1 / escalated), signals written, and model rationale |
| **System** | WebSocket status, last-bar age, feed lag, reconciliation status, model call counts, latency, cost and errors, container health, backup status |
| **Audit log** | Searchable, hash-chain verified, exportable |
| **Risk** | Read-only view of `risk.yaml` and the mirrored Alpaca account settings, with checksum |

### Strategy chat rules

- Each chat is a **new Claude conversation** with the strategy's context. Transcripts are saved to the audit log.
- Claude can explain, critique, and **propose a change as a DSL diff**. Accepting a proposal creates a new draft version. Chat can never edit an approved version, approve anything, or touch `risk.yaml`.
- Claude's answers about performance must cite the decision records or reports they rely on.

The front end is Next.js 15 with React 19 and TypeScript, running on Node. It polls the API, draws charts as dependency-free SVG, and holds no secrets. Trading rules (validator, backtester, risk guard) stay in Python, so the dashboard and the broker run the same code.

---

## Trade approval

Each strategy has an **execution mode**:

| Mode | Behavior |
|---|---|
| `manual` (default for new live strategies) | Every order intent waits in the approval queue for you |
| `auto` | Orders go straight to the executor after the risk guard |
| `paper` | Executes against Alpaca paper only |

### How a manual approval works

1. The rule engine emits an **order intent**. The risk guard checks it. If the intent passes, it enters the queue with an expiry (default 90 seconds).
2. The approval card shows:
   - symbol and side;
   - quantity, entry, stop and target, with the risk in $ and as a % of equity;
   - every condition and its value;
   - the signals and their rationale;
   - a mini chart;
   - current exposure vs caps.
3. You **approve** or **reject** the intent, with an optional reason. You can also ask Claude about it before deciding.
4. On approval, `broker` **re-runs the risk guard** and a **price-drift check**. If the price has moved more than `max_drift` from the intent, or the cards no longer pass the rules, the order is rejected automatically.
5. If the intent isn't approved before it expires, it expires and is logged. Nothing executes by default.

Approvals and rejections are recorded with the user, time, and reason. These records become training data for reviewing which of your overrides helped.

Notifications for new approvals, fills, halts, and drift go to your phone (configurable: Telegram or email).

---

## Login and security

- **Accounts:** an `owner` role (full control) and a `viewer` role (read-only), both set up in the dashboard. No public sign-up.
- **Authentication:** password (argon2) plus **TOTP 2FA**, which is mandatory for `owner`. Sessions are short-lived with secure HttpOnly cookies, and login attempts are rate-limited.
- **2FA at login only.** Once logged in, an owner approves trades, approves strategy hashes, switches execution modes and triggers the kill switch without entering another code. Every such action still needs a valid session and CSRF token, and is written to the audit log with the user's name. The 8-hour session limit applies.
- **Tightening is easy, loosening is hard.** The dashboard can pause strategies and trigger the kill switch. It cannot raise limits or reset the kill switch: those require editing `risk.yaml` on the host and running `make reset-kill-switch` over SSH.
- **Network:** only Caddy (HTTPS) is exposed. The `models` API is internal to the Docker network. Consider restricting the dashboard to a VPN or Tailscale.
- **Secrets:** one env file per service, outside the repo and never baked into images.

---

## Hard risk rules (`risk.yaml`)

`risk.yaml` is owned by you and mounted read-only into every container. Its checksum is logged at startup and shown on the dashboard. A strategy can only tighten these limits; violations are rejected, never clamped.

```yaml
account:
  max_gross_exposure: 1.0      # x equity, i.e. no leverage. Mirrored at Alpaca (margin multiplier 1)
  allow_short: false           # mirrored at Alpaca (no-shorting flag)
  max_open_positions: 3
  max_trades_per_day: 10       # the only frequency brake (Alpaca no longer enforces PDT)
  max_daily_loss: 2%           # breach -> kill switch
per_trade:
  default_risk: 0.25%
  max_risk: 1%
  max_position: 10%            # notional, of equity
  require_bracket: true        # stop and target live at the broker
execution:
  entry_order: marketable_limit
  max_slippage: 0.1%
  cancel_unfilled_after: 2min
  approval_expiry: 90s
  max_drift: 0.3%              # price move allowed between intent and approval
market:
  entries: "09:35-15:30"       # ET; strategies can only narrow this
  flatten_by: "15:55"          # clamped on early-close days (Alpaca calendar)
  min_price: 5
  min_adv: 10_000_000
  stale_feed_halt: 3min
  reconcile_every: 1min        # mismatch -> kill switch
```

**Kill switch.** Triggers: a daily-loss breach, a reconciliation mismatch, a stale feed that persists, or you (via the dashboard). It does three things, in this order:

1. Cancel all orders.
2. Close all positions.
3. Set Alpaca's suspend flag.

Resetting it is host-only.

A backup flatten job on a separate host at 15:57 ET is recommended.

---

## Strategy DSL (v0.1)

Strategies are YAML files with a small expression language. Hard limits are not part of the language, and there is no leverage field: a leverage request becomes a compiler warning.

### Rules (enforced by the validator)

1. **Closed language.** No loops, user functions, or imports. YAML anchors, aliases, tags and duplicate keys are rejected. Times are quoted strings.
2. **Declared names only.** Every name is a param, var, declared signal, or versioned builtin.
3. **Thresholds are params** with declared ranges. Comparisons against bare numbers are rejected.
4. **Signals are fenced.** `news.*` and `note.*` may appear only in `entry.when_all` and `exit.when_any`, including indirect use through `vars`. They can say *no* or *get out*, never *bigger*.
5. **Missing data fails closed.** A condition line with a missing, stale, or not-yet-computable value is false, unless the signal declares a `default`.
6. **Limits only tighten** `risk.yaml`.
7. **Fixed timing.** Evaluation at bar close on completed bars. Stop and target are set once at order time as a bracket. Backtests fill at the next bar's open plus slippage.
8. **Provenance in the file, status in the registry.** The file carries your verbatim request, the compiler model and prompt version, `assumptions`, and `suggestions`. Status and approvals are keyed by the file's hash in the registry.

### Builtins

| Group | Builtins |
|---|---|
| Bars | `open high low close volume`, with `x[n]` meaning n bars back |
| Session | `vwap`, `day_open`, `day_high`, `day_low`, `prior_close`, `gap`, `or_high(min)`, `or_low(min)` |
| Indicators | `sma`, `ema`, `atr`, `rsi`, `highest`, `lowest`, `rvol(days)` |
| Daily | `adv(days)`, `atr_d(n)` |
| Events | `crosses_above`, `crosses_below`, `streak(cond)` |
| Position (exits only) | `entry_price`, `risk_per_share`, `bars_held` |

### Signals (`news_agent@v1`)

| Signal | Values |
|---|---|
| `news.score` | number, −1 to 1 |
| `news.catalyst` | earnings, guidance, analyst_upgrade, analyst_downgrade, product, contract, m_and_a, legal, macro, other |
| `news.materiality` | low, medium, high |
| `news.fundamental` | bool; once true, stays true for the session |
| `news.red_flag` | bool; once true, stays true for the session |
| `note.stance` | bullish, bearish, neutral, avoid |

### Example

The plain-English request:

> On liquid stocks with fresh, positive company-specific news this morning (earnings, guidance, product or contract news, not analyst notes), buy if price breaks above the first 15-minute high on strong volume. Stop just below the opening-range low, take profit at 2R, flat by 3:50pm. Risk 0.5% per trade, max 3 trades a day.

What the compiler produces:

```yaml
dsl: 0.1
id: orb_news_long
version: 1
source:
  text: "On liquid stocks with fresh, positive company-specific news…"   # verbatim, in full
  compiled_by: claude-opus-5-5
  prompt: compiler@v1
assumptions:                     # everything you didn't state; each needs your OK
  - liquid = prior close >= $10 and 20-day avg dollar volume >= $50M
  - fresh = received since yesterday's 16:00 ET close
  - positive = news.score >= 0.6
  - strong volume = rvol(20) >= 2.0
  - breaks above = a 1-min bar CLOSES above the range high, on that bar only
  - just below = 0.1% under the range low
  - no entry cutoff stated -> 09:45-11:30 ET (flagged)
suggestions:                     # proposed, NOT applied
  - add `close > vwap` to entry
universe:
  from: {news_today: true}
  filters:
    - prior_close >= min_price
    - adv(20) >= min_adv
timeframe: 1min
params:
  min_price:   {value: 10,         range: [5, 50]}
  min_adv:     {value: 50_000_000, range: [10_000_000, 500_000_000]}
  or_minutes:  {value: 15,         range: [5, 30]}
  rvol_min:    {value: 2.0,        range: [1.2, 5.0]}
  score_min:   {value: 0.6,        range: [0.3, 0.9]}
  stop_buffer: {value: 0.1%,       range: [0%, 0.5%]}
  target_r:    {value: 2.0,        range: [1.0, 4.0]}
signals:
  news.score:    {from: news_agent@v1, max_age: since_prior_close}
  news.catalyst: {from: news_agent@v1, max_age: since_prior_close}
vars:
  range_high: or_high(or_minutes)
  stop_level: or_low(or_minutes) * (1 - stop_buffer)
entry:
  side: long
  window: "09:45-11:30"
  when_all:
    - crosses_above(close, range_high)
    - rvol(20) >= rvol_min
    - news.score >= score_min
    - news.catalyst in [earnings, guidance, product, contract]
sizing:
  risk_per_trade: 0.5%
exit:
  stop: stop_level
  target: entry_price + target_r * risk_per_share
  flatten_at: "15:50"
execution:
  mode: manual                   # manual | auto | paper; the registry starts at manual, the owner switches modes on the dashboard
limits:
  max_trades_per_day: 3
```

---

## Quick start

Local development is covered under [Prototype status](#prototype-status). To deploy with Docker:

```bash
git clone <repo> && cd <repo>
for f in postgres db broker models web; do cp secrets/$f.env.example secrets/$f.env; done   # then fill them in
cp config/risk.example.yaml config/risk.yaml        # review every line
docker compose up -d --build                          # the broker seeds simulated history on first start
docker compose exec api python -m services.api.create_owner --username admin   # prints the TOTP secret
```

Then:

1. Open `https://<your-host>`, log in, and enroll 2FA.
2. Leave the system in **record-only** mode for a while, so the news pipeline builds a point-in-time signal history before any strategy trades.
3. Set Alpaca account settings to margin multiplier 1 and no-shorting. Run `make broker-guard` to apply and verify them.

---

## Configuration

| File | Purpose |
|---|---|
| `config/risk.yaml` | Hard limits (read-only to the app) |
| `config/models.yaml` | Provider per tier and task (Jev / Gemini lite / Haiku / Sonnet / Opus), model IDs, timeouts, daily budgets |
| `config/idealab.yaml` | Event-study windows, minimum sample sizes, holdout length, run schedule |
| `config/universe.yaml` | Watchlists and universe size cap |
| `config/alerts.yaml` | Telegram or email notification targets |
| `secrets/*.env` | One env file per service |

Key environment variables:

| Variable | Service | Notes |
|---|---|---|
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true` | broker | Paper by default |
| `ALPACA_DATA_FEED=iex` | broker | Switch to `sip` with Algo Trader Plus before volume-based rules go live |
| `ANTHROPIC_API_KEY` | models | |
| `JEV_API_KEY`, `JEV_MODEL` | models | |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | models | A lightweight Gemini model ID |
| `SESSION_SECRET` | api | Keep it stable, or every restart logs everyone out |
| `DATABASE_URL` | broker, models, api | PostgreSQL DSN |
| `MODELS_URL`, `API_URL` | api, dashboard | Internal service URLs |
| `MODE=record_only` | all | `record_only` → `paper` → `live` |

---

## Repository layout

```text
.
├── docker-compose.yml
├── config/                 # risk.yaml, models.yaml, idealab.yaml, Caddyfile, ...
├── secrets/                # *.env (git-ignored), *.env.example
├── strategies/             # DSL files: <id>.v<n>.yaml
├── services/
│   ├── broker/             # ingest, features, rule engine, risk guard, approval gate, executor
│   ├── models/             # triage tiers, quick layer, idea lab, compiler, chat API
│   ├── api/                # JSON API: auth + 2FA, approvals, lifecycle (no keys)
│   └── dashboard/          # Next.js + React frontend (Node)
├── core/                   # rule engine, risk guard, simulated exchange, trading session
├── common/                 # config, Postgres access, audit chain, simulator, registry
├── scripts/                # dev launcher, dev owner, demo setup, kill-switch reset
├── dsl/                    # grammar, builtins library (versioned), validator, evaluator
├── backtest/               # vectorized replay using dsl/ evaluator
├── prompts/                # versioned prompts: news_agent@v1, compiler@v1, idealab@v1, chat@v1
└── tests/                  # DSL fixtures, risk guard, approval flow, look-ahead checks
```

---

## Known constraints

- **Data feed.** Alpaca's free plan streams real-time equities from IEX only. RVOL and VWAP will differ from the consolidated tape. Use the same feed in backtest and live.
- **No PDT throttle.** Alpaca no longer enforces the pattern-day-trader rule. `max_trades_per_day` is the only frequency brake.
- **Model look-ahead.** Models know what happened after historical news. News-gated strategies and Idea Lab statistics use recorded signals only.
- **Jev availability.** Jev is in early access, so keep the quick layer swappable.
- **Manual approval latency.** Per-trade approval suits strategies holding minutes to hours, not scalps. Expiry and the drift check protect against stale approvals.
- **Single host.** One PostgreSQL instance on the same VPS. Services coordinate through tables (commands, intents) rather than a message bus.
- **Simulator calendar.** Simulated sessions are weekdays without holidays or early closes.

---

## Roadmap

- [x] DSL parser, validator and evaluator, with the example strategies as test fixtures
- [x] Broker service on the simulator: reconciliation, kill switch
- [ ] Alpaca adapter: bars/news WebSockets, bracket orders, account guard, record-only mode
- [x] Models service: T0/T1/T2 triage (offline model + Claude)
- [ ] Jev adapter; Gemini adapter verified against the live API
- [x] Dashboard (Next.js) + API: auth + 2FA, overview, system health, news & signals
- [x] Backtester and simulated paper trading
- [x] Trade approval queue and per-strategy execution modes
- [x] Strategy chat and trade review (chat needs `ANTHROPIC_API_KEY`)
- [x] Idea Lab: event studies, theses, hypothesis counter, holdout scoring
- [ ] Notifications, backups, market calendar

---

*Not financial advice. Trading involves risk of loss. Start in paper mode.*
