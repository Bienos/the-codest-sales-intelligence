# 05 — Technology Initiative & Workstream Intelligence
**ID:** RI-INIT-05 · **Version:** 1.0 · **Status:** Proposed production contract · **Depends on:** 03 Signals, 04 Data, 06 Enrichment, 07 Opportunity

## 1. Purpose
Reconstruct named and evidence-backed technical programmes from fragmented events. A technology initiative is the *work happening*, not a company-level intent score. One bank can operate dozens of simultaneous initiatives with different budgets, legal entities, implementers, procurement processes and delivery phases.

## 2. Key objects and relationships
`organization` owns/sponsors `initiative`; `initiative` includes `workstream`; `event` provides evidence for `initiative`; `supplier` is contracted/mentioned for a **scoped role**; `buyer_role` owns/influences `workstream`; `procurement_route` determines a permitted purchase path; `hypothesis` proposes where Codest might add value. These relationships have dates, source claim IDs, and verification state.

## 3. Initiative lifecycle
`candidate` → `announced` → `planning` → `sourcing` → `implementation` → `transition_or_launch` → `completed`; alternatives `paused`, `cancelled`, `scope_unknown`. Avoid forcing all initiatives through all phases; actual state reflects cited evidence and may be partial or contradictory. Keep `stage_verified_at`, last relevant update and next expected milestone.

## 4. Event-to-initiative association
### Candidate generation
- Entity scope: exact bank legal entity and/or group, named programme, vendor, product, technological system, programme owner.
- Text: initiative name/aliases, technology, locations, timeline, known workstreams.
- Temporal proximity: event date in initiative window, with exceptions for multiyear programme developments.
- Relationship context: same named platform vendor or integrator; relevant subsidiary.
### Link decision
- `confirmed_link` when source explicitly names programme or an unambiguous official cross-reference.
- `probable_link` when independent evidence ties scope and milestones; requires additional checks for important conclusions.
- `candidate_link` when semantic similarity is insufficient for automatic link.
- `rejected_link` when contradictory company, date or scope.
Do **not** combine unrelated AI initiatives simply because they occur at the same bank.

## 5. Initiative schema
```json
{
  "initiative_id": "init:example",
  "title": "Core banking transformation (official name where known)",
  "title_is_official": false,
  "owner_organization_id": "org:example",
  "other_organizations": [{"organization_id":"org:vendor","role":"technology_provider","claim_ids":[]}],
  "business_objective": {"text":"...", "claim_ids":[]},
  "technical_objective": {"text":"...", "claim_ids":[]},
  "stage": "implementation",
  "stage_evidence_claim_ids": [],
  "milestones": [{"description":"...", "date":null, "claim_ids":[]}],
  "technology_tags": [],
  "workstream_ids": [],
  "supplier_edges": [],
  "last_material_event_at": null,
  "research_gaps": ["Is integration work allocated to existing partners?"],
  "review_status": "research_needed"
}
```
Example shape only; do not store unsubstantiated text as fact.

## 6. Workstream ontology
Examples: backend/API integration, data migration, core banking customisation, payment rails, event-driven architecture, cloud landing zone, legacy decommissioning, customer onboarding, test automation, security validation, platform reliability, AI implementation, data platform and analytics. A workstream has `scope_statement`, `scope_confidence`, `delivery_stage`, `needed_capabilities`, `owning_organization`, `known_suppliers`, `resource_evidence`, `milestones`, `blocking_constraints`.

## 7. Supplier and counterparty graph
The same development may imply three distinct routes:
1. **Buyer-direct** — bank has separable workstream and eligible supplier arrangement.
2. **Vendor implementation** — software vendor may own rollout and need implementation experts.
3. **Prime subcontracting** — SI owns contract and may engage specialist partners.
Separate the counterparties and route hypotheses; do not create three independent 'confirmed opportunities'. Verify if supplier selection is exclusive, partial, announced, completed or merely proposed. Supplier announcements may be promotional and may not establish service scope.

## 8. Timeline reasoning
Store exact event dates, programme milestones, tender deadlines and known workstream windows. Derive `time_to_milestone` only from verified dates, mark timezone. A published programme deadline alone does not prove an active procurement window. Retire 'upcoming' labels when milestones pass unless new official progress evidence exists. Track event-amendment chains.

## 9. Initiative refresh logic
- New named event → direct graph update, no full re-research if sufficient.
- New related vacancy cluster → link to initiative as capacity evidence with caveat.
- New vendor award → update supplier graph; reconsider direct access.
- Stage change / contract award → recalculate urgency and invalidate superseded hypotheses.
- No material change → preserve history, do not repeat alert.
- Stale initiative → schedule status verification at a cadence appropriate to importance.

## 10. Engineering opportunity extraction
Generate possible workstreams only from source-backed objectives; distinguish `(a) confirmed scope`, `(b) directly implied engineering activity`, `(c) inferred possible external opportunity`. Each workstream is mapped to The Codest capability catalogue and validated by 08/approved proof. Avoid inventing a product stack, delivery date, contract size or available team.

## 11. Core investigative questions
Who owns programme delivery? What is already contracted? Which exact tasks might remain? Is budget confirmed? Are there regulatory or launch milestones? Who controls supplier selection? Does the vendor/prime have a separate eligible capacity route? Are restrictions direct-only or group-wide? What evidence would make this lead irrelevant?

## 12. Reconciliation and human review
Create `possible_duplicate_initiative` review when same title but conflicting owners or subsidiaries; show side-by-side source/evidence. On merge, preserve both event sets, aliases and full audit. On split, retain links and re-evaluate affected opportunities. User override must cite a reason and may be time-bound.

## 13. Acceptance scenarios
**A:** Bank selects core-banking vendor and SI: one initiative, multiple named supplier roles, three separately qualified commercial paths. **B:** Two unrelated cloud programmes: two initiatives even at same parent company. **C:** Vendor reports completion: phase updates and old active hypotheses reevaluated. **D:** Parent announcement, subsidiary procurement: no automatically shared supplier eligibility. **E:** Four syndicated articles: one event origin. **F:** New hiring cluster: staffing demand remains explicitly unconfirmed. **G:** No source for claimed budget: `budget_verified=false`.

## 14. Metrics
Programme clustering precision/recall on labelled data; duplicate initiative rate; correct supplier role mapping; stage accuracy; percentage of actionable hypotheses with evidence-linked workstream; ratio of stale initiatives; BDM correction rate. Require reviewer samples by industry and country.
