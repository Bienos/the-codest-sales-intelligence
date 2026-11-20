# 09 — External Systems & Integration Contracts
**ID:** RI-INT-09 · **Version:** 1.0 · **Status:** Proposed integration contract · **Depends on:** 02 Sources, 04 Data, 06 Enrichment, 11 Security, 13 Operations

## 1. Design principle
All external systems connect through versioned adapters that enforce permissions, idempotency, provider budget and source traceability. **No silent two-way synchronisation.** Google Sheets supplies baseline research; Pipedrive supplies account owner/deal truth; research systems supply external evidence; approved outreach systems receive only explicitly approved campaign payloads.

## 2. Integration inventory
| Integration | Direction | Role | Operations allowed by default |
|---|---|---|---|
| Google Sheets A | Inbound | Master financial-sector accounts, signals, buyers | Read metadata, bounded ranges, scheduled snapshots |
| Google Sheets B | Inbound | Outreach accounts and contacts | Read metadata, bounded ranges, scheduled snapshots |
| Pipedrive | Inbound; proposal-based outbound | CRM owner, organisations, people, activities, deals | Reads + webhook subscription; writes behind approval |
| Exa / alternate discovery | Inbound | Discovery search and focused research | Authorised search within budget |
| TED / UK FTS | Inbound | Official public procurement | Read official API |
| GLEIF / Companies House | Inbound | Identity verification | Read official API |
| ATS providers | Inbound | Public job postings | Read documented/public endpoints |
| Webpage monitor | Inbound | Specific page changes | Permitted monitoring only |
| Apollo / approved enrichment | Inbound | Verified contact/company data | Credit and PII access **only after approval** |
| Lemlist/engagement | Proposed outbound | Reviewed sequences, suppression/engagement context | Read where authorised; no auto-enrol or auto-send |
| Slack/email | Outbound notifications | Briefs and failure alerts | Approved destinations, limited detail |
| Internal knowledge | Inbound | Approved collateral, sales policy and proof | Role-scoped retrieval |

## 3. Google Sheets contracts
### Workbook A
ID `1J9vDQK3lQzZLP2tx2TkoHnQQQSFEA3-qRmXtoU4TX9Y`; canonical `MASTER ACCOUNTS`; auxiliary `5000+ CORPORATIONS`, `UK`, `NORDICS`, `DACH`, `POLAND`, `BUYER CONTACTS`, `BUYING SIGNAL LOG`, `BUYER RESEARCH STATUS`.
### Workbook B
ID `1wJKkovlp4oFGo8aj3ayRlAH-oXZ4O-RGDK8LZme9EMc`; `Accounts`, `Contacts`, `Contacts — Compact`, `New Account Research — Nov 20`, `Contact Research Queue`, `Priority Outreach`.

**Procedure:** fetch metadata → discover tab ID/name and bounds → header schema check → bounded range pages → stage raw values with source row refs → validate required columns → build canonical links → log imports and reconciliation errors. No assumption that user-provided deep-linked `gid` is the main account tab. Changes to tab names, headers or columns must trigger mapping review and preserve prior successful snapshot. No source writes without scoped approval.

## 4. Pipedrive contract
- Identify organisations by CRM ID where linked, otherwise canonical domain/entity match with review for ambiguity.
- Pull organisations, people, deal and activity metadata with least privilege; respect pagination, provider rate limits and field-specific access.
- Use documented webhooks for incremental updates; verify webhook signature/secret where the provider supports it, otherwise secure endpoint and validation.
- Implement periodic reconciliation to repair missed events.
- Pipedrive is authoritative for `owner_id`, active `deal_id`, stage, engagement and next activities; never set these from unverified AI research.
- Material writes require `write_proposal_id`, diff, exact target CRM ID, user/manager approver, approval expiry, audit ID and idempotency key.

**Approval example:** `operation=propose_activity`, `crm_org_id=...`, `owner=...`, `payload=...`, `approval_status=pending`. Only the approval executor (not an LLM worker) can call the CRM mutation adapter.

## 5. Research-provider contract
Separate interfaces: `search(query, filters)`, `retrieve(url/id)`, `research(question, allowed_sources, max_cost)` if provider supports it. Providers expose their actual capabilities and quota; features must be verified against current docs and account entitlement. Cache search results and original retrieval metadata. High-priority event claims require direct source content verification, not snippet-only citation.

## 6. ATS contract
Normalize job records: `provider`, `company`, `job_id`, `title`, `team`, `location`, `employment_type`, `contract_type`, `posted_at`, `updated_at`, `open_status`, `job_url`, `tech_keywords`. Preserve source semantics; a role does not inherently signal supplier eligibility. Track closure/removal separately from site timeout.

## 7. Procurement contract
Normalized notice: `source`, `notice_id`, `buyer_entity`, `procedure`, `lots`, `scope`, `published_at`, `amended_at`, `deadline_at`, `timezone`, `status`, `eligibility`, `contract_type`, `official_url`, `value_if_reported`, `evidence`. Recheck original official current notice before urgent alert; historical award is not active tender. Documents may be external references if storage rights prohibit copying.

## 8. Notifications and review links
Daily seller digest includes source-backed account summary and links to internal recommendation records. Critical tender alert can be immediate; avoid duplicating daily digest items. Delivery targets scoped by recipient/role; don't send personal details or confidential CRM notes into unapproved Slack channels. Include signed/internal deep-link, priority, last verification, owner, action and if relevant deadline timezone.

## 9. Event/webhook semantics
Every inbound webhook should be authenticated where possible, validated against provider schema, replay-safe, written to durable inbox, acknowledged promptly and processed asynchronously. `event_id` or composite stable key prevents repeats. `last_seen_version` and reconciliation watermark enable healing after service outage. On schema drift quarantine event and alert integration owner; do not silently discard.

## 10. Outbound action and approval object
```json
{
  "proposal_id": "proposal:uuid",
  "target_system": "pipedrive",
  "operation": "create_activity",
  "target_id": "crm:org:id",
  "payload": {"subject":"Verify integration workstream owner"},
  "requested_by": "user:...",
  "approval_required": true,
  "approved_by": null,
  "approved_at": null,
  "expires_at": null,
  "idempotency_key": "...",
  "status": "pending"
}
```
Write executor rejects absent/expired/mismatched approval. Record provider response IDs and recovery reconciliation.

## 11. Failure modes and fallbacks
- Google Sheets permission failure → preserve last timestamped snapshot, show stale status, do not fabricate current list.
- Pipedrive outage → continue public research; disable owner-sensitive outbound recommendations when ownership cannot be checked.
- Exa unavailability → structured and monitored feeds continue; queue deep research, display reduced coverage.
- ATS 404/403 → mark source health as unavailable; don't report 'all jobs closed' without affirmative source semantics.
- Approval executor failure → retry with idempotency key, show status and reconcile before a second write.

## 12. Integration tests / production qualification
Mock and provider-sandbox tests for pagination, rate limiting, malformed/changed schema, duplicate webhooks, retry after timeout, cursor repair, stale permissions, suppressed contacts, CRM ownership conflict and regional date parsing. Maintain a connector certification record: provider doc URL, auth entitlement, verified endpoints, data licence, technical owner, last tested date, SLA/limits and rollback path.
