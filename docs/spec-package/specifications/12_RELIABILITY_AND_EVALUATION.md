# 12 — Quality, Reliability, Benchmarking & Evaluation
**ID:** RI-QA-12 · **Version:** 1.0 · **Status:** Production quality gate design · **Depends on:** 02–11, 13 Operations, 15 Implementation

## 1. Quality philosophy
A fluent, sourced-sounding answer can still be wrong. Evaluate the **entire decision chain**: source collection → factual event → identity → signal → initiative → workstream → commercial access → next action. Each step needs independent labelled tests and run-time checks. The platform must know when coverage is incomplete and avoid treating an unsuccessful source check as no relevant event.

## 2. Gold benchmark construction
Build a labelled dataset with explicit source files and date stamps from (a) original 64-rule CSV, (b) two Google Sheets' existing historical signals, (c) approved prior research, (d) sourced real/public procurement and company cases, and (e) synthetic negative/stress fixtures labelled **synthetic**. Each item includes original document/notice ID, expected entity, event type, date, rule, source authority, initiative link, negative conditions, recommended route and adjudication rationale.

**Proposed corpus minimum:** 300 candidate documents/events across multiple markets and languages, plus ≥100 labelled entity pairs and ≥50 initiative association scenarios. Minimum is a design target; increase sample to obtain stable per-signal estimates. Include positive, negative and ambiguous variants for each activated rule. Separate training/tuning from held-out evaluation. Protect internal/PII fixture data.

## 3. Test dimensions and proposed thresholds
| Metric | Definition | Initial target / policy |
|---|---|---|
| Citation traceability | Relevant factual output claims linked to auditable source | 100% for actionable assertions |
| Exact entity identity | Correctly matched distinct legal/corporate entities | ≥98% on labelled evaluation set |
| Active tender status | Alerted as open while actually expired/awarded | Zero tolerated in reviewed critical-alert set |
| Duplicate priority alerts | Identical unchanged events repeatedly surfaced | <2% in controlled replay |
| Signal precision | Correct detections / all detections | Per-signal threshold set after labels; high-priority recommendations ≥85% BDM-relevant proposed |
| Recall on monitored sources | Known relevant labelled events found within configured source bounds | Pilot target ≥95% for designated fully monitored fixtures |
| Initiative link correctness | Correct programme grouping and separation | Threshold calibrated by programme gold set |
| Buyer and route accuracy | Correct role/authority/supplier-route interpretation | Human reviewed, track error categories |
| Unauthorised external/CRM writes | Privileged effects without approval | Zero |
| Critical collector failure visibility | Outage marked degraded, surfaced to owner | 100% in fault injection |
| Replay determinism | Same input and versions do not duplicate state | 100% idempotency required |

These are **proposed**, not observed. Do not claim exhaustive market recall.

## 4. Stage-by-stage test plan
### Collection tests
API pagination; timestamp/cursor; 200/304/404/401/429/5xx; schema drift; consent/licence limitations; partially retrieved batches; incident recovery; daylight-saving and source timezones. Validate stable ID and version.

### Extraction tests
Named company vs subsidiary; multiple counterparties; award vs tender; published date vs event date; explicit vs implied funding; permanent vs contract job; empty/malformed documents; multilingual technical procurement terminology; adversarial instruction embedded in document. Every output must satisfy JSON schema, source citation and allowed vocabulary.

### Identity tests
Company aliases; new legal entity; shared group domain; name collision; parent/subsidiary; acquisitions; rebrands; ambiguous LinkedIn and company references; duplicate CRM organisations; same contact moving companies. No automatic high-risk bank entity merging without strong identity evidence.

### Signal/initiative tests
Syndicated announcements with single original origin; unrelated initiatives in same account; programme stage changes; late amendments; similar-name vendors; old article republished; completed contract; positive signal contradicted by `NEG`; direct-route block vs viable SI route.

### Sales-action tests
Wrong role, buyer authority inferred from title, no procurement route, unapproved case proof, suppressed prospect, ownership conflict, insufficient delivery fit, uncertain supplier status. Recommended next action must correspond to a real evidence gap or buyer commitment and include owner/objective/trigger.

### State/CRM tests
Meeting held not sufficient for qualification; material CRM diff requires approval; duplicate webhook with same ID executes once; write timeout reconciles before retry; expired authorisation rejected; disqualified lead not resurrected absent material new evidence.

## 5. Human review rubric
Reviewers score each candidate on 0–2 or explicit pass/fail: company correctly identified; original event genuinely current; engineering workstream plausible; external demand classification honest; procurement access supported; correct buyer role; appropriate counterevidence; actionable next step; useful to spend seller time. Store field-level reasons, not only thumbs-up/down.

## 6. AI regression and prompt versioning
Version model ID, provider, prompt template, tool list, response schema, temperature/parameters where applicable, retrieval configuration, signal catalogue version, source snapshot and scoring version. Before any prompt/provider change, run gold corpus and compare confusion matrices and structured error changes. Fail deployment when high-severity regressions occur; allow controlled canary rollout and rollback.

## 7. Production quality monitoring
Dashboards: event volume by source vs baseline, source health, verification failures, uncited claims, duplicate rate, account match review queue, incorrect buyer rate, research escalation usage, drift in high-priority alert acceptance, open-tender deadline misses, stale initiatives and source licence violations. Trigger quality incidents for sudden source-yield drop, unusually many high-score opportunities, or unexplained changes in account matching.

## 8. Observability traces
A recommendation's trace references: source retrieval run → document ID/hash → source claim IDs → event ID → signal/rule version → entity match decision → initiative links → researched commercial questions → negative/counterevidence → action and approver → observed feedback/outcome. Provide an internal 'Why this recommendation?' view sufficient to reproduce result.

## 9. Failure mode severity
- **SEV-1:** unauthorised outbound/CRM write, restricted data leakage, corrupted ownership, loss of critical approvals.
- **SEV-2:** missed urgent tender deadline due to monitoring/processing failure, widespread unverified claims, source outage across important account families.
- **SEV-3:** noncritical enrichment provider degraded, partial search coverage or individual low-priority incorrect association.
Response and escalation defined under 13; incidents should feed regression fixtures.

## 10. Production release gate
Cannot go live until: source/credential approval; data schema migration tested; gold regression passes; >1 retriable fault scenario tested; approval gate proven; critical source health visibility; master-list matching reviewed; security/privacy sign-off; BDM task-based usability review; recovery drill; bounded provider spending; known limitations published.

## 11. Continuous evaluation
Monthly per-signal precision/false-positive review; quarterly source coverage re-evaluation; win/loss and buyer-confirmed need analysis by sales play and market; small-cohort uncertainty shown. Keep outcome measurement separate from system accuracy. Do not claim source or model uplift based on a small number of positive replies.
