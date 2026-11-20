# Risk register
| ID | Risk | Likelihood | Impact | Mitigation in code | Residual / owner action |
|---|---|---|---|---|---|
| R1 | Connector field names differ from live APIs | High | Med | Fixture tests only; `schema_drift` stops collector; activation needs certification | Run qualification per source; **owner: integration lead** |
| R2 | False entity merge (parent/subsidiary) | Med | High | Exact-name+country only auto-links; fuzzy/domain → review; contradictory IDs block; edges not merges | Reviewer capacity; gold entity pairs (≥100) not yet labelled |
| R3 | LLM hallucinated facts | Med | High | Verbatim anchor check, schema validation, discovery snippets never original, injection flag | Prompt/gold regression before enabling; none run (no key) |
| R4 | Demand over-claimed | Med | High | D3/D4→unknown demand; qualification gates; high-priority footing floor; counterevidence review | BDM relevance study (spec 12 target ≥85%) not run |
| R5 | Unauthorised CRM write/outbound | Low | Critical | Approval object + payload hash + two-person + kill switch default off + executor-only credential; no send path exists | Pen-test; confirm Pipedrive token scopes |
| R6 | PII exposure / retention | Med | High | RBAC+row scope in SQL; PII reads audited; emails never in API/digest; suppression sticky; erasure column | **Legal/DPA sign-off, retention schedule, erasure propagation to indexes not built** |
| R7 | Source outage read as "no events" | Med | High | Health states, staleness→degraded, coverage on Today/Explorer, cursor-after-commit | SLOs unmeasured |
| R8 | Cost overrun | Low | Med | Zero default caps, reserve-before-call, hard cap, release on failure | Obtain price lists (spec 14 §10) |
| R9 | Stale owner data | Med | Med | Ownership unverified ⇒ outreach/CRM proposals disabled; webhook+reconcile | Reconcile job schedule to be wired in prod |
| R10 | Weights are guesses | High | Med | Labelled provisional; confidence separate; no probabilities | Outcome study (needs ≥30/cohort) |
| R11 | Row scope gaps in new queries | Med | High | Central `org_scope_sql`; 404 on out-of-scope; tests for 2nd seller/manager/analyst | Add a scope lint/test per new endpoint |
| R12 | Single Postgres is SPOF | Med | Med | Backups + restore drill script (passed locally) | Managed PITR, RPO/RTO to be agreed |
