# 02 — Source Intelligence & Collection Network
**ID:** RI-SRC-02 · **Version:** 1.0 · **Status:** Proposed integration contract · **Depends on:** 03 Signal Engine, 04 Data, 11 Governance, 13 Operations, 14 Cost

## 1. Purpose
Collect a *measurably covered* stream of current, legitimate, source-grounded observations. A source provides information, **not automatic buyer intent**. Collection must distinguish no findings from a missed/failed collection and identify whether original, syndicated or secondary evidence is being retrieved.

## 2. Source tiers
| Tier | Classes | Collection preference | Trust and use |
|---|---|---|---|
| S1 | Official procurement, regulatory, registry and company statements | Documented APIs/feeds | Strong evidence for the specific stated fact; procurement status still verify |
| S2 | Official careers, developer changelogs, IR, engineering blogs | Supported API/feed then change monitor | Strong observation of hiring/technical activity, not sourcing proof |
| S3 | Official vendor/SI/partner announcements | Feeds/monitor/search | Identify participants and declared scope, check who issued release |
| S4 | Established independent specialist media, public conferences | Search/feed/authorised transcript | Discovery/context; corroborate key claims |
| S5 | Public professional content and third-party aggregators | Search with terms/licence checks | Leads/attention context; not confirmed demand alone |
| Internal | Google Sheets, CRM, approved research and collateral | Connector read | Account history/ownership, not current external truth without validation |

## 3. Registry contract
`source_id`, `display_name`, `canonical_domain`, `source_tier`, `source_type`, `countries[]`, `languages[]`, `industries[]`, `signal_rule_ids[]`, `collection_method`, `endpoint_template`, `auth_mode`, `credential_ref`, `legal_basis/licence_notes`, `robots_access_policy`, `retention_policy`, `poll_interval`, `cursor_type`, `rate_limit`, `max_document_size`, `expected_fields`, `status`, `last_success`, `last_attempt`, `error_class`, `cost_profile`, `owner`, `reviewed_at`.

Suggested source entry (candidate; do not assume current API shape until verified):
```yaml
source_id: procurement-ted
source_tier: S1
source_type: procurement
provider: TED
method: official_api
canonical_docs: https://docs.ted.europa.eu/api/latest/search.html
countries: [EU]
languages: [en, de, pl, sv, da, no, fi]
signal_rule_ids: [SRC-01, SRC-02, SRC-03]
poll_policy: daily_with_cursor
raw_retention: source_permitted
health_policy: alert_after_failed_window
status: pending_connector_validation
```

## 4. Priority source registry candidate inventory
**Procurement:** TED (`docs.ted.europa.eu`), UK Find a Tender (`find-tender.service.gov.uk`), UK Contracts Finder (`contractsfinder.service.gov.uk`), country procurement platforms, relevant public bank supplier portals, approved MSP/VMS portals. **Regulatory:** FCA, EBA, ECB, relevant national authorities, Swift and European Payments Council where applicable. **Identity:** GLEIF API, Companies House and national company registers. **ATS:** Lever Postings API, Ashby public jobs API, Greenhouse job boards where supported; Workday and company sites through verified permissible methods. **Corporate:** investor relations, newsrooms, careers, approved corporate feeds. **Vendor ecosystem:** Temenos/Mambu/Thought Machine/Finastra/Avaloq; Form3/FIS/Fiserv/Adyen; GFT/CGI/Capgemini/Tietoevry/appropriate others. **Attention:** Finextra, FinTech Futures, The Paypers, LeadDev, CTO Craft, EBAday, techUK, UK Finance, Bitkom, Copenhagen Fintech. **Discovery:** Exa and replaceable alternatives. Every named provider is a **candidate to qualify**, not an assertion that an API entitlement or permission exists.

## 5. Retrieval adapter API
```python
class SourceConnector(Protocol):
    def describe(self) -> SourceDescriptor: ...
    async def health_check(self) -> HealthCheck: ...
    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch: ...
    async def fetch_by_id(self, external_id: str) -> RawItem | None: ...
    def canonicalize(self, item: RawItem) -> SourceDocumentDraft: ...
```
`FetchBatch` includes `items`, `next_cursor`, `has_more`, `requested_at`, `source_version`, `quota_usage`, `warnings`, `partial_result`; all network calls have timeout/retry budgets. Preserve stable external ID and canonical URL; content hash detection only for content with applicable storage rights.

## 6. Processing contract and incremental behaviour
1. Verify connector permission, authentication and available budget.
2. Use saved cursor/modified-since and provider-supported pagination; backfill is a separate limited job.
3. Store retrieval attempt, response metadata, item identifiers and allowed excerpts/snapshots.
4. Compare content/version fingerprint. Record `new`, `changed`, `unchanged`, `deleted/unavailable` distinctly.
5. Emit document-change events via queue; update cursor only after durable commit.
6. Classify authentication failures, rate limits, transient network, schema drift, access-denied and permanent 404 separately.
7. Record coverage loss if expected polling window missed; escalate if priority source unavailable.

**Idempotency key:** `(source_id, external_id, source_version|content_hash)` for ingestion and `(source_id, query_plan_id, date_window, cursor)` for batches where supported.

## 7. Source-specific semantics
### Procurement lifecycle
Store `notice_id`, buyer legal entity, title/scope, procedure, service codes, original issue date, amended date, submission deadline/timezone, eligibility, award status, linked lots, country, official detail URL. `open` only after verifying current notice/official status; changes must update existing notice record.

### Jobs lifecycle
Store employer, external posting ID, title, employment type, location/time zone, skills, related team, publication/updated dates, closed status. Contract wording, direct supplier route and hiring volume are **separate fields**. Presence of a role does not prove external agency eligibility.

### Website change monitoring
Select precise URL and page section. Prefer RSS or `Last-Modified` where reliable; snapshot content hash after normalising dynamic chrome; suppress tracking widgets. For dynamically rendered pages use permitted supported extraction; on repeated failure mark source degraded and investigate alternative access.

### Market search
Use search plan IDs (`signal+sector+geo+language+source tier+recency`) and persist exact query, result rank, retrieval timestamp, source domain and document URL. Search result snippets are discovery evidence only; fetch original sources before factual claims.

## 8. Account source coverage matrix
Per account and source family, maintain `coverage_state = monitored_healthy | monitored_degraded | source_not_found | access_restricted | not_applicable | not_configured`, `last_success`, `poll_schedule`, `last_material_change`, `source_ids[]`. Dashboard distinguishes `no_events_in_monitored_sources` from `coverage_insufficient`. Coverage denominator excludes only truly not-applicable categories; note unknowns.

## 9. Quality evaluation and controls
Monitor successful/failed runs, stale ratio, duplicate document ratio, cost per verified event, accepted event yield, source authority, source citation integrity, backfill completeness and missed-source event tests. Source health issues trigger operations notifications; never silently turn an outage into a negative buying signal.

## 10. Acceptance tests
- One official amendment updates a single tender entity and triggers one material-change event.
- A source with 401/429/timeout retains cursor, logs accurate error and degrades coverage after configured threshold.
- A repeated unchanged page emits no new candidate events.
- A republished article links to the originating announcement rather than becoming independent proof.
- A user-supplied master account without known supplier portal has `source_not_found/unknown`, not `no suppliers`.
- Search plans respect daily provider quota and stop at configured budget.

## 11. Qualification checklist before connector activation
Verify current provider docs, legal/ToS/robots, licences, auth entitlement, sample response schema, availability of stable ID, rate limits, supported geography, historic depth, data freshness, PII implications, data retention, cost and failure mode. Do not enable unqualified connectors in production.
