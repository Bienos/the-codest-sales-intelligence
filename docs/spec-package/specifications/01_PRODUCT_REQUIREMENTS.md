# 01 — Product Requirements Specification (PRS)
**ID:** RI-PRS-01 · **Version:** 1.0 · **Status:** Proposed production contract · **Depends on:** Master V4, 04 Data, 07 Opportunity, 08 Sales, 10 UX, 11 Governance

## 1. Product mission
Deliver a trusted daily sales operating system for The Codest that produces commercially meaningful, evidence-backed engineering opportunities from two canonical Google Sheets, monitored public intelligence and approved CRM interactions. Product success = more **buyer-confirmed, commercially feasible** opportunities per seller hour, not the number of detected news articles.

## 2. Users and permissions
| Persona | Core objective | Read scope | Allowed actions |
|---|---|---|---|
| BDM/account owner | Prioritise and progress assigned accounts | Own book + permitted shared records | Review, dismiss, research, draft, request approved CRM sync |
| Sales manager | Balance territories and review quality | Team book | Reprioritise in accordance with ownership policy; approve actions |
| Research/operations analyst | Maintain evidence and company matching | Relevant research data | Validate claims, merge with review, curate sources |
| Marketing | Provide approved proof and campaign assets | Approved external materials | Maintain collateral and reviewed campaigns |
| Delivery/recruitment expert | Validate delivery feasibility | Relevant opportunity scope | Confirm capability and potential capacity subject to policy |
| System administrator | Operate connectors and policies | Infrastructure/control plane | Source configuration, credentials rotation, role grants, audit |
| Compliance/privacy | Review data and outbound controls | Restricted audit scope | Review lawful use, retention, suppression and policies |

## 3. Outcomes and non-goals
**Must:** signal-first discovery; comprehensive existing-account watch; procurement/vendor monitoring; source verification; account entity resolution; initiative/workstream correlation; qualified commercial hypotheses; source/negative evidence; bounded research; buyer/access route; Pipedrive context; seller review and action tracking; operational quality. **Not:** guarantee all internet activity detected, assert buying intent from search activity, autonomous external outreach, unfounded win probability, automatic private-community scraping, or unreviewed supplier applications.

## 4. Critical user journeys
### UJ-01: Morning priorities
1. Seller opens Today; sees material new/changed recommendations, not all events.
2. Each item shows account, existing/new tag, actual event, initiative, evidence confidence, accessibility, unknowns, recommended action, accountable owner and trigger/due date.
3. Seller accepts, dismisses with reason, requests specific further research or opens account.
4. Decision and outcome persist; no duplicate alert on unchanged intelligence.
**Acceptance:** seller can determine an action without reading long research; empty digest valid; ownership enforced.

### UJ-02: Existing bank initiative
1. Official source or monitoring reports an event.
2. Company is matched to `MASTER ACCOUNTS` and CRM, subsidiary scope preserved.
3. Existing programme is updated rather than duplicated.
4. Commercial planner investigates engineering work and sourcing route; displays counterevidence.
5. System recommends bank-side, vendor-side, SI-side or monitor strategy with source links.
**Acceptance:** no third-party statement upgraded to bank-specific demand without evidence.

### UJ-03: Open tender
1. Official procurement source yields relevant notice ID with open period and required services.
2. Validate live status, authoritative deadline, eligibility, geography and buyer entity.
3. Alert owner with bid/no-bid questions; monitor amendments and deadline changes.
**Acceptance:** expired award notices not treated as open RFPs; changes produce amendment alerts, not duplicate leads.

### UJ-04: Existing buyer reactivation
1. Company initiative is matched to CRM account with previous discussions.
2. Agent retrieves permitted history and checks suppression/owner.
3. Re-engagement recommendation references genuine new information and previous relation.
**Acceptance:** no suggestion to contact suppressed person; no inferred warm intro without validated relationship.

### UJ-05: Deal continuation
1. Buyer interaction recorded in authorised CRM/notes.
2. Platform separates buyer-confirmed facts from interpretation.
3. Tracks champion, scope, procurement gate, funding path, delivery confirmation and next commitment.
4. Suggests activities and CRM changes requiring approval.
**Acceptance:** meeting held alone never changes commercial qualification state.

## 5. Functional requirements
| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-001 | P0 | Read two verified workbooks by metadata and source-row lineage | Import audit and idempotent replay |
| FR-002 | P0 | Ingest original 64-signal CSV with immutable original IDs | Import diff/reconciliation test |
| FR-003 | P0 | Source-specific polling and freshness/coverage status | Mock failure/expiry test |
| FR-004 | P0 | Record atomic events and source claims distinctly | Schema and fact fixture tests |
| FR-005 | P0 | Deduplicate event across mirrored articles | Replayed article fixture |
| FR-006 | P0 | Match canonical organisations with parent/subsidiary caveats | Labelled matching corpus |
| FR-007 | P0 | Associate events with initiatives and workstreams | Graph scenario acceptance |
| FR-008 | P0 | Preserve demand evidence vs access vs confidence separately | Negative/counterexample fixtures |
| FR-009 | P0 | Support research queue with cost and stop rules | Budget/reason test |
| FR-010 | P0 | Provide actionable recommendation with one next step | Seller review rubric |
| FR-011 | P0 | Prevent external/CRM write without explicit authorisation | Authorisation integration tests |
| FR-012 | P0 | Daily digest suppresses repeated unchanged information | Digest replay test |
| FR-013 | P0 | Audit historical model, rule and evidence versions | Reproduce previous recommendation |
| FR-014 | P1 | Buyer committee and supplier route research | Human validation rubric |
| FR-015 | P1 | Integrate Pipedrive account/deal history with owner restrictions | Sandbox + permissions test |
| FR-016 | P1 | Track feedback and conversion events | Trace to CRM outcome IDs |
| FR-017 | P1 | Source admin and failure dashboards | Source outage drill |
| FR-018 | P1 | Multi-language source extraction | EN/DE/PL/Nordic fixture matrix |
| FR-019 | P2 | Advanced partner graph and introductions | Evidence-backed relationship tests |
| FR-020 | P2 | Sales collateral proof matching from authorised docs | Permission leakage tests |

## 6. Nonfunctional requirements
- **Trust:** auditable source links for every sales-relevant factual assertion; no made-up sources, contacts, revenue or technology stacks.
- **Correctness:** explicit temporal state and entity scope; unverified data marked as such.
- **Isolation:** roles, accounts, CRM writes and external-provider permissions enforced server-side.
- **Operability:** durable jobs, retries, dead-letter queue, backup restore and source health dashboard.
- **Cost:** hard per-provider/per-research-task budget, explicit approval before credit-consuming contact enrichment.
- **Usability:** summary-first, human-readable unknowns, small daily prioritized queue, accessible evidence drilldown.
- **Performance:** operational targets in 13 and 14 are proposed and must be load-tested, not promised.

## 7. Product acceptance gates
1. Gold-set replay of ambiguous/negative events demonstrates stable recommendations.
2. Existing-account source-row lineage available for every matched account; no accidental merging of subsidiaries.
3. A valid open tender with a corrected deadline changes the original record and triggers a single amendment alert.
4. A source outage produces coverage degradation, not an invented 'zero events' result.
5. Human review can see original source, research reasoning summary, disqualifiers, buyer route and precise next action.
6. No unauthorized outbound, contact-credit purchase or material Pipedrive write in any test.
7. Pilot sellers evaluate actual recommendation usefulness; monitor outcome funnel rather than volume alone.

## 8. Product instrumentation
Capture `recommendation_presented`, `source_opened`, `research_requested`, `accepted`, `dismissed(reason)`, `outreach_draft_requested`, `approved_action`, `buyer_confirmed_need`, `procurement_validated`, `qualified_crm_opportunity`, `signed`, `revenue_observed`. Every event references account/initiative/recommendation/rule version without exposing unnecessary contact PII.

## 9. Source boundaries and open decisions
Baseline ICP and activity rules derive from The Codest guidebook; imported company data and 64-signal tracker require continued access and schema verification. To approve before production: ownership permissions, Pipedrive field schema, data retention, preferred cloud/security environment, paid API allowances, delivery-case permissions, and what constitutes a qualified opportunity.
