# Launch readiness review

This is the baseline review before the paper integration. Many findings below have since been addressed; see [current paper setup and validation](paper-trading.md).

Reviewed 2026-10-10. Scope: repository architecture, broker/session/exchange/risk code, auth and API, model pipeline and authoring, strategy lifecycle, storage, dashboard, deployment files and existing tests. This is a source and local-check review, not a deployed penetration test or an Alpaca integration certification.

## Decision

A private simulator demo is close. Real market paper trading and real-money trading are not ready. The existing broker always uses synthetic bars and SimExchange. MODE is currently a label, not an enforced execution boundary.

## Verification

- Python: 33 tests passed in 4.35 seconds, against the explicit local llamaos_test database.
- Dashboard: Next.js production build passed, including type validation and all 15 static pages. The earlier page-collection failure did not recur.
- Python dependency consistency: pip check passed. This is not a vulnerability scan.
- npm production audit against registry.npmjs.org: two affected package entries (one high, one moderate), involving PostCSS and its Next.js parent. Exploitability in this app was not established. Review and patch before external exposure; do not blindly apply a major upgrade.
- Docker deployment not executed: Docker daemon unavailable.
- No paid model requests or actual broker orders were made. No application implementation was changed by this review.

## Required before a durable private demo

1. **Include public assets in production.** services/dashboard/Dockerfile copies standalone and static output, but not public/. Copy it into the runner; verify /logo.svg and /llama-logo-v2.png in the built container.
2. **Preserve records across restarts.** services/broker/main.py:start_day deletes SESSION_TABLES (including orders, fills, trades, positions, intents and commands), current-session bars/signals/news and the current live equity history. Make demo reset an explicit command. Persist/recover the session cursor and account instead of replaying/resetting on process startup.
3. **Test deployment permissions.** Python containers run as UID 10001, but API/models write draft versions into a host bind-mounted strategies directory. The current local directory is owner-writable only (0755). Provision writable ownership/ACLs for the deployment UID and test compile/apply-diff inside containers. This is deployment-dependent, not a verified container failure.
4. **Set up the host.** Configure the real hostname/TLS path, strong database credentials, a stable session secret and production-only accounts. Remove development credentials from the deployed environment. Verify login/2FA and owner/viewer access through the actual HTTPS proxy.
5. **Patch dependencies and run a deployment smoke test.** Resolve the npm audit findings, build the images, and exercise login, dashboard reads, approvals, strategy creation and kill commands. Add CI for Python tests, dashboard build and vulnerability checks.
6. **Implement backups and restores.** Back up PostgreSQL and strategy files off-host; protect enrollment secrets and sensitive records. Restore into a clean stack and verify the audit chain. A named Docker volume alone is not a backup.

## Required before real Alpaca paper trading

7. **Build the broker/data adapter.** Ingest real bars and news, hydrate daily history, handle reconnects/gaps/duplicates/out-of-order data, and implement actual paper account, order, bracket and fill APIs. Existing core/exchange.py is simulated. Add account-setting verification (the documented broker-guard target does not exist).
8. **Enforce execution boundaries.** record_only must ingest without orders. Paper and live credentials/endpoints, strategy status and execution mode must agree. Refuse unsupported MODE values, refuse live mode until explicitly provisioned, and keep simulator data/account history separate from actual results.
9. **Make execution restart-safe.** Use persistent intents and unique broker client order IDs. Recover open orders, partial fills, positions and brackets from the broker after restart; avoid duplicate submissions and ensure only one active executor. Existing intent/order maps are in memory. Current reconciliation compares DB state just written by the simulator with the same simulated exchange, not an independent external account.
10. **Wire independent safety monitoring.** TradingSession.feed_age_s starts at zero and is never updated by the broker. Implement wall-clock freshness checks and a watchdog that still runs when no bars arrive. Verify cancel/flatten/suspend outcomes, retries and errors against the real broker; add an independent end-of-day flatten path.
11. **Implement exchange sessions.** common/timeutil.py uses weekdays, fixed 09:30–16:00 sessions and a fixed 390 bars. Add holidays, early closes, session rollover and calendar-aware flatten deadlines. The current broker waits indefinitely after its single simulated session closes.
12. **Tighten approval handling.** Broker processing checks expiry while pending, but does not recheck expiry in the approved branch. TradingSession.recheck explicitly removes the entry-window check. At actual submission verify expiry, current hard and strategy windows, current strategy eligibility, feed freshness, price drift and available account capacity.
13. **Wire alerts and actionable health.** Notify on pending approvals, rejection/fill, disconnect, stale feed, failed flatten, kill switch, model failure and backup failure. config/alerts.yaml is currently unused. API healthz always returns ok; add dependency-aware readiness and monitoring independent of this host.
14. **Verify model integrations.** Exercise configured Claude/Gemini IDs and request options with real provider responses, including timeout, refusal, malformed output and fallback. Jev is explicitly unimplemented. Add hard spend/token/concurrency caps: T2 has a call cap, but the configured Idea Lab daily cap is not enforced by idealab.run. The configured premarket/post-close schedules are not driven by a scheduler in the current worker.

## Required before real-money use

15. **Harden authentication lifecycle.** Authentication has Argon2, TOTP, CSRF and role checks, but sessions are signed cookie payloads; current() trusts the stored role and user identity without checking a server-side revocation record. Logout cannot invalidate an already copied cookie. Add revocable server-side sessions, account disable/password/TOTP rotation and a recovery process. Login throttling is in process memory and resets on restart.
16. **Make promotion evidence meaningful.** config/lifecycle.yaml currently permits zero paper sessions. Promotion counts distinct dates from all strategy trades, without a paper-execution provenance filter, and does not establish independent paper risk/performance criteria. Require actual paper evidence and an explicit approved immutable hash before live execution.
17. **Complete failure-path testing.** Existing tests cover DSL restrictions, risk checks, simulated fills, audit tampering, basic auth and replay/portfolio. They do not establish real broker behavior, full approval flow, restart recovery, stale-feed watchdogs, external reconciliation, early-close flatten, duplicate/partial order events, restore or container deployment. Add focused integration and fault-injection coverage, plus browser tests for critical actions.
18. **Strengthen storage controls.** Add versioned schema migrations and service-specific database permissions. All Python services currently share one DB env file. The hash chain detects row changes but, without an independent off-host checkpoint, cannot alone establish protection against privileged rewriting or tail truncation.
19. **Validate research integrity.** Idea Lab reuses a rolling holdout across independent runs; scoring once per idea does not prevent repeatedly testing new hypotheses against the same holdout. Preserve experiment lineage and separate final untouched validation data. Also review fade hypotheses: _holdout_score computes returns in the news direction without inverting for fade, while holdout_passed expects positive signed returns.

## Can wait beyond the first private paper release

- Jev support if a verified alternative is used.
- Automated Claude trade narratives; deterministic narratives already exist.
- Further visual polish, advanced chart interactions and broader model choices.
- Larger-scale/multi-tenant infrastructure unless launch means other users trading through this system.

## Suggested sequence

Deployment/demo reliability → real-data record-only ingestion → Alpaca paper adapter → safety/recovery/alerts and full failure tests → sustained observed paper operation → controlled live enablement.

If launch means a public product for other people, scope a separate review for tenant/account isolation, user onboarding, data licensing and applicable product obligations. Those capabilities are not implemented by the current owner/viewer prototype.
