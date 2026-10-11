# Portfolio overlays: the big-tech worked example

Status: implemented as portfolio DSL 0.2. The executable version is [`strategies/big_tech_software_tilt_hold.v2.yaml`](../../strategies/big_tech_software_tilt_hold.v2.yaml); it differs from [the original proposal](portfolio-monitoring.example.yaml) only in `dsl: "0.2"` and `monitoring.scenarios.catalog: scenario-catalog.yaml` (the executable catalog is `config/scenario-catalog.yaml`). The v1 strategy and the 0.1 compiler example are unchanged and still valid. Where the code lives: validator `dsl/portfolio_v2.py`; engine `core/portfolio_v2.py` (shared by the backtester and the paper desk); index feed `services/broker/index_feed.py` with `config/market_inputs.yaml`; monitored assumptions and overlay context `services/models/assumption_monitor.py`; tests `tests/test_portfolio_v2.py`. The example file starts in shadow mode, so its overlays log decisions without trading until a version declares `mode: enforce` and the owner enables enforcement on the strategy page.

## Translating the supplied text

The trading request becomes `source.text`. The pasted prior assistant dialogue supplies candidate defaults, not confirmations. Its claims about available bars, strategy validity, capital availability and earnings dates must be checked independently before execution. The v1 file exists, but this document does not assert its registry or backtest status.

The draft retains the ten held tickers, proposes 45% capital and 25%-of-original-allocation adds, and uses 42 exchange sessions starting on the first funded session on or after October 12, 2026. It does not combine a fixed December end date with the session horizon. Group weights imply 14% of sleeve capital per software name and 6% per other name. At 45% initial capital those are 6.3% and 2.7% of inception equity; the 150% allocation caps correspond to 9.45% and 4.05% at inception. These are allocation arithmetic, not guarantees that subsequent price appreciation or equity changes stay inside account risk limits. Available cash and other sleeves still need checking at submission.

AMD is an additional monitored ticker, not a held ticker or silently accepted TSLA replacement. The hardware return basket is explicitly NVDA, AAPL, AVGO and AMD; it differs from the `other_big_tech` allocation group, which includes AMZN and TSLA. These classification and aggregation choices remain visible proposed defaults for review.

## What the template adds

| Section | Purpose |
| --- | --- |
| `market_inputs` | Fresh typed returns for indices or external ticker baskets, including price/reference basis and missing-data behavior. |
| `portfolio_overlays` | Named sleeve/group policies that can take precedence over daily per-symbol rules. |
| `bindings` | Select a reusable catalog scenario and supply local metrics, thresholds and target groups. |
| Response sizing | Deterministic proportional math with an explicit quantity basis and caps. |
| Cash and pause state | Persist which proceeds must remain cash and which buys/rebuilds are suspended. |
| Execution resolution | Merge conflicting responses into one target per symbol and reconcile outstanding orders and fills. |

Nested assumptions retain evaluation, questions, assessment and responses. The example's macro-context assumption is informative, not an extra requirement that blocks this mechanically defined strategy whenever news is absent. More news-based assumptions can be added explicitly later.

## Nasdaq falls 2% in a session

The confirmed response is **sell 40% of each holding, wait for a positive day, then buy in 25% steps toward the maximum allocation**. The timing remains a visible draft assumption: Nasdaq Composite is at least 2% below its previous regular-session close, observed immediately during the regular session. It does not mean a rolling 24-hour change, a fall from the intraday high, Nasdaq-100, QQQ or NDAQ. The account must configure a supported index feed or explicitly review a proxy as a different input.

The response trims 40% of actual shares belonging to this sleeve, cancels its pending buys and pauses adds/rebuilds. It does not sell another strategy's positions. One Nasdaq episode cannot repeatedly trim on every observation: at most one macro reduction per exchange session. A later session with a verified −2% return triggers another 40% trim of then-current shares and restarts waiting, including during a rebuild.

A positive day is proposed to mean **Nasdaq return > 0% at the 15:45 ET decision on a later session**. An intraday bounce on the trigger day is insufficient. Once that condition starts recovery, code attempts to add **25% of each eligible symbol's original dollar allocation per session**, including subsequent non-positive days, until reaching **150% of original allocation**. A fresh −2% macro trigger interrupts recovery. These step basis, positive-day reference and subsequent-day behavior are explicit interpretations for review, not additional confirmed answers.

For a symbol originally allocated $10,000 and currently worth $10,000, a 40% trim leaves approximately $6,000 (ignoring fills and price moves). With constant prices and sufficient unreserved cash, recovery targets become $8,500, $11,000, $13,500 and then $15,000. The last increment is capped at $1,500, rather than adding another full $2,500. A different starting holding or market price produces a different number of steps. A per-symbol risk cap or cash shortage can leave recovery pending; the engine cannot borrow or claim the target was reached.

A daily full exit or horizon/account stop takes priority. A symbol closed permanently must not be reopened by macro recovery. A symbol in its own −5% waiting state must also meet its own first-up-day condition; its existing 20%-to-100% rebuild takes priority before a macro top-up toward 150%. If the macro threshold is already crossed before initial funding, block that first buy; after a later positive session, initial funding remains 100% of original allocation rather than silently increasing it to 150%.

Laya receives the matched numeric scenario and explains its context/relevance. It remains the applicability layer for semantic news scenarios, but a user-specified numeric instruction is binding: Laya cannot veto a fresh verified 2% response or delay it during provider failure. Code calculates returns, matches the numeric rule and sizes execution. The 25% recovery is an expressly configured numeric buy policy, not permission for Laya to increase exposure.

## Hardware gains and software trims

The proposed default uses the equal-weight mean of four synchronized member returns at the daily decision time. Individual members' returns are not processed as four separate sells. Missing any required member produces unknown; the engine must not quietly drop that member and change the basket weights.

The reduction fraction is:

`clamp(max(hardware_basket_return, 0) / 5% × 30%, 0%, 100%)`

| Hardware basket day return | Sell from each software holding |
| --- | --- |
| 0% or below | 0% |
| +1% | 6% of current shares |
| +2.5% | 15% of current shares |
| +5% | 30% of current shares |
| +10% | 60% of current shares |
| +20% | 100% (capped) |

For example, member returns of +5%, +3%, +7% and +5% average +5%, causing one 30% trim of each software holding. With 100 shares in each of the five software names, the targets become 70 shares per name, before whole-share and existing-order adjustments. This is not 30% of account equity or 30% of the original allocation. No hardware purchases are implied.

It happens once per session on current shares, so two consecutive +5% basket days leave 49% of the original shares. The example skips this overlay on the initial funding session and can apply it during later rebuilds. A continuous intraday version would need an episode baseline and incremental target accounting; repeatedly selling 30% on every price update is prohibited.

Actual net sell proceeds become reserved strategy cash after fills, not when orders are merely submitted. If a merged sell is larger than the hardware-requested quantity, reserve only the filled proceeds attributable to that requested rotation portion; do not lock unrelated macro/daily-trim proceeds as well. Partial-fill attribution is cumulative and cannot reserve the same cash twice. Software buys are suppressed on the trim session, then may resume under the normal rules and macro recovery using only unreserved cash. Locked rotation proceeds remain cash unless explicitly released by the owner or sleeve retirement. This can prevent rebuilding to the 150% target; the dashboard must show the cash restriction and incomplete recovery. Hardware purchases are never an automatic consequence. Existing account cash cannot be attributed to this reserve twice.

## Which rule wins?

Account safety and broker permissions always precede strategy instructions. Horizon liquidation and symbol full exits precede partial reductions. A macro partial trim and group/daily trim at the **same decision snapshot** merge to the largest required sell fraction, not a sum or two sequential trades. Software rotation overrides that symbol's +5% hold, add or rebuild. Daily first-match semantics remain unchanged within the per-symbol list. A buy and a sell must never be submitted for the same symbol from the same decision snapshot.

A macro trim executed earlier in the session and a hardware trim at 15:45 are different snapshots; the latter uses the shares remaining at 15:45. For example, 100 shares trimmed 40% in the morning leaves 60; a later 30% hardware trim leaves 42. The engine must explain these as separate events, not pretend the whole day has a 40% maximum reduction. Retries for either event must never create an additional trim.

| Situation at the same snapshot | Intended result |
| --- | --- |
| Nasdaq −2%, hardware +5% | Other names trim 40%; software also trims 40% (larger than 30%); pause all buys. |
| Nasdaq −1%, hardware +5%, software name +6% | Trim that software holding 30%; cross-group policy overrides ordinary hold. |
| Hardware +2.5%, software name +4% | Daily 30% trim wins over 15% rotation; submit one 30% reduction. |
| Hardware +10%, software name +4% | Rotation 60% trim wins over daily 30%; submit one 60% reduction. |
| Nasdaq −2%, software name −6% at daily decision | Sell all of that name; other holdings trim 40%; preserve the name's waiting state. |
| Hardware +5%, software name −4% | Sell 30%; suppress its add. |
| Hardware data missing, Nasdaq −2% verified | Still honor the macro trim; unrelated missing data cannot suppress it. |
| Laya unavailable, Nasdaq −2% verified | Still honor the numeric response under the activated execution mode. |
| Recovery active, daily +3% trim applies | Sell according to reduction policy; defer that symbol's rebuild. |

When macro rebuilding and a daily add both request a buy, select the larger eligible increment, not both. Macro rebuilding can override a normal daily hold after recovery starts, but cannot override a full exit, sell/trim, waiting-state prerequisite, locked cash, horizon or account guard. Completion checks use market value relative to the fixed original dollar budget; a price move can change the remaining room. Appreciation above the cap prohibits adds but does not imply an automatic sell unless a sell rule applies.

Boundary behavior remains strict: +5% uses the >+3% trim because hold requires >+5%; +3% does not trim; −5% adds because full exit requires <−5%; −3% adds. Nasdaq exactly −2% triggers the macro response. Nasdaq exactly 0% does not start recovery.

## Implementation and validation required

These fields are proposed typed operations, not arbitrary expressions or code. Catalog bindings must resolve approved metrics, named parameter types, target groups and supported actions. New imports, permissions or provider endpoints cannot be supplied by a strategy. Unsupported inputs/actions must fail activation.

Implementation requires a versioned portfolio validator, durable overlay/cash/pause state, live index and extra-ticker adapters, an execution resolver, risk-aware cash accounting and backtest support using identical return calculations and action priority. Replay requires synchronized intraday stock/index observations at the actual decision time; daily closing bars cannot faithfully simulate an immediate macro reduction or a 15:45 return. Existing daily-bar coverage does not establish adequate data for this proposal.

Test boundaries, repeated events, restart recovery, missing members, stale/out-of-order observations, price-basis changes, early closes, pending buys, sell/fill races, partial fills, approval expiry and ownership. In shadow mode log decisions only. In manual enforcement create approval requests; in owner-enabled paper auto mode submit only the pre-approved numeric policies through the risk/execution path. Model prose cannot create orders. A pending macro reduction blocks buys even if sell approval is outstanding, and a verified urgent reduction retains its intent through a halt or failed sell.
