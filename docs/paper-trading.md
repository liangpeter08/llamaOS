# Alpaca paper trading

This release sends strategy orders to Alpaca's paper endpoint only. Real-money endpoints and live promotion are refused. News and prices come from Alpaca; Laya runs locally for typed T1 decisions, Claude Haiku provides a fallback, Sonnet performs deeper reads, and Opus handles authoring/Idea Lab. No Jev credentials or calls are used.

## Local setup

Prerequisites: PostgreSQL with a persistent data directory and a role allowed to create the initial paper database, Python 3.12+, Node 20.9+, and pg_dump/pg_restore matching the database server. Local Laya downloads its checkpoint on first use and requires additional memory.

1. Put paper credentials in `secrets/broker.env` using the example; use only `https://paper-api.alpaca.markets`. Put Claude credentials in `secrets/models.env`. Files must be Git-ignored and owner-readable only. Set ANTHROPIC_WORKSPACE_ID only if your provider requires it.
2. Run `make init`, then `make paper-setup`. The latter loads real IEX daily/minute history into a separate UTF-8 database, validates/backtests strategies, and enables their paper execution. It never creates simulated equity history in this DB. Setup intentionally enables automatic paper strategy execution; set individual strategies to manual on the dashboard if you want per-trade approval.
3. Enroll the new `paper-owner` account from `secrets/paper-owner.txt` in your authenticator. This file contains the initial password and enrollment secret; do not share or commit it.
4. Run `make paper` (builds the production dashboard) and open http://localhost:3000. The launcher starts API, dashboard, models, paper broker, independent watchdog and operations worker. Each service loads only its own credential file.

`make dev` still runs the separate simulator against the original development environment. Do not start both stacks on the same ports. `secrets/paper.env` contains the paper DB configuration and stable session secret. Override PAPER_DATABASE_URL during setup if required; the database name must contain `paper`. Use MODE=record_only to prevent new entries while ingesting data; existing paper positions still retain safety exits.

## Execution and recovery

- Only registered strategies in paper status execute. Every order passes hard risk, current trading windows, calendar flatten, fresh data/price, drift, account restrictions and buying-power checks.
- Entries are whole-share, two-decimal limit brackets, with stops/targets held by Alpaca. No extended-hours entries.
- Each durable intent has a deterministic client order ID. POST timeouts recover by this ID. Unresolved submission uncertainty blocks that symbol rather than blindly retrying.
- Orders and incremental fills are reconciled from actual broker responses. Restarts preserve trades and fills. A database advisory lock prevents two executors on the same DB.
- Unknown positions/orders block entries. Use this paper account exclusively for llamaOS; do not mix unrelated manual orders.
- REST polling records catch-up bars but creates entries only from a fresh latest completed bar, never historical catch-up signals. The free IEX feed is sparse and differs from consolidated volume; signals may fail volume filters. Historical backtests are diagnostics, not evidence of live profitability or guaranteed paper trade frequency.
- The watchdog runs separately and checks broker heartbeat, feed freshness and the actual exchange calendar. It cancels/flattens on persistent failures and sets a durable kill switch. It retries failed requests; no local software can guarantee an exit while the external API or network is unavailable.
- Normal and early-close sessions flatten five minutes before the broker calendar close, or the earlier hard flatten deadline. The system rolls sessions on the clock date and does not evaluate entries on closed days.
- Pause blocks new entries while exits continue. The kill switch persists across restarts. Investigate before resetting it on the host; load the paper environment when running reset-kill-switch.

## Models and operations

Configured ceilings: 200 paid model attempts/day, 30 paid deep reads/day, four Idea Lab runs/day, bounded context/output. These are request limits; reported dollars are estimates. Local Laya outputs are advisory and need domain calibration; they cannot size or submit orders.

Premarket/post-close Idea Lab runs use the configured schedule and persistent daily run markers. They require recorded events and a running models process. A heartbeat indicates process liveness; the System page also displays time since the last completed triage batch.

The operations worker records alerts durably. Phone/email delivery is optional: configure `secrets/alerts.env` from its example. Without a recipient, alerts remain available on the dashboard/audit log. API tokens and targets are not created automatically.

`make backup` writes PostgreSQL, strategy files and an audit checkpoint under `data/backups/`; the worker repeats daily. Set PG_DUMP if the binary is outside PATH. Set BACKUP_TARGET to an SSH rsync destination for off-host copies. Local backups are not off-host protection. A restore into a separate database was checked during implementation, including audit-chain verification.

## Deployment

Docker Compose includes the paper broker, watchdog, operations worker, Laya-capable models and dashboard public assets. Set real TLS hostname and secrets, configure APP_UID/APP_GID to a host identity that can write `strategies/` and `data/`, and create both directories. For a new deployment use a fresh database/volume; never point the paper executor at a seeded simulator database. Provision the initial owner and promote validated strategies through the dashboard or the paper setup flow.

Allow about 4–6 GB RAM for local Laya and the stack. Docker builds/deployment need verification on the target host; the local Docker daemon was unavailable during this work. Keep this private behind HTTPS/VPN. The currently running local PostgreSQL instance must be provisioned as a persistent service before relying on unattended operation across a host restart.

## Validation completed

Python simulator/auth/storage tests and paper adapter/recovery tests pass; dashboard production build and TypeScript pass; npm production audit reports no known vulnerabilities. Actual Alpaca account, clock, calendar, assets, bar/news reads and Claude/Laya inference were verified. Market-hours order execution was not observed during implementation because the market was closed; the first actual entry still depends on a qualifying strategy signal.
