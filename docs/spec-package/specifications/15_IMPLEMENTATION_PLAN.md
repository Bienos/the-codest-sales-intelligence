# 15 — Implementation Plan, Dependencies & Release Gates
**ID:** RI-PLAN-15 · **Version:** 1.0 · **Status:** Proposed delivery contract · **Depends on:** Specifications 01–14

## 1. Delivery philosophy
Build the **whole production architecture**, release in complete and testable increments. Do not spend months assembling disconnected agents or present a prototype as production ready. Every release includes actual data migration, tests, operational telemetry, security controls, documentation and a rollback plan. Existing business systems remain authoritative where specified.

## 2. Prerequisites and approvals
- Approve accountable product owner, technical owner, CRM administrator, security/privacy reviewer and sales validation reviewers.
- Grant least-privilege access to source Google Sheets, approved Pipedrive instance and provider/API accounts.
- Verify CSV 64-rule baseline, workbook live schemas, source licences, case-study approval policy, employee/territory rules and legal compliance.
- Select cloud/data location, CI/CD, secrets, SSO and provider budgets.
- Agree definitions of `BDM-accepted`, `buyer-confirmed need`, `commercially qualified`, `signed` and `revenue observed`.
- Prioritise actual source contracts; don't assume every candidate provider has a suitable API.

## 3. Work packages and dependencies
| WP | Deliverable | Predecessor | Completion condition |
|---|---|---|---|
| WP-00 | Gold evaluation corpus and decision rubric | Business input | Labelled positive/negative/ambiguous cases and sign-off |
| WP-01 | Repository, CI/CD, auth, secrets, migrations, logs | 11,13 | Secure baseline deploy and recovery drill |
| WP-02 | Canonical account and Google Sheets import | WP-01 | Source-row lineage and identity review, no unapproved writes |
| WP-03 | Source registry, official API collectors, monitoring | WP-01 | Certified connector health and replay tests |
| WP-04 | Atomic events, claims, 64-rule catalog, active subset | WP-02/03 | Evidence-supported detection and dedup accuracy |
| WP-05 | Initiative/workstream/supplier graph | WP-04 | Correct clustering and scoped vendor relation tests |
| WP-06 | Adaptive research and staged enrichment | WP-05 | Cost capped, sources cited, no unauthorised PII |
| WP-07 | Commercial qualification, counterevidence, action | WP-06 | BDM-reviewed useful recommendations and state gates |
| WP-08 | Seller Today, Account and Explorer | WP-07 | Usability/permissions acceptance |
| WP-09 | Pipedrive read-sync, history, approved actions | WP-02, WP-08 | Ownership/approval/replay tests |
| WP-10 | Buyer graph, sales plays, deal support | WP-06/09 | Approved relationship and qualification workflows |
| WP-11 | Operational hardening, cost, quality dashboards | All | SLO evaluation, incident exercises, security sign-off |

## 4. Production release gates
### R0 — Foundation
WP-00/01/02: data contracts, importer, canonical accounts, roles and security. Gate: sample imports match expected rows/fields; zero unauthorized writes; ambiguous parent/subsidiary queued for review; full backup/restore tested.

### R1 — Evidence radar
WP-03/04: selected official procurement feeds, company monitoring and original signal catalogue; positive, negative, stale and duplicate tests pass. Gate: high-priority source failures visible, procurement deadlines correct in reviewed sample, clear source trace.

### R2 — Initiative graph
WP-05: technology programmes, workstreams, vendors and time validity. Gate: sample cases distinguish buyer bank, vendor and prime and do not invent supplier demand.

### R3 — Opportunity intelligence
WP-06/07: adaptive research, waterfall enrichment, route analysis, challenge and scoring. Gate: meaningful BDM recommendations with counterevidence and cost bounded. All unknowns represented honestly.

### R4 — Seller workspaces and CRM
WP-08/09: Today, Account, Explorer, approved Pipedrive read/sync proposals. Gate: end-to-end seller workflow, idempotent CRM write proposals, role access, usability test and notification dedup.

### R5 — Deal development and general release
WP-10/11: supplier/buyer access, approved proof, discovery/deal progression, monitoring, reliability, security and on-call. Gate: production governance sign-offs, fault injection, ongoing commercial measurement. No claim of revenue uplift without observation.

## 5. Implementation contracts to produce in code
- Typed event/claim/initiative/signal/route/action JSON schemas.
- Database migrations with versioning and unique/idempotency constraints.
- Connector interfaces and provider qualification checklist.
- Rule catalogue derived from 64-source CSV, plus separate evaluated rule variants.
- Source health and coverage records.
- AI worker I/O schemas and provider/prompts configuration.
- Cross-system account/CRM identity and approval executor.
- Human reviewer decisions, evidence review UI and audit trail.
- End-to-end replay test suite, monitoring dashboards and operational runbooks.

## 6. Parallelism and critical path
Source connector qualification, master-list reconciliation, interface UX research and gold-set labelling can run partly in parallel. Critical path: **verified data identity → event/claim evidence → initiative correlation → commercial decision → seller action → measured outcome**. Do not build buyer-contact automation before ownership/suppression and approval controls work. Do not ship high-priority recommendation generation before the adversarial test suite and source health exist.

## 7. Acceptance evidence per work package
Each team produces: spec link/version, implementation commit, test output, security/permissions findings, known limitations, sample input/output with original evidence, observed costs, rollback steps, named approver and go/no-go decision. Track unresolved blockers explicitly; no blanket 'complete' label for partly configured integrations.

## 8. Pilot and production rollout strategy
Run new system in **shadow mode** first (no outbound or consequential writes): compare known source events against detector output and existing lead research. Then enable small owned-account review cohort with full source transparency. Expand sources and accounts only when accuracy, operational stability and BDM utility gates are met. Test with real records where authorised; anonymise demonstration cases; never fabricate actual lead results to satisfy a quota.

## 9. Commercial experiment design
Baseline manual research effort and outcomes for a comparable account cohort; compare with platform-supported cohort for accepted recommendations, useful conversations, buyer-confirmed needs, supplier-route discoveries and qualification progression. Track unequal account quality and the long enterprise sales cycle. Review strategic versus near-term separately. Treat revenue attribution as indicative rather than causal unless study design supports stronger claim.

## 10. Release checklist (blocking)
- [ ] Canonical imports preserve both workbook and CRM lineage.
- [ ] 64 original tracker rules imported unchanged; enabled subset evaluated.
- [ ] Source licences and API entitlements checked.
- [ ] Official tender status/expiry correct in reviewed tests.
- [ ] Event/article dedup, subsidiary identity and temporal checks pass.
- [ ] Research costs and personal-contact approvals enforced.
- [ ] High-priority recommendations show counterevidence, unknowns, buyer route and next action.
- [ ] Source outages shown as degraded coverage.
- [ ] No unapproved messaging, CRM material writes or external supplier applications.
- [ ] Restricted client references withheld from external drafts.
- [ ] Logging, backups, retry, DLQ, rollback, incident response and admin monitoring validated.
- [ ] BDM task-based usability and relevance review completed.
- [ ] Security/privacy sign-off recorded and production owner assigned.

## 11. Operating ownership and handover
Assign named owners for source connectors, data quality, AI/rule quality, sales product, CRM integration, infrastructure/security and privacy. Document who changes signal rules, who approves contact enrichment and outreach, who reviews false account merges and who responds to source/tender incidents. Include aftercare, maintenance and provider deprecation plans.

## 12. Final definition of done
An authorised seller can open Today, identify a priority *real* existing account or new eligible lead, inspect accurate original sources, understand programme/workstream/buyers/supplier access and counterevidence, choose one relevant sales action, safely approve CRM activity, and later see outcome feedback. The system continues through provider outages with explicit coverage warnings and recoverable history; quality and costs are measured. All claims of actual conversion improvement require observed post-launch results.
