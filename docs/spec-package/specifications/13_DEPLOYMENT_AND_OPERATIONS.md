# 13 — Deployment, Jobs, Monitoring & Operational Runbooks
**ID:** RI-OPS-13 · **Version:** 1.0 · **Status:** Proposed operations standard · **Depends on:** 02 Sources, 04 Data, 09 Integrations, 11 Security, 12 QA, 14 Cost

## 1. Target runtime
Cloud-hosted web app + API + durable workers + PostgreSQL + permissible object storage + secrets manager + operational logging. Start with modular services and managed infrastructure; separate worker types for network fetch, extraction, verification, deep research, CRM sync and notification. Use EU-relevant hosting/privacy review before cloud selection. Local development is not production deployment.

## 2. Environments and promotion
`development` (synthetic/sanitised), `staging` (sanitised or approved restricted replica), `production` (live source and CRM). Each has separate credentials, databases, queues, storage, webhooks and budgets. Production writes disabled unless approved role/system policy. CI pipelines run lint, type, unit, integration, security scans, schema migration dry run, gold regression and controlled deployment. Protect main branch and production secrets.

## 3. Service topology
| Service | Responsibility | Notes |
|---|---|---|
| Web UI | Seller/admin workflows | RBAC, summary-first UX |
| API | Domain data, approval endpoints, authorised queries | Strong request schema and audit |
| Collector workers | Official API/feed and targeted monitoring | Per-provider rate limit and idempotency |
| Research workers | Bounded AI extraction and investigation | Token/provider/cost budget and output schema |
| Graph workers | Identity, programme correlation, negative checks | Durable transactional updates |
| CRM worker | Pipedrive reconciliation and approved writes | Separate privileged credential |
| Notification worker | Daily digest and urgent alerts | Dedup/throttle, safe content |
| Scheduler | Source poll plans, backfill, freshness checking | Durable planned execution |

## 4. Job lifecycle
`scheduled → claimed → fetching → parsing → verifying → committing → completed`; exceptions: `retryable_failed`, `permanent_failed`, `requires_approval`, `quarantined`, `cancelled`. Each job has stable ID, payload hash, connector version, attempt count, max attempts, deadline, provider budget, run trace and parent correlation ID. Cursor transaction commits only after data persistence succeeds.

## 5. Scheduling defaults
| Workload | Initial schedule | Escalation |
|---|---|---|
| Official tender feeds | Daily | Deadline-sensitive recheck before alert |
| Priority account search | Daily | Material event → targeted refresh |
| Jobs / engineering pages | 2–4 times weekly | New cluster → workstream investigation |
| Company IR / press | Daily or source-aware | Significant announcement → same-day pipeline |
| Vendor/SI feeds | Several times weekly | Programme award → candidate event |
| Relationship/CRM | Webhook + daily reconciliation | Ownership/suppression change → priority processing |
| Strategic initiative status | Weekly | Missed milestone → verification |
| Source health | Each run + periodic checks | High-priority failure alert |
| Signal quality | Monthly | Regression canary after rule change |

No claimed guaranteed detection latency until measured; cadence must respect source limits and costs.

## 6. Retry, DLQ and error classification
`429` exponential backoff with provider `Retry-After`; `5xx/network` bounded retries; `401/403` require credential/permission investigation; `404` can indicate deleted item or source drift; malformed schema quarantined; policy/permission block stops collector. Store dead-letter payload safely, expose recover/replay procedure. Never turn inaccessible careers page into all jobs closed.

## 7. Idempotency and transactions
Enforce durable natural keys at ingest and recommendation levels. Import staging and canonical merge in transactions. Persist source cursor only once batch records are committed. CRM approved executor uses immutable idempotency key and fetch-before-retry reconciliation. Notification `dedup_key = account+initiative+material_change+recipient+period` prevents repeated alerts.

## 8. Source health and coverage SLOs
Proposed operational service-level indicators: `source_success_ratio`, `hours_since_last_success`, `missed_due_runs`, `document_change_delay`, `priority_account_coverage`, `verified_event_processing_delay`, `critical_deadline_recheck_rate`, `approval_write_success`, `research_cost_guardrail`. Final SLOs must be calibrated after source qualification and observed workload. Show each affected account's degraded monitoring, not just aggregate uptime.

## 9. Backup and recovery
Encrypted daily database backups with point-in-time recovery if supported, scheduled restore drill, object storage lifecycle, versioned schema migrations and rollback migrations or forward-fix plans. Define RPO/RTO with business/security stakeholders; don't invent promised times. On restore: reconcile provider webhooks/cursors before accepting new writes; replay with idempotency guarantees.

## 10. Runbooks
### RB-01 Source auth expires
Detect 401/403 → pause connector → mark affected coverage degraded → notify admin → rotate/re-authorise credential → test health → resume from last committed cursor → verify gaps via backfill.
### RB-02 Tender deadline wrong
Disable affected urgent notices → fetch original current official record → correct event and alert affected owners → log incident/impact → add regression fixture and review extractor.
### RB-03 Provider outage
Mark degraded, stop dependent deep research, continue other sources, preserve budget and task queue, retry with bounded backoff, communicate coverage limitation.
### RB-04 CRM double-write risk
Freeze write worker → compare remote target IDs and idempotency keys → reconcile exactly-once outcome → investigate approvals/audit before resume.
### RB-05 Identity false merge
Quarantine affected opportunity routes → split canonical identities preserving lineage and audit → rerun relevant graph/scoring → notify impacted sellers.
### RB-06 Prompt injection incident
Quarantine source content → disable influenced action path → review exposed data and model trace → update safety fixture and response procedure.
### RB-07 Paid research cost spike
Enforce hard stop, suspend escalation, inspect provider calls, cache and query cardinality, obtain budget approval before resume.

## 11. Observability and alerts
Logs JSON with request/job IDs, source, connector version, model version, result type, error class, token/cost usage; redact secrets/PII. Metrics dashboards and distributed traces for critical paths; alerts on high-priority source outage, queue backlog, repeated schema errors, unauthorised write attempt, high duplicate rate and unexpected cost. Dashboards identify which accounts and signals are affected by outage.

## 12. Maintenance and schema change
All schema/rule/provider changes reviewed, versioned, migrated and regression-tested. New connectors enter staging, check data licence and run sampled replay, then canary release. Keep provider contract version and deprecation watch. Quarterly audit of unused sources, operational cost and source coverage gaps.

## 13. Production checklist
DNS/TLS; roles and SSO; provider secrets; source legal review; data retention; privacy policy and data flows; source health; daily digest recipient test; degraded-mode test; backups restore; timezones; DLQ; cost budgets; approval write gate; ownership reconciliation; incident on-call/contact; observability; manual rollback; documentation owners; release approval.
