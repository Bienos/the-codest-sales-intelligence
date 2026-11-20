# 11 — Security, Privacy, Permissions & Governance
**ID:** RI-SEC-11 · **Version:** 1.0 · **Status:** Proposed security design, subject to legal review · **Depends on:** 01 Product, 02 Sources, 04 Data, 09 Integrations, 13 Operations

## 1. Security objectives
Protect customer/prospect information, internal sales history, personal data, licensed source content and API credentials; prevent unauthorised contact, CRM changes and disclosure of restricted client proof. EU/UK relevant privacy, electronic marketing, vendor access and outsourcing obligations require legal/compliance review before launch. This document is an engineering specification, not a legal conclusion.

## 2. Data classification
| Class | Examples | Requirements |
|---|---|---|
| Public | Public procurement notices and official announcements | Respect origin and reuse licence; attribution |
| Licensed | Research-provider content and proprietary aggregators | Contract-compliant retention and retrieval; no unnecessary redistribution |
| Internal | Account lists, playbooks, scoring, internal sales decisions | BDM/team-scoped access, audit |
| Confidential | Deal negotiation, pricing, customer contracts, restricted proof | Need-to-know, strict external release gate |
| Personal | Contact names, job history, verified work email, communications | Lawful purpose, minimisation, access, retention, suppression/deletion |
| Secrets | API tokens, OAuth refresh, webhook signing secrets | KMS/secret manager, never logs/LLM prompts where avoidable |

## 3. Role-based access and row scoping
BDM sees assigned and authorised shared accounts; managers see team scope; research ops may see evidence, but restricted CRM notes require separate role; marketing sees approved collateral only; compliance/security have audit-specific access. Enforcement is backend/database-level, not hidden UI buttons. Apply tenant/team/owner scopes to queries and background jobs. Record every privileged read/write and administrative permission change.

## 4. Action policy matrix
| Action | Default |
|---|---|
| Read approved public source | Autonomous within terms |
| Import existing read-only Google Sheet | Autonomous with granted account scope |
| Search public company details | Autonomous within usage policy |
| Access personal contact data from paid provider | Requires **prior explicit approval** |
| Purchase enrichment/search credits | Requires **prior explicit approval** |
| Create an outbound draft | Allowed for authorised user, never auto-send |
| Send email/LinkedIn/call | Human approval; additional process constraints apply |
| Enrol in live sequence | Human approval, suppression/ownership check |
| Create or materially update Pipedrive deal | Human approval with field-level diff |
| Update CRM owner/stage | Explicit authorised approval; never inferred |
| Submit procurement/vendor registration | Human approval and legal/commercial review |
| Quote price/capacity/start date or client proof | Internal authorisation and source checks |

## 5. Approval object and enforcement
An approval must be specific to requester, approver, target, operation, immutable payload hash, justification, expiry, scope and execution ID. The system must reject stale, modified or mismatched approvals. Separate the reviewer UI from write-executor credentials; AI workers do not possess direct send/deal-write privileges. Audit records include who approved, exactly what changed and the remote result.

## 6. Contact and outreach compliance
Maintain privacy purpose, lawful-basis assessment, relevant geography, data source, first-seen timestamp, required notices, suppression/opt-out, retention schedule and permitted channels. A verified work email is not a communication permission. Honour suppression across all downstream tools. Process requests for access/correction/deletion with propagation and audit as advised by legal counsel. Do not infer sensitive personal traits or collect private community content without authorised access.

## 7. Source licensing and retention
Document each source's allowed retrieval, retention, derivative uses, redistribution and citation. Where full-text retention is prohibited, store metadata, stable original URL and limited permitted extracts. Do not bypass paywalls, authentication or site blocks. Respect provider quotas. Separate public source intelligence from internal buyer notes in generated outputs.

## 8. LLM security boundary
Fetched documents and email/CRM content are untrusted instructions. They can supply evidence, not control tool policy. Defend against prompt injection, malicious URLs, model output schema violations, secret exfiltration and instruction spoofing. Restrict model context to minimum necessary, pass cited source excerpts rather than full sensitive records, validate generated external text, and require manual approval for external action.

## 9. Credentials and infrastructure
Use managed secrets/KMS, key rotation, per-provider service principals with minimum scope, encrypted HTTPS, storage encryption, private network/egress allow-list where practical, per-environment separation and secure callback endpoints. No API tokens in repository, logs, test fixtures or exported documents. Backups encrypted and restore access audited.

## 10. Client case evidence and claims governance
Internal guidebook warns against unapproved case metrics and misleading client relationships. Maintain `proof_asset` access policies: external-approved, internal-only, limited-audience or restricted. A sales draft using a restricted client name/metric must be blocked until approved. Avoid implying a bank is direct client where only an indirect involvement is established.

## 11. Logging and security observability
Log authorisation decisions, failed authentication, suspicious repeated requests, attempted unauthorised writes, prompt injection alerts, data export, deletion actions, key rotation and significant data-model changes. Mask PII and secrets. Retain access logs under reviewed retention policy; facilitate investigations without over-collecting personal data.

## 12. Threat scenarios and tests
- Prompt injection in retrieved procurement PDF tries to tell agent to post a token: treat as text, not instruction.
- Wrong BDM accesses another team's restricted notes: deny at API layer.
- Model crafts a plausible email and attempts to send: prohibited by absent scoped approval.
- Expired approval changes a CRM stage: reject.
- Suppressed email appears in enrichment output: do not propose outreach.
- Source licence blocks full storage: retain permissible metadata only.
- API key appears in log on connector failure: mask/redact and rotate.
- GDPR erasure changes canonical contact: delete or restrict derived caches and search indexes per policy.

## 13. Governance sign-off
Before go-live: approve DPA/vendor register, legal basis and marketing practices, retention/deletion schedule, international data flows, role matrix, source licence list, model provider terms, incident response, backup/restore, approved proof catalogue and CRM external-action policy. Required reviewers: business owner, engineering, security/privacy, CRM administrator and legal/compliance where applicable.
