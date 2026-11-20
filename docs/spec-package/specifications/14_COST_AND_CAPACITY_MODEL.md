# 14 — Research Cost, Capacity & Efficiency Model
**ID:** RI-COST-14 · **Version:** 1.0 · **Status:** Parameterised planning model · **Depends on:** 02 Sources, 06 Enrichment, 09 Integrations, 13 Operations, 15 Implementation

## 1. Cost objective
Optimise **cost per BDM-accepted, evidence-backed commercial recommendation**, not cost per raw search or number of enriched contacts. Source reuse, event deduplication, selective deep research and early disqualification should dominate cost strategy. Provider rates are variable and must be pulled from current contracts; this document intentionally does **not invent prices**.

## 2. Cost categories
| Cost class | Unit driver | Budget gate |
|---|---|---|
| Structured official API collection | Requests/pages and infrastructure | Per connector quota/limits |
| Web discovery | Search requests, retrieval units/results | Daily/weekly query plan quota |
| Monitoring/change detection | URLs × effective poll count | Domain/page priority |
| LLM extraction | Changed docs × tokens per doc | Cheap model where validated |
| Verification | Candidate events × source fetches / calls | Escalate if event relevant |
| Deep multi-source research | Promising initiatives × complexity | Per-hypothesis budget |
| Company firmographics | Unresolved identities × vendor credits | Provider budget |
| Personal contact enrichment | Approved target contacts × credits | **Prior explicit approval** |
| Notifications | Messages/digests and delivery | Throttling and limit |
| Database/storage | Records, permitted blobs, backups | Retention and compression |
| Compute/observability | Worker time, queues, log volume | Scaling policy |
| Human review | Minutes per accepted/rejected item | Sales productivity KPI |

## 3. Parametric budget equations
Definitions: `A` monitored accounts; `P` average designated monitored pages/account; `f` checks/page/day; `q` discovery queries/day; `r` average result pages/query; `u` fraction content changed; `e` relevant event fraction after extraction; `v` fraction promoted to verification; `h` fraction of verified events requiring deep research; `c_*` provider-specific measured unit cost.

- `page_checks_month = A * P * f * days` (actual effective checks adjusted for source policies).
- `discovery_queries_month = q * days`.
- `changed_documents_month = page_checks_month * u + discovery_queries_month * r * effective_unique_fraction`.
- `verification_candidates = changed_documents_month * e * v`.
- `deep_research_tasks = verified_event_count * h`.
- `total_monthly_cost = sum(each_volume_i * measured_unit_cost_i) + fixed_cloud + storage + monitoring`.
- `cost_per_accepted_recommendation = (research + enrichment + operational costs) / BDM_accepted_recommendations`.
- `seller_time_saved = baseline_minutes_on_comparable_tasks - actual_minutes_with_engine`, measured, not assumed.

Use ranges and uncertainty for `u`, `e`, `h`, source coverage and actual vendor billing.

## 4. Capacity scenarios (illustrative volumes, no made-up prices)
| Scenario | Monitored accounts A | Prioritised deep research | What to validate |
|---|---:|---|---|
| Controlled production | 40 active/reserve + broader low-frequency master list | Only meaningful verified changes | Cost, relevance, reliability |
| Expanded financial-sector coverage | All eligible imported financial accounts with source profiles | Rule-gated priority accounts | API rate limits, per-source yield |
| Multi-market coverage | Financial + selected enterprise secondary sectors | Opportunity-value gating and strict budget | Provider scales, review capacity |

Account counts beyond 40 depend on live list deduplication, configured sources and tested budget. Do not assume all imported rows become daily monitored webpages.

## 5. Query budget allocation
Define hard caps: `daily_search_budget`, `monthly_provider_budget`, `per_hypothesis_deep_research_budget`, `max_ai_tokens_per_event`, `contact_enrichment_budget`, `max_retry_cost`. Per-account research priority can vary by recent material signals, account ownership, verified active programme and urgent procurement. Existing master accounts get coverage guarantee; urgent new tenders can override action rank, not unlimited spending.

## 6. Adaptive decision-to-research rule
Before a paid research step, record `question`, `evidence_gap`, `probability_of_resolution` (if supported, as heuristic only), `decision_effect`, `estimated_cost`, `approval_required` and `stop_reason`. Do not research merely to increase the length of a report. If answer must come from buyer conversation, create qualification action instead of repeated web research. Maximum research depth policy may differ by account priority.

## 7. Provider fallback and cache economics
Use official APIs for structured data, targeted monitoring for known pages and semantic discovery for unknown events. Cache immutable original sources and metadata (subject to licence), source snapshots, entity resolution and prior conclusions. Invalidate on event change, amended tender, contact role change or source expiry. Separate provider outage fallback from duplicate simultaneous paid provider calls.

## 8. Budget control mechanics
- Reserve provider-specific monthly spend and credentials per environment.
- Increment budgets before provider call; reconcile actual usage after response.
- On hard-cap breach reject/cancel task before call; return `budget_exceeded` to planner and UI.
- Contact-credit spend and personal-contact data access require **prior explicit user approval**, in addition to account/month budget.
- Retry policy uses budget and provider billing semantics; avoid double-credit spending after timeout.
- Daily report shows query volume, deep research, costs, credits and accepted recommendations.

## 9. ROI and effectiveness measurement
Track BDM hours spent, recommendations acted upon, buyer-confirmed needs, qualified opportunities, contract progress and recognised revenue where recorded. Attribute opportunities to meaningful detection/research contributions using transparent multi-touch records rather than claiming causal ROI. Use matched account cohorts and acknowledge low sample sizes / long sales cycle. Avoid optimising exclusively for raw meeting count.

## 10. Sensitivity analysis to complete before purchasing
Obtain price lists for Exa, page monitoring, LLM tokens, Apollo credits, cloud, database and notification. Evaluate combinations of `A, P, f, q, e, v, h`, 429 rates, duplication and human-review constraints under low/base/high scenarios. Output vendor quotes with date and entitlement. Recommend architecture only after feasibility against approved budget.

## 11. Acceptance and alerts
Enforced cap tested by simulated multi-provider retries; no personal contact credits without approval; unchanged pages not reprocessed; duplicate syndicated event doesn't trigger repeated deep research; usage traceable to a source/query/initiative/action; budget dashboard ties measured costs to accepted leads and incidents; over-budget tasks fail safely and remain resumable.
