# 08 — Buyer Access Graph, Sales Plays & Deal Progression
**ID:** RI-SALES-08 · **Version:** 1.0 · **Status:** Proposed sales operating contract · **Depends on:** 01 Product, 05 Initiatives, 06 Enrichment, 07 Opportunity, 09 Integrations, 11 Security

## 1. Objective
Turn intelligence into credible, human-approved sales progress: identify *who owns the engineering problem*, who can sponsor the work, who approves spend and who controls vendor access. Support direct, partner, reactivation and strategic entry without pretending attention signals prove intent.

## 2. Attention / Intent / Access model
- **Attention:** public talks, engineering articles, events, technical leadership forums. Establish stated interests and relevant conversation topics; never private behaviour or purchase probability.
- **Intent:** active RFP, directly stated need, funded/detailed initiative or explicit buyer confirmation. Grade by source and current status.
- **Access:** previous CRM conversation, existing champion, verifiable introduction, supplier portal, partner ecosystem or initiative owner. A shared event is not a warm relationship.

## 3. Buyer committee data
| Role | Core decision | Evidence standard |
|---|---|---|
| Problem owner | Owns technical pain and practical workstream | Current functional responsibility + initiative tie |
| Programme owner | Delivery milestone and implementation | Explicit programme ownership where available |
| Economic buyer | Spending authority | Direct buyer/organisational evidence; don't infer from job title |
| Technical evaluator | Validates solution/design | Scope-relevant technical role |
| Procurement owner | Sourcing and contracting route | Official supplier process or confirmed role |
| Risk/security | Third-party assurance | Documented vendor-control responsibility |
| Champion/introducer | Helps navigate internal decision | Verified relationship or buyer-confirmed support |
| Prime partner | May purchase subcontracted delivery | Evidence of programme allocation and partner route |

Each person/role edge carries verification status, current role date, specific initiative scope and PII permissions.

## 4. Access route ranking
Rank by verifiable access, not seniority alone: (1) relevant existing buyer relationship in correct account; (2) buyer-confirmed technical owner; (3) official procurement channel for live requirement; (4) verified prime/vendor delivery route; (5) introduction path with known intermediary; (6) new targeted outreach to relevant functional owner; (7) strategic pre-shortlist familiarity. Check contact suppression and CRM owner before recommendation.

## 5. Supplier-entry record
For each account/workstream: `direct_vendor_route`, `MSP/VMS`, `framework/panel`, `prime_subcontract`, `supplier_registration`, `eligibility_conditions`, `security_review`, `contracting_entity`, `known_incumbent`, `submission_deadline`, `approved_categories`, `proof_claim_ids`, `unknowns`, `last_verified`. Mark `portal_exists` separately from `procurement_open`. Country-specific regulated outsourcing and labour leasing requirements need specialist legal review.

## 6. Sales play catalogue
| Play ID | Trigger | Primary buyer | Qualification objective | Required proof |
|---|---|---|---|---|
| SP-01 Specialist capacity | Confirmed shortage/contract requisition | Engineering/Delivery Director | Role, duration, budget route, eligibility | Approved capability/skills evidence |
| SP-02 Embedded team | Sustained scoped delivery needs | VP Eng / PMO | Capacity, operating model, ownership | Approved delivery method and relevant reference |
| SP-03 Defined workstream | Independent technical scope | Product/Programme Owner | Deliverables, acceptance criteria, schedule | Scoped technical proof |
| SP-04 Payment integration | Bank/payment platform integration | Payments Engineering Head | API, compliance, testing/implementation scope | Approved payments integration reference |
| SP-05 Banking modernisation | Core/legacy platform initiative | Transformation Director / SI | Separable workstream + purchase route | Approved banking capability evidence |
| SP-06 Cloud/data | Named migration/data programme | Platform/Data Director | Technical constraint and milestone | Approved infrastructure/data proof |
| SP-07 AI implementation | Specified operational AI use case | Business/AI owner | Business outcome, data, evaluation plan | Approved AI assessment scope |
| SP-08 Partner subcontract | SI/vendor implementation award | Partner Delivery/Alliance | Partner eligibility and specialist gap | Approved service capabilities |
| SP-09 Supplier entry | Panel or onboarding path | Procurement + technical sponsor | Eligibility, timeline and category | Approved supplier risk preflight pack |
| SP-10 Re-engagement | Prior relationship + new material event | Former sponsor / current owner | Whether need or timing changed | CRM history + relevant new verified evidence |

Per play, maintain approved wording, suggested questions, objection handling, disqualifiers and market-specific caveats; never directly expose restricted internal references.

## 7. Sales-stage workflow
**Initial research:** Confirm company, initiative, relevant service and action. **First contact:** Use approved channel and reviewable draft tied to source. **Reply:** Capture actual response and consent/opt-out. **Discovery:** Ask about problem, impact, timeline, engineering team, external supplier model, budget path and buying committee. **Post-discovery:** Distinguish buyer-confirmed from inference, secure mutually agreed next step. **Qualification:** Validate sponsor, procurement access, scope, delivery feasibility. **Proposal:** Internal scoped solution, proof and approval. **Procurement:** Track supplier assurance, legal/security and contracting blockers. **Contract:** Confirm executed terms before labeling won. **Handover:** Pass validated scope and commitments to delivery; track revenue separately.

## 8. Discovery question engine
Generate targeted questions from missing gates. Examples: “Which team owns the integration and target migration milestone?”; “Is the implementation allocated to your current SI or are specialist workstreams separately sourced?”; “Does the framework support an EU engineering supplier or a subcontracted delivery model?”; “Who signs off technical acceptance and supplier eligibility?” Do not ask questions whose answers are already verified in CRM.

## 9. Approved content and proof matching
Maintain a catalogue of case studies and capability claims with `service_tags`, `approved_audience`, `approval_owner`, `source_doc`, `effective_date`, `restrictions`. Match workstream and scope, but require an external-use gate. Internal references to financial clients are not automatically authorised external testimonials. No invented commercial outcome, dates, prices or engineer availability.

## 10. Relationship development
Public buyer commentary can inform a technical conversation when linked to current responsibility. Distinguish known contact, named intermediary, shared organisation/event and cold connection. No fabricated mutual connection or private-interest profiling. Recommend credible long-term touches where no purchasing process exists; preserve `strategic` vs `near_term` routes.

## 11. Deal risk and next action
For active deals, show last buyer-confirmed commitment, missing stakeholder, unresolved purchase route, next meeting, delivery sign-off status, blockers and aging. One recommended step should have owner, purpose, date or trigger, and how success is verified. A sequence of generic “follow up” tasks is not acceptable.

## 12. Approval boundary
Drafting permitted; autonomous sending, sequencer enrolment, CRM material change, supplier registration, price quote, legal statement or client-proof release require explicit authorisation. Respect organisational ownership and non-circumvention. An approved action request is scoped to exact recipient, payload, system and time.

## 13. Sales acceptance rubric
BDM reviews each high-priority lead: relevant initiative? external demand and counterevidence appropriately classified? right purchasing route? correct buyer? useful next action? acceptable proof? time worth investing? Track accept/reject reasons and eventual buyer-confirmed findings. Do not reward superficial email reply rates at the expense of qualified engineering need.

## 14. Example workflows
**Direct tender:** Verify scope, deadline and eligibility → bid/no-bid review → procurement owner + technical sponsor → internal delivery/risk check → approved action. **Prime route:** Verify award scope → identify prime delivery function → qualify subcontracting eligibility → approved capability exchange. **Existing contact:** Match meaningful programme to prior CRM conversation → check permissions/owner → prepare factual re-engagement → capture buyer response. **Negative:** Closed panel but possible SI contract → disallow direct application and research alternate route.
