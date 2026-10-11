# Macro, news and price scenario routing

Status: proposed extension, not implemented runtime behavior. Read alongside [strategy monitoring](strategy-monitoring.md), the [strategy example](strategy-monitoring.example.yaml), and the [58-scenario catalog](scenario-catalog.example.yaml). The existing engine does not yet have the macro feeds, scenario matcher or new assessment actions described here.

The catalog is an extensive set of configurable examples, not a claim to enumerate every possible market event. Each strategy explicitly selects relevant scenario IDs; including the library does not turn on all scenarios. Numerical thresholds are illustrative and need review/replay testing. Repeated decisions must be attributable to a named scenario and exact input snapshot.

## Laya's role

Laya acts as the fast relevance/applicability layer. It maps available evidence to typed news classifications and decides whether a pre-approved candidate scenario applies to the current strategy/ticker. Code measures price moves, verifies timestamps, generates numeric trigger candidates and enforces the action policy. Laya does not calculate exchange returns from prose or invent the order to submit.

A useful pipeline is:

1. **Ingest:** timestamp macro series, stock prices, economic calendars, company news, article revisions, positions and orders.
2. **Measure:** compute returns, consensus surprises when units are comparable, liquidity/volatility features and event-relative price windows.
3. **Quick classify:** Laya labels news scope, polarity, surprise relevance and macro/thesis context. Cache this neutral classification for reuse across strategies.
4. **Generate candidates:** use verified numeric facts and admissible typed classifications to shortlist configured scenarios. Missing values do not become zero/false. A semantic classification without adequate confidence cannot satisfy a trigger as if it were a fact.
5. **Match scenarios:** Laya answers `applicable`, `not_applicable` or `uncertain` for each shortlisted scenario against the strategy's assumptions and exposure. Keep the candidate batch bounded. This second pass is unnecessary for deterministic-only operational rules.
6. **Escalate:** Claude assesses material negative events, mixed facts, macro/company conflicts and disputed causal interpretations. Opus reviews persistent disagreements or proposed thesis changes.
7. **Resolve policy:** merge applicable restrictions, create one current entry gate and, if authorized, an exit proposal. Code rechecks and submits any approved action through existing execution/risk paths.
8. **Audit/recover:** persist the full chain and require current supported evidence before clearing a restriction.

These steps may share a request when the provider supports the canonical output schema. The current Laya adapter needs extension and evaluation before it can support arbitrary scenario questions. Failure to support a question or a timeout is recorded as `uncertain`; a generic classifier must not fabricate confidence or evidence. A model's confidence threshold is a routing rule, not a proven likelihood that the scenario is correct.

## The Nasdaq 5% example

“Nasdaq” needs a precise instrument. Nasdaq Composite, Nasdaq-100, QQQ and the Nasdaq company ticker NDAQ are different things. The example uses **Nasdaq Composite falling 5% or more from its previous regular-session close**. A 5% fall over five minutes is a separate scenario with a separate feature and threshold. A stock-specific drop is also separate.

The index series must come from an account-configured feed with the required instrument capability. The current eight-stock paper watchlist does not provide a Nasdaq index series. QQQ can only be used as an explicitly approved and labeled proxy; it must not silently stand in for the Composite. The monitor treats a missing or stale required index as unknown and blocks dependent entry gates in enforce mode.

For a fresh, verified −5% observation:

- The configured numeric guard immediately prevents new dependent entries while assessment is pending. This guard is code-based and does not wait for a model response.
- Laya receives the trigger, the strategy's direction/sector exposure, monitored claims and holding/pending-entry state. It assesses whether the configured stress scenario is applicable.
- An applicable stress response pauses the relevant strategy/ticker entries, notifies the dashboard and requests a deeper assessment. Existing holdings can produce an exit proposal under the configured authority.
- An uncertain response retains the conservative entry block and escalates. A not-applicable result cannot clear an account-level macro restriction or hard risk breach.
- Manual exit proposals require current position state and approval. If automatic scenario exits are introduced later, they require a separately reviewed policy and still pass the normal execution checks. Hard account stops and protective orders remain independent of Laya.

The regime recovery policy needs hysteresis/cooldown so the engine does not alternate between pause/resume around a threshold. Recovery from a manually paused strategy or account kill follows existing operator permissions.

## Macro conditions

Macro context should cover equity-market direction, sector relative strength, volatility, rates, currency exposure, breadth and scheduled high-impact events. Some sources are optional; any assumption that depends on an optional missing series becomes uncertain. A strategy can declare a macro series required, making the relevant gates fail closed when the series is unavailable.

The same environment can affect strategies differently. A growth-stock continuation strategy may be sensitive to a rates shock; a company contract thesis may have different exposure. Laya assesses that relationship from the declared claims and attribution. An inferred relationship is labeled as inference and should not be treated as a deterministic numeric fact.

Scheduled macro releases need event identity, actual release time, verified reference values and revisions. No consensus surprise can be calculated when consensus is absent or the quantities are incomparable. “Unexpected” and “negative” are separate labels: a scheduled report can contain an unexpected negative surprise, and an unscheduled article may have no material impact.

## Company news and expected impact

News analysis needs distinct outputs for source quality, company relevance, novelty, positive/negative/mixed direction, materiality, expectation and horizon. The catalog covers positive and negative new catalysts, expected positive/negative surprises, unscheduled adverse events, earnings/guidance conflicts, withdrawal of guidance, financing/dilution, legal/regulatory issues, product/contract changes, recalls, M&A changes, opinion, rumors and corrections.

Expected impact should include a mechanism and uncertainty, not an unsupported price target. Sonnet can explain the likely channel—cash flows, dilution, costs, liabilities, financing or sentiment—and an impact band/horizon. Actual reaction remains an observed feature, separate from the expected impact. An article tagged positive cannot alone clear a negative macro restriction or establish that a trade should be entered.

For unsupported domains, geographic exposure, uncertain legal outcomes, incomplete article bodies or unavailable fundamentals, answers should be uncertain and identify the missing input. A strategy can require direct company facts and explicitly exclude analyst opinion from its entry thesis.

## Price behavior around news

Measure pre/post-event returns, relative performance versus market/sector, volume response, gaps, reversals and VWAP distance. Keep published time and locally available time distinct. A reaction window must contain only completed bars that were available at the decision time. Windows that have not elapsed remain pending; they cannot be filled with future values during replay.

Scenarios cover:

- Positive news with supportive price/volume behavior.
- Positive news with falling relative prices or a rapidly fading initial rally.
- Negative news with a confirming decline or resilient prices.
- Little measurable response despite a seemingly material headline.
- Price moving before the article arrived locally.
- Positive company facts conflicting with a broad market selloff.
- An unexplained stock-specific drop with insufficient news evidence.

“Consistent with” does not mean “caused by.” The assessment must consider concurrent news, sector/broad-market moves, open/auction effects, halts and low liquidity. A price move cannot authorize the model to invent a negative article or a fundamental cause. If a strategy explicitly depends on causal attribution and the evidence cannot establish it, that assumption remains uncertain.

## Position-sensitive response

The same scenario has different consequences depending on state:

| State | Response options |
| --- | --- |
| Candidate only | Log, defer evaluation, require entry approval, block new entries or allow existing deterministic entry rules to be evaluated. |
| Unsubmitted intent | Invalidate/reject the stale intent; require a fresh eligible assessment before a new intent. |
| Submitted entry | Request cancellation if invalidated; account for cancel/fill races and protect any actual partial fill. |
| Held intraday position | Continue hard stop/target/flatten management, block adds/new entries and create approved exit proposals when allowed. |
| Held portfolio sleeve | Reassess its longer-horizon thesis; preserve sleeve ownership, schedule and drawdown rules. A market move does not silently convert a multi-day strategy into intraday liquidation. |
| Broker restriction/halt | Do not assume a sell can fill immediately. Preserve intent/state, notify and retry only through authorized execution rules. |

A model output cannot loosen the stop, add leverage, short a restricted instrument, increase allocation, override the monitoring switch or evade the owner approval policy. Entry-positive actions mean “evaluate the strategy's existing rules,” not “buy now.” Multiple matched scenarios produce one merged policy and idempotent actions, not several independent orders for the same shares.

Catalog action names are typed policy operations that need an implemented registry. State updates and assessment requests are internal operations; responses affecting entry eligibility, cancellation or exit proposals must be permitted by both the strategy's `monitoring.responses.allowed_actions` and account policy. A catalog cannot expand those permissions. Unsupported operations fail activation. Scenarios without a specific `laya_question` use the default matcher question with their definition and verified facts.

## Missing data and failure scenarios

The library includes missing required macro inputs, stale stock prices or clock state, provider outages/budget exhaustion, conflicting responses, asset restrictions, liquidity deterioration and account risk breaches. These paths are deterministic; no model approval is needed to enforce existing hard safety.

The catalog also covers earnings blackouts, corporate-action price adjustments, buybacks/capital return, management changes, geopolitical/supply-chain exposure, currency/commodity shocks, breadth divergence, correlated holdings, conflicting reports, fiscal-period/unit mismatches, forecast/reaction divergence and thesis/horizon expiry. These require implemented typed features and data adapters; naming a scenario does not imply the current engine has that data. For example, an unadjusted split-driven price change must not be mistaken for a catastrophic return. Additional scenarios can extend the same typed interface for order-book changes or position-level hedging exposure.

Operational context also includes restart recovery, duplicate events, stale replies, daylight-saving/session boundaries, revisions, feed outages and partial fills. Existing monitoring design describes durable snapshots, idempotency, budgets and provider circuit breakers. No generic “catch-all” instruction should let Laya decide an unapproved trading action when the catalog has no suitable scenario.

## Extensibility and review

The [portfolio worked example](portfolio-monitoring.example.yaml) binds two parameterized catalog scenarios to a −2% Nasdaq response (40% trim, then positive-day-triggered 25% rebuilding) and hardware-driven software trims. Their explicit numeric policies use `numeric_mandatory_laya_context`: Laya supplies contextual assessment but cannot veto or size them. This differs from semantic news applicability. Portfolio response parameters, group references, cash locks and recovery state require their own implemented validation/execution contract; a catalog binding cannot grant authority on its own.

Imported scenario libraries are local, bounded, schema-validated templates. Activation expands selected scenarios into a canonical strategy and pins the content hash. Editing the library creates a new effective strategy version; it must not alter already-approved behavior through a mutable path. Reject network imports, path traversal, recursive include cycles and arbitrary executable predicates.

The compiler should propose relevant scenarios from the user's request, explain every added threshold and distinguish requested rules from defaults. The owner reviews affected tickers, required feeds, authority, estimated call volume and behavior on missing evidence. Account safety invariants cannot be disabled by scenario YAML.

The first implementation should run in shadow mode. Replay validation needs each scenario's expected match/nonmatch/unknown behavior, competing triggers, missing facts and timestamp boundaries. Model evaluation needs labeled examples per scenario family and negative cases so a high apparent confidence is not mistaken for reliable routing. Paper enforcement should initially support conservative entry gates and manual exit proposals; further authority needs explicit review.
