# 03 — Event Extraction & Signal Engine
**ID:** RI-SIG-03 · **Version:** 1.0 · **Status:** Proposed rule engine · **Depends on:** 02 Sources, 04 Data, 05 Initiatives, 07 Opportunity, 12 Evaluation

## 1. Core separation
`document` (retrieved content) → `atomic claim` (attributed statement) → `event` (dated factual observation) → `signal` (versioned business rule satisfied) → `initiative` (programme evidence) → `opportunity hypothesis` (commercial possibility). Classification must not merge these objects. Buyers' intent is not automatically inferred from technology news.

## 2. Existing baseline
Canonical input: `The Codest Buying Signal Tracker - Signals.csv` (UTF-8-SIG, **64 rows, 11 columns**). Preserve original `ID`, `Group`, `Signal`, `Strength (0-3)`, `Fresh for (days)`, `Window note`, `How to spot it`, `Who to contact`, `Check it is real`, `Pairs best with`, `Stop effect`. Source file copied unmodified into `reference/`. Maintain `legacy_signal_id` as primary code and add `rule_version`. Start production with evaluated high-value subset; keep all others as `defined_not_activated` until detector tests pass.

## 3. Event ontology
`procurement_published`, `procurement_amended`, `procurement_awarded`, `vendor_selected`, `implementation_launched`, `programme_announced`, `programme_completed`, `integration_release`, `product_launch`, `engineering_job_opened`, `contractor_job_opened`, `job_closed`, `technology_leader_appointed`, `acquisition_announced`, `funding_announced`, `supplier_panel_opened`, `supplier_panel_closed`, `regulatory_change`, `deadline_approaching`, `crm_buyer_response`, `relationship_changed`, `negative_cancellation`.

Every event needs `event_id`, `event_type`, `event_date?`, `publication_date`, `first_observed`, `last_checked`, `subject_entity_id`, `other_entity_roles[]`, `source_document_ids[]`, `claims[]`, `verification_status`, `lifecycle_status`, `supersedes_event_id?`, `extraction_model_version`.

## 4. Rule schema
```yaml
id: SRC-01
version: 1.0.0
legacy_group: A buying route is open
name: Live engineering services tender
status: candidate_until_evaluated
required_event_types: [procurement_published, procurement_amended]
source_tiers: [S1]
required_fields: [buyer_entity, scope, authoritative_url, submission_deadline, notice_status]
positive_semantics:
  - relevant software development, engineering, IT consultancy, QA, cloud or AI services
exclusions:
  - expired or awarded procedure unless a new eligible lot opens
  - pure hardware supply without a relevant service scope
verification:
  source: original_official_notice
  current_status: must_be_open
  deadline: timestamp_with_timezone
commercial_questions:
  - Is The Codest eligible, alone or in consortium?
  - Are technical scope, certifications, location and contract terms feasible?
related_play: formal_procurement
buyer_roles: [technical_sponsor, procurement_owner]
negative_rules: [NEG-05, NEG-12]
urgency: deadline_sensitive
```
This is a *rule-design example*: details of actual notice qualification are evaluated per source.

## 5. Demand evidence ladder
| Tier | Defined meaning | Permitted output |
|---|---|---|
| D1 | Verified live purchasing process or directly stated external resource requirement | High-confidence demand event, subject to eligibility/fit |
| D2 | Specific correlated commercial hypotheses, not direct buyer confirmation | Strong hypothesis with clear unknowns |
| D3 | Verified programme likely to create engineering work | Initiative to investigate |
| D4 | Public attention, leadership, funding or context only | Watch/relationship context |

**Independent evidence:** different publication outlets quoting the same release are not separate origins. Maintain `origin_document_id` and `independent_origin_group`. `signals_count` is never a simple number-of-articles tally.

## 6. Rule evaluation process
1. Filter candidate events by type, geography and applicable dates.
2. Verify minimum factual fields from source-backed claims.
3. Run positive patterns / semantic classifier to form candidate match.
4. Evaluate explicit exclusions/negative patterns.
5. Check freshness policy; distinction between publication and event time.
6. Correlate independent evidence around initiative/workstream and time window.
7. Assign `verification_status`, demand tier, signal strength and reason code; never use model confidence alone as evidence confidence.
8. Store rule-version snapshot and cited claim IDs.
9. Emit material update only if category/status/evidence or action priority changed.

## 7. Signal correlation
Correlations are rules over *event entities and shared initiative* rather than company-only joins. Examples: `payments_integration + relevant_contract_role`, `core_migration + prime_award`, `product_launch + implementation_deadline`, `new_leader + specific_public_technology_plan`, `supplier_panel_open + scoped_project`, `previous_contact + new_confirmed_initiative`. Evidence strength should increase only when independent and relevant, not via unrelated group announcements.

## 8. Temporal rules and expiry
Store original CSV freshness as a baseline but map to dynamic policies. Tender signals expire on official close or award, not arbitrary days. Job signals change on vacancy closure. Programme signals remain historical but **current opportunity urgency** decays without fresh milestones. Reopen only on material new events. Uncertain event dates are recorded as unknown and cannot be silently replaced by publication date.

## 9. Negative signal processing
`NEG` rules attach to `account`, `initiative`, `workstream` or `route`. Distinguish `hard_block`, `route_block`, `demand_counterevidence`, `soft_risk`, `ownership_gate`, and `expiring_warning`. For example a closed direct supplier panel blocks direct route but can leave a legitimate partner route; an account ownership restriction blocks unauthorised seller action regardless of score. Do not delete positive evidence when a negative is added; preserve contradictions and state.

## 10. Output contract
```json
{
  "event_id": "evt:...",
  "signal_id": "SRC-01",
  "rule_version": "1.0.0",
  "organization_id": "org:...",
  "initiative_id": null,
  "evidence_claim_ids": ["claim:..."],
  "independent_origin_groups": ["official:..."],
  "demand_tier": "D1",
  "verification_status": "verified",
  "effective_from": "2026-11-20T00:00:00Z",
  "expires_at": null,
  "negative_rule_ids": [],
  "commercial_questions": ["Can The Codest meet tender eligibility?"],
  "summary_is_inference": false
}
```
IDs are illustrative. No fabricated evidence permitted.

## 11. Language and extraction robustness
Support EN/DE/PL/SV/DA/NO/FI source variants through common event ontology. Preserve original text for precise evidence. Technical translations should not merge legal concepts: a permanent engineering job, freelance contract, services framework and labour leasing agreement are different. When translation is ambiguous, route to specialised review.

## 12. Testing and acceptance
Test all 64 CSV rows import without loss; every active rule requires ≥3 positive, ≥3 negative, ≥2 ambiguous and ≥1 expiry fixture. Validate: expired tender rejected, procurement amendment updates original, same press release syndicated 3x yields one origin, contractor role distinguished from permanent vacancy, negative supplier-panel rule changes route not entire company, source outage creates no negative event, subscope/parent legal entity handled, ML classifier JSON schema failure does not write signal. Track per-rule precision/recall and false-positive patterns; release blocked for unreviewed high-impact rules.

## 13. Human adjudication and versioning
Reviewers may mark `correct`, `incorrect_reason`, `needs_evidence`, `wrong_entity`, `stale`, `duplicate`, `not_commercially_relevant`. Store rule ID, version, model/prompt version, reviewer and reason. Changes to active rules require replay tests, changelog, monitored rollout and rollback path.
