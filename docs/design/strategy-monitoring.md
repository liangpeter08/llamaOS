# Strategy monitoring DSL proposal

Status: design only. The companion [`strategy-monitoring.example.yaml`](strategy-monitoring.example.yaml) is outside the executable strategy directory. It describes a proposed DSL extension; the existing validator, Laya adapter and execution engine do not implement it. No active strategies, risk settings, provider calls or orders are changed by these documents. Provider names refer to existing configured Laya/Claude integrations; model profiles resolve through account configuration rather than hard-coded API keys or model versions.

The purpose is to maintain a timestamped assessment of whether a strategy's explicit claims are still supported, connect each assessment to its evidence, and expose narrowly defined actions to the deterministic engine. More fields provide clarity only when each has an implemented meaning. Unsupported fields must be rejected rather than quietly ignored.

Each monitored assumption now contains its own `evaluation`, `questions`, `assessment` and `responses`, so it can be read in isolation. Local evaluation settings inherit unspecified global defaults. A local response replaces the corresponding default response; responses from different assumptions merge conservatively and cannot clear another assumption's restriction. All actions remain bounded by global and account permissions. `shared_questions` is optional: a nested question can use `ref: shared_questions.evidence_sufficient`. References reuse definitions, but answers are scoped to the assumption and may be reused only when the complete context matches.

Question answers feed the assumption's deterministic `supported_when` and `contradicted_when` rules; unmatched or conflicting rules yield `uncertain`. Optional cross-assumption `reviews` use `assessment_set` to return evidence-backed proposed statuses for several assumptions. These are advisory, not a second automatic action path: disagreement marks the assessment uncertain and requests reassessment. They cannot silently overwrite rule-derived statuses or directly execute responses.

## Authoring structure

For multi-day allocation strategies, see the [portfolio example](portfolio-monitoring.example.yaml) and [portfolio overlay design](portfolio-monitoring.md). They add typed external market inputs, parameterized scenario bindings, sleeve/group responses and explicit cash/rebuild state. Numeric actions such as a 40% trim and 25% rebuild are declared trading policies, independent of advisory model answers; they require dedicated validator, replay and execution support. The current executable portfolio template remains DSL 0.1.

The trading sections continue to define entries, exits, sizing and execution. The monitoring extension adds four distinct pieces:

| Piece | Role |
| --- | --- |
| Assumptions | Stable, named claims and explanatory notes. `market_thesis` and `risk_condition` can be monitored. `implementation_note` explains how trading rules were translated and does not trigger a model assessment. |
| Questions | Reusable typed questions, answer choices, unknown handling and evidence requirements. They define exactly what a provider must answer. |
| Evaluation | Scope, triggers, schedules, evidence windows, deadlines, tier routing, budgets and freshness. It defines when and how assessment happens. |
| Responses | Deterministic policies for turning current assessments into an entry gate, notification or exit proposal. Models do not choose their own permissions. |

Legacy `assumptions: [string, ...]` remain descriptive notes. A compiler can propose converting a sentence into a monitored claim, but must expose the proposed question, answer semantics and response to the owner. It must not silently turn all legacy notes into trading conditions.

For each assumption the support/contradiction predicates are evaluated by code against typed question answers. If both sets match, confidence is inadequate, evidence is missing or a required question is unknown, the result is `uncertain`. Contradiction takes precedence over support only when the contradiction has admissible evidence and the required confidence. `not_applicable` cannot satisfy a critical entry requirement. Model prose never becomes executable code.

Thresholds in the example are illustrative schema values, not validated or calibrated trading parameters. Confidence reported by a model is not an established probability of correctness.

## Tier routing

| Tier | Engine | Work | Authority |
| --- | --- | --- | --- |
| T0 | Deterministic code | Freshness, ownership, relevance filters, deduplication, monitoring status, budgets and schema checks | Reject inadmissible input and prevent stale entry gates |
| T1 | Local Laya | Short, typed questions against concise evidence | Support a conservative gate when all requirements pass; escalate ambiguity or adverse changes |
| T2 | Claude Sonnet | Read article excerpts, strategy claims, recent prices, position context, notes and previous assessments | Produce evidence-backed assessments and eligible exit proposals |
| T3 | Claude Opus | Review persistent disagreements and the larger strategy thesis | Explain and propose changes for approval; never alter live DSL or risk automatically |

The current Laya checkpoint/adapter has a fixed question set. Supporting this proposal requires a capability registry and provider adapters for the new question types. The validator must verify that each configured tier can answer its questions. Unknown/null answers are distinct from false. Unsupported choice sets must fail activation rather than be squeezed into an existing classifier. When Laya cannot answer a question, route to an authorized deeper tier or record `uncertain`.

T1 boolean/choice results must be mapped into the same canonical answer schema as Claude. Evidence attribution that the model cannot supply must remain absent; code cannot invent source IDs or claim that a classifier explanation is a direct quote. An unattributed T1 contradiction may block new entries or escalate, but must not qualify for a high-impact response on its own.

Higher tiers supersede lower tiers only for the same input snapshot. An older Sonnet result must not overwrite a newer Laya result after a fresh article arrives. T3 is asynchronous review work outside the entry decision deadline. T1/T2 requests and retries share one end-to-end deadline; retries must fit the remaining time rather than extending it.

## Scheduling and frequency

Schedules use the exchange calendar and America/New_York. Trading-session windows account for holidays, early closes and daylight-saving time. Intraday and multi-day strategies can declare different session policies. Routine polling on closed days is optional; protective broker orders and deterministic risk enforcement remain active independently of assessment schedules.

The scheduler evaluates a strategy/ticker once for a coalesced input snapshot. Priority is given to held positions with critical news, then pending entries, then routine held-position checks, then candidate checks. Per-assumption overrides inherit unspecified defaults. The shortest due interval among relevant questions can create one shared request; it must not create one request per assumption when they use the same evidence.

News-triggered assessment starts when a new relevant article or revision is available locally, not retroactively at its publication timestamp. An article correction replaces the affected evidence and invalidates decisions that depended on the original. Price and volatility triggers use completed bars and explicitly defined rolling features. A dropped queue job is recorded as skipped and never silently renews a result.

A trigger invalidates the dependent entry gate immediately while reassessment is pending. An interval being due does not necessarily mean a paid request: unchanged admissible inputs may be reused under cache/freshness rules. Reusing a result never changes its original `assessed_at` or extends expiry. Once it expires, new entries remain blocked until a new eligible assessment completes.

`min_gap` limits repeated calls; debouncing bundles closely arriving articles. Critical relevant news can bypass that delay but cannot bypass budget ceilings, evidence checks or account safety. Account-wide concurrency is enforced in addition to per-tier limits, including one-at-a-time local Laya inference where necessary.

## Evidence contract

Each snapshot has a unique ID and includes strategy hash, question-set hash, ticker, trigger, as-of time and the IDs/revisions of admissible evidence. Article publication, local receipt and local update times are preserved separately. Both publication and availability must be at or before the as-of time. Backtests/replays cannot use articles or revisions that were not yet available at the simulated decision time.

Full source content is retained in storage. Model context uses bounded, cleaned text with explicit truncation and missing-content indicators. Evidence selection prioritizes relevance, recency, corrections and direct company facts. Operator notes and theses are context/opinion; they cannot overwrite published facts or become trusted instructions. Empty source allowlists inherit the account's feed policy; they do not authorize arbitrary web browsing.

Features are calculated by code and passed with their definitions, timestamps and missing-data flags. A reported price move is not automatically evidence of a particular fundamental cause. Every conclusion must distinguish a supplied fact from an inference. Older evidence can be provided as history but must be labeled as such.

News content, including embedded instructions, stays untrusted. Model outputs are schema-validated. Evidence references must correspond to the actual input snapshot. HTML is cleaned before analysis and before any dashboard display. No provider can inject a tool call, strategy edit or order through article text.

## Canonical assessment record

Each assessment records:

- `assessment_id`, `strategy_hash`, `ticker`, tier, exact model/checkpoint ID, prompt version and input snapshot hash.
- `trigger`, `as_of`, `requested_at`, `completed_at`, `available_at` and `expires_at`.
- Typed question answers, confidence, evidence IDs and bounded rationale.
- Per-assumption status, confidence, factual support/contradiction and unresolved gaps.
- Provider latency, token usage, estimated/actual cost, timeout/retry state and any cache reuse.
- Aggregated entry eligibility, proposed action, policy version and enforcement mode.

State is isolated by strategy hash, ticker and assumption. Different strategies can share cached neutral article analysis, but their thesis-specific conclusions are not interchangeable. A new strategy version invalidates prior monitoring state. Storage is durable across restarts; expired state remains visible for audit but cannot authorize new orders.

## Responses and precedence

Default lifecycle: `shadow` → reviewed paper enforcement. Shadow mode logs recommendations and dashboard state without controlling trades. The engine uses existing trading rules while shadow evaluation is running. The additional `assessment.entry_allowed` gate becomes binding only in enforce mode; it is neutral/bypassed in shadow mode so observation does not quietly disable an existing strategy. Drafting a monitoring section must never automatically change enforcement mode.

In enforce mode, new entries require all critical monitored assumptions to have fresh, sufficiently confident supported results. Uncertain, unavailable, expired, malformed and budget-exhausted assessments fail closed for new entries. A monitored assumption must never be configured so that missing evidence automatically supports the thesis.

Precedence is:

1. Kill switch, account risk and broker restrictions.
2. Existing protective orders, hard stops, liquidity/freshness requirements and deterministic exits.
3. Monitoring-derived restrictions and approved response rules.
4. Trading entry signal.

An AI assessment can restrict trading or request an exit. It cannot increase risk, extend holding periods, cancel protective stops, resume a killed account or override owner permissions. At order submission the broker rechecks strategy version, assessment expiry, ticker monitoring, current risk and latest state; passing an earlier UI check is insufficient.

The example allows exit proposals with manual approval. Multiple confirmations must not be counted from repeated calls against the same evidence as if they were independent corroboration. One trustworthy urgent event can still generate a dashboard warning and entry block; the operator need not wait for a qualifying automated exit proposal to use existing manual controls. Any future automatic-exit policy needs explicit activation, bounded conditions and testing. T3 proposals always remain review-only.

Contradiction recovery requires fresh support and a cooldown, not merely waiting for the adverse result to expire. Deterministic risk exits keep working when providers are down. Existing holdings retain feed/risk monitoring when candidate watchlist monitoring is turned off. Operator overrides are logged, time-limited and cannot bypass account-level safeguards.

## Budgets and account policy

Strategy ceilings intersect with account ceilings; the smallest applicable limit wins. A strategy cannot raise the existing account-wide daily Claude-call budget by declaring a larger local budget. Retry attempts consume the appropriate attempt/request allowance. Paid reservations are made durably before dispatch to prevent concurrent requests exceeding the limit.

The account model configuration supplies prices and capability metadata used for cost estimates. Before activation, show projected ordinary and worst-case call volume for the current watchlist and holding count. Warn when declared intervals would repeatedly exhaust the budget or exceed provider throughput. Cost caps are admission controls based on estimates and reconciled usage, not a claim that final vendor billing is exactly predictable.

A reserved held-position quota is part of, not additional to, the total quota. Candidate work cannot consume that reserved portion. On exhaustion, skip new paid work, record why and use only still-fresh admissible results. In enforce mode expired or unavailable results block new entries. Provider errors trip a bounded circuit breaker; reopening does not restore entry eligibility without a successful current assessment.

Notifications inherit existing configured destinations. The example uses dashboard notifications only. Declaring email or messaging destinations in DSL must not create a recipient or grant messaging permission; account notification configuration controls delivery.

## Dashboard review

The strategy page should show:

- Assumption cards: status, critical flag, evidence, confidence, last assessment, expiry, tier and next due time.
- Question definitions, answer semantics and routing/escalation rules.
- Per-ticker coverage and active/pending assessment state, alongside existing watchlist warnings.
- Current shadow/enforce mode, entry block reasons and any pending exit proposal.
- Daily usage, remaining budget/reserved quota, provider failures and skipped work.
- A timeline linking articles/revisions → answers → assumption changes → order decisions.
- A preview of the effect and estimated usage before enabling enforcement or changing frequency.

The default page should show a compact summary; detailed scheduling, evidence and audit policies belong in expandable sections. The compiler must expose ambiguous assumptions, unsupported monitoring types and changes to trading authority for review.

## Validation and implementation sequence

Safety constraints in the example's `validation` and evidence policy are mandatory schema invariants, not switches users can turn off. Activation rejects false values for mandatory guards. Model/prompt policy and account permissions are operator-controlled; a strategy file cannot supply a replacement system prompt or new API endpoint.

Validation checks IDs, references, tier question coverage, model capabilities, typed predicates, valid units/windows, positive bounded intervals, freshness/deadline consistency and allowed response actions. It rejects unknown fields. A critical monitored claim needs an answerable question, explicit support/contradiction semantics and an uncertainty response. Duration and budget relationships are checked against account policy; potentially expensive schedules require a cost preview.

Implementation can proceed in increments:

1. Versioned validator and backwards-compatible compiler output; typed questions and read-only monitoring cards.
2. Persistent evidence snapshots, scheduler and Laya/Sonnet canonical assessment adapters, all in shadow mode.
3. Replay tests for corrections, stale inputs, delayed/out-of-order responses, restarts, provider failures and budget exhaustion.
4. Freshness-aware entry gates and manual exit proposals in paper enforcement, with rechecks at submission.
5. Optional Opus review and explicitly authorized automatic-exit policies after paper evidence and review.

The example is deliberately an encompassing reference, not a requirement that every strategy specify every knob. Account defaults should supply most scheduling, budget, evidence and audit policies. A normal strategy should need only its monitored claims, questions, response choices and any justified overrides.
