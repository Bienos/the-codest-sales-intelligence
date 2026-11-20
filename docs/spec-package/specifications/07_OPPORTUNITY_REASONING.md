# 07 — Opportunity Hypotheses, Reasoning & Qualification
**ID:** RI-OPP-07 · **Version:** 1.0 · **Status:** Proposed commercial decision contract · **Depends on:** 03 Signals, 05 Initiatives, 06 Enrichment, 08 Sales, 12 Evaluation

## 1. Commercial truth standard
A public technology initiative is not a confirmed external engineering requirement. Distinguish **observable event**, **engineering workstream**, **commercial hypothesis**, **explicit buying evidence**, **buyer-confirmed problem**, **commercial qualification**, and **CRM opportunity**. Every recommendation must name the boundary between facts and inference.

## 2. Hypothesis structure
```json
{
  "hypothesis_id": "hyp:...",
  "initiative_id": "init:...",
  "workstream_id": "ws:...",
  "potential_counterparty": "org:...",
  "route_type": "direct|vendor|prime_subcontract|relationship|procurement",
  "possible_codest_service": "payments_api_integration",
  "supporting_claim_ids": [],
  "counterevidence_claim_ids": [],
  "external_demand_status": "unknown|inferred|explicit|buyer_confirmed",
  "access_status": "unknown|potential|verified|restricted|blocked",
  "buyer_status": "role_identified|person_verified|buyer_confirmed",
  "critical_unknowns": ["Is relevant integration scope externally available?"],
  "qualification_state": "investigating",
  "recommended_action_id": null,
  "scoring_rule_version": "1.0.0"
}
```
This schema is illustrative; no fabricated values in real records.

## 3. Commercial pathways
For a programme, evaluate buyer-direct, vendor-side, SI subcontracting, existing-client cross-sell and relationship reactivation as **separate routes**, each with independently verified accessibility and owner. Avoid triple-counting a single initiative as three real opportunities. A route may be blocked while another remains open.

## 4. Qualification gates
| Gate | Core question | Evidence needed | Failure action |
|---|---|---|---|
| G1 Account | Is correct legal entity and ICP fit established? | Identity, sector and source-backed size/exception | Review identity or disqualify |
| G2 Initiative | Is work actually happening? | Named event / corroborated programme and stage | Monitor |
| G3 Engineering | Is work relevant to Codest? | Specific technical workstream and capability fit | Stop if outside capability |
| G4 Demand | Is external help plausible or explicitly sought? | Procurement, buyer statement or credible independent signals | Research, don't label confirmed |
| G5 Supplier access | Can Codest participate via direct or partner route? | Tender eligibility, qualification rules or buyer/partner confirmation | Route-block or investigate |
| G6 Buyer path | Who can advance a relevant conversation? | Role-responsibility evidence, CRM/relationship context | Role research |
| G7 Execution | Can scoped work be delivered commercially? | Internal approval of capability, terms/timing as required | Internal feasibility gate |

A recommendation may be actionable with unknown budget, but it must not be commercially qualified while critical buying/feasibility gates are unresolved.

## 5. State machine
`observed → verified → relevant → investigating → actionable_hypothesis → engaged → buyer_need_confirmed → commercially_qualified → proposal_procurement → contracted_delivery`. Optional `monitor`, `blocked`, `disqualified`, `expired`, `superseded`. Transitions are recorded with actor, date, supporting claim/CRM event and requirement checklist. Direct promotions across states forbidden except controlled migration/reconciliation. Reopen from blocked/expired only on new material evidence or approved review.

## 6. Scoring dimensions
| Dimension | Proposed weight | Evidence rule |
|---|---:|---|
| ICP/strategic fit | 15 | Verified employee market/sector + approved exceptions |
| Engineering relevance | 25 | Workstream-to-Codest capability relevance |
| External demand evidence | 20 | Explicit > corroborated > purely hypothetical |
| Buyer and supplier access | 20 | Verified route > plausible route > unknown |
| Timing/urgency | 10 | Verified deadline/milestone and response window |
| Relationship and delivery fit | 10 | Verified access and internally validated service match |
| **Total** | **100** | Policy versioned; no probability-of-close interpretation |

**Parallel non-score measures:** `evidence_confidence`, `research_completeness`, `negative_signal_severity`, `last_checked`, `commercial_qualification_state`. Scoring weights remain provisional until outcome studies. Use explicit rules; LLM may provide a reasoned assessment but cannot write an opaque number as truth.

## 7. Hard and scoped blockers
`opt_out`, `ownership_conflict`, `explicit_no_supplier`, `ineligible_tender`, `cancelled_workstream`, `expired_procurement`, `unapproved_claim`, `unavailable_delivery_model`, `confidentiality_restriction`. Hard restrictions prevent suggested prohibited actions. Scoped blockers affect appropriate route and time window; prime involvement alone is not always a blocker.

## 8. Contradiction and adversarial review
For `priority >= high` call an independent bounded challenge review using evidence-only inputs. Questions: correct entity? actual dates? is programme complete? incumbent coverage? independent corroboration? direct vs partner purchasing? buyer actually responsible? budget inferred? client proof permitted? what evidence would invalidate? Output `supports[]`, `contradicts[]`, `unknowns[]`, `proposed_route_change`, `confidence_impact` and citations. Reviewer can downgrade/reopen research, never silently elevate an unsupported claim.

## 9. Next-best-action policy
Prioritise one action that reduces a real commercial bottleneck, e.g., confirm eligibility, identify delivery owner, investigate prime subcontracting or ask a buyer about external capacity. Each action: `owner_id`, `target_role`, `action_type`, `objective`, `reason`, `evidence_claim_ids`, `due_at/trigger_condition`, `approval_required`, `expected_outcome`, `stop_condition`. Avoid generic 'follow up' without purpose. Before paid research, compare expected decision value to cost; before contact drafting, check ownership, suppression and approved proof.

## 10. Priority queue allocation
Existing master-list accounts get guaranteed monitoring and first position in default digest. New companies with *verified relevant urgent demand* may outrank weak existing signals. Separate `coverage_priority` (which sources/accounts are monitored) from `action_priority` (what a rep does next). Avoid inflating opportunity score solely for existing-list membership.

## 11. Example adversarial determination
Input: bank announces multiyear migration; engineering vacancies increase; SI wins the migration contract. **Facts:** programme and SI scope exist; vacancies exist. **Hypotheses:** integration specialism could be required. **Counterevidence:** SI may own delivery; bank may insource. **Action:** investigate SI partner route before direct-bank pitch. **State:** researching; not a qualified external need. This is a fictional test example.

## 12. Feedback and calibration
Capture BDM accepted/rejected/reason, buyer confirmed or denied demand, supplier route result, meetings held, procurement progress, qualified opportunity, signed contract and attributable revenue where available. Evaluate source/signal/route contribution with cohorts and enough samples. A reply is not proof that the original inference was correct. Do not infer calibrated close probabilities until data supports them.

## 13. Acceptance suite
- A bank announcing AI investment without external buying route never moves to commercially qualified.
- Expired relevant tender is urgent only for historical analysis, not live action.
- Active tender with impossible jurisdiction requirement is blocked/ineligible.
- SI award changes bank-direct access but may open partner route.
- A prior contact in Pipedrive does not override opt-out or owner restrictions.
- High-priority lead has cited claim, contrary facts, meaningful unknowns, recommendation and route.
- Model score cannot override hard procurement/legal/ownership blockers.
- All state changes are auditable and reversible via explicit transition.
