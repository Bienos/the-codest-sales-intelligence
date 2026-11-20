# 06 — Progressive Enrichment & Research Orchestration
**ID:** RI-ENR-06 · **Version:** 1.0 · **Status:** Proposed provider-neutral contract · **Depends on:** 02 Sources, 04 Data, 05 Initiatives, 07 Opportunity, 11 Security, 14 Cost

## 1. Objective
Enrich **the minimum information required to change a sales decision** with field-level provenance, temporal validity and strict cost control. Never run expensive full-profile or personal-data enrichment on every discovered company.

## 2. Six-stage waterfall
| Stage | Inputs | Required output | Gate to next stage |
|---|---|---|---|
| E1 — Entity | Company from event | Canonical entity, domain, geography, size confidence, ICP | Identity confidence or review |
| E2 — Internal | E1, source sheets, Pipedrive | Ownership, contacts, account history, prior events | Relevant initiative or significant relationship |
| E3 — Technical | Verified event, initiative | Workstreams, scope, technologies, milestones, vendors | Plausible Codest fit and unresolved need |
| E4 — Commercial | E3 | External supplier route, incumbent, timing, eligibility | Accessible/direct/partner hypothesis or unknown requiring buyer conversation |
| E5 — Buyer | Selected route | Role-specific decision committee and existing access | Human accepted research candidate |
| E6 — Contact/outreach | Approved action | Verified necessary contact data, compliance and personalisation | Explicit prior approval for paid or personal-contact access |

## 3. Field-level evidence model
Each enriched field is `value`, `source_claim_ids[]`, `observed_at`, `valid_from/to`, `confidence`, `method`, `source_provider`, `verification_status`, `cost`, `manual_override?`. Missing, unknown, unavailable, contradicted, not-applicable and stale are different states. A blank `email` never becomes guessed pattern. Provider claims should not silently override authoritative official evidence.

## 4. Data sourcing hierarchy
1. Existing canonical/master/CRM records and approved internal sources.
2. Official corporate/regulatory/procurement sources.
3. Authoritative programme/vendor/company documents.
4. Secondary specialised publications, cross-verified for high-value claims.
5. Licensed enrichment APIs (firmographics, contacts) where necessary and authorised.
6. Targeted AI synthesis over retrieved evidence, never as independent fact source.

## 5. Provider adapter interface
```typescript
interface EnrichmentProvider {
  providerId: string;
  capabilities(): Promise<{ company: boolean; people: boolean; credits: boolean }>;
  estimateCost(query: EnrichmentQuery): Promise<CostEstimate>;
  searchCompanies?(q: CompanyQuery): Promise<EnrichedCompany[]>;
  searchPeople?(q: PeopleQuery): Promise<PersonCandidate[]>;
  enrichCompany?(id: ExternalCompanyId): Promise<EnrichedCompany>;
  enrichPerson?(id: ExternalPersonId, approvalId: string): Promise<EnrichedPerson>;
}
```
Actual method availability must be verified against provider/version/account permissions. No adapter is allowed to exceed budget or access personal-contact data without an authorisation receipt.

## 6. Identity and firmographics
Use verified legal identity where available. Normalise domains and subsidiaries without merging distinct procurement entities. For headcount, retain source, methodology (`official_report`, `provider_estimate`, etc.), `as_of_date`, range and confidence; do not interpret size estimates as exact. A `500–999` boundary must have explicit uncertainty if estimates straddle threshold.

## 7. Technical research protocol
For each initiative, research only relevant systems: known platform, integration API, core banking product, cloud environment, data migration, delivery scope, engineer vacancy requirements, launch milestones. Attribute every technology to a direct cited source; e.g., React listed in a generic vacancy is not evidence that the relevant programme uses React. Store `technographic_link_scope = company | team | initiative | inferred`.

## 8. Commercial access protocol
Order questions: is requirement real? is work already allocated? does target buy external engineering? direct vendor/framework/MSP route? can existing SI subcontract? geography/certification/regulated outsourcing constraints? who owns buyer decision? known budget? expected timing? Mark `unknown_until_buyer` when public research cannot resolve. Do not replace an unknown with probability fiction.

## 9. Buyer committee research
Determine likely technical owner, delivery owner, economic buyer, procurement route, risk/security stakeholders and existing champion. Keep `role_is_verified`, `person_current_role_verified_at`, `initiative_responsibility_verified`, `buyer_authority_inferred`, `contactability` separate. Current corporate role does not prove budget authority. Uncertain buyer identity should lead to a role-first discovery task.

## 10. Contact data restrictions
Only business-appropriate personal information, lawful basis and data minimisation; role/person sources must be auditable. Track email source, deliverability verification, suppression and applicable channel restrictions. Approved integrations may be used for targeted contact search, not indiscriminate harvesting. **Do not run any credit-consuming search, export, enrichment, or personal-contact access without the user's prior explicit approval.**

## 11. Adaptive research decision policy
ResearchPlanner receives `{hypothesis, known_claims, gaps, next_sales_actions, provider_costs, max_budget, last_queries}` and outputs `{question, method, expected_decision_effect, max_cost, stop_condition}`. Prefer a low-cost lookup for a specific claim. Escalate to deep multi-source research only if important uncertainty is resolvable and may change priority/route/action. Never perform repeated unconstrained broad research.

## 12. Cache and refresh policies
Cache by entity + field + authoritative source + as-of date + provider version; different TTLs for role/employment, company size, procurement state, product launch, technical stack and published contract. Event-driven invalidation on CRM role change, supplier award or procurement amendment. Disambiguate 'stale' from 'false'. Reuse evidence from previous research and store refresh history.

## 13. Failure and arbitration
If a provider returns no results, record `not_found_in_provider`, not absolute absence. If external data contradicts master data, preserve both, choose source precedence per 04 and queue review. If a provider times out, continue existing-account report with degraded enrichment completeness. If paid credit quota reached, stop affected research and retain clear partial status.

## 14. Quality KPIs
Field fill rate *with valid citations*, company identity correctness, current-role accuracy, wrong-buyer rate, enrichment credits per accepted recommendation, paid research return on additional decision-relevant evidence, staleness, PII retention exceptions, duplicate contact rate.

## 15. Acceptance cases
- Existing buyer email marked unverified remains unverified and is not promoted by model speculation.
- Two provider people with same name and different employers are not merged.
- A credible open tender triggers E4 before paid E5/E6.
- An account with no relevant workstream stops at E2/E3.
- BDM approval for one contact enrichment is not reusable across unrelated bulk jobs.
- Role change invalidates contact targeting, not historical conversation record.
- An expensive research call that cannot change action is skipped and logged.
