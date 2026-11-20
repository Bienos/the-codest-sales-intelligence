# 10 — Seller Experience, Interface & Interaction Model
**ID:** RI-UX-10 · **Version:** 1.0 · **Status:** Proposed product UX contract · **Depends on:** 01 Product, 07 Opportunity, 08 Sales, 09 Integrations, 11 Security

## 1. UX goals
The product must reduce cognitive and administrative load, not become another CRM. Default experience: **short recommendation → source and counterevidence → specific action → feedback**. Show confidence and unknowns without hiding them in an advanced tab. Existing master-list matches appear prominently. Information depth expands only when requested.

## 2. Information architecture
Primary navigation: **Today**, **Accounts**, **Opportunities**, **Market Explorer**; privileged **Administration**. Cross-navigation uses canonical `account_id`, `initiative_id` and `hypothesis_id`. Every view has recent change history and audit provenance accessible to the authorised user.

## 3. Today workspace
### Default elements
- Critical time-limited items (deadlines, eligibility changes, source failures affecting urgent targets).
- Existing account developments; show original master list and Pipedrive owner.
- Actionable new qualified-ICP discoveries, separate from the master list.
- Relationship/re-engagement suggestions.
- Existing deal blockers and commitments.
- Research awaiting review.

Each card: company and legal context, existing/new marker, initiative, what changed, evidence level, route, priority, counterevidence, next action, owner, due/trigger, original source links, last checked. User actions: `Review sources`, `Investigate missing question`, `Accept`, `Dismiss with reason`, `Draft outreach`, `Propose CRM action`, `Monitor`, `Reassign request`. No direct external send control without approval workflow.

### Ranking semantics
Queue is ordered by urgency and actionable commercial route while preserving coverage of owned accounts. Large news coverage does not increase card count. Show explicit empty state: "No new high-priority changes among successfully monitored sources" plus a separate coverage-health indicator.

## 4. Account Intelligence workspace
Tabs: `Overview`, `Initiatives`, `Signals & evidence`, `Technology`, `Suppliers`, `Buyers`, `Access`, `CRM history`, `Research & coverage`. Overview starts with: current opportunity recommendation; latest important changes; known confirmed/unknown; assigned owner; procurement route status; next action. Timeline distinguishes event date, published, discovered and last verified. Group/parent relationships shown without aggregating separate procurement entities as one buyer.

## 5. Initiative drilldown
Show programme stage, milestones, supporting events, workstreams, participating entities, known supplier coverage, evidence and counterevidence. Differentiate official name from system working title. Each workstream can have multiple **route hypothesis** cards: bank-direct, vendor-side, SI-side, each with distinct access status and owner. A source view opens precise original source context.

## 6. Opportunity Development workspace
Sections: `Current qualification state`, `Problem and evidence`, `Stakeholders`, `Supplier route`, `Technical delivery fit`, `Buyer-confirmed notes`, `Risks`, `Next commitments`, `Approved proof`, `Suggested communications`, `CRM proposals`. Before outreach, produce a reviewable concise contact-specific angle; before meeting, evidence-backed brief; after meeting, separate facts and assumptions; in procurement, track eligibility docs, decision-makers, due dates and internal approvals. Only authorised CRM changes appear as executable proposals.

## 7. Market Explorer
Search companies, people where authorised, initiatives, events, vendors, procurement notices. Filters: country, market, sector, headcount band, existing vs new, owner, signal family, source authority, event date, programme stage, vendor, accessible route, evidence confidence, last verification, blocked status. Query results should explain whether ranking is ICP fit, freshness, opportunity priority or confidence. Display source coverage so 'no results' is not falsely interpreted as no market activity.

## 8. Administrative UX
Connector health grid with `healthy / degraded / failed / paused / unconfigured`, last success, expected cadence, affected accounts and source families. Other views: rule catalogue and versions, source coverage, budget consumption, paid enrichment approvals, identity review queue, model evaluation, security/retention, audit logs, deployment/recovery status. Show actionable incident resolution, not a wall of metrics.

## 9. Key component specification
| Component | Required behaviour |
|---|---|
| Evidence pill | Opens exact source URL, published/retrieved dates, attributed claim, authority |
| Confidence | Plain-language label + why; distinct from opportunity score |
| Research gaps | Specific unanswered commercial question + next research option |
| Counterevidence | Visible in summary for high priority, not hidden |
| Access route | Direct/partner/relationship/unknown/blocked with evidence |
| Material change | Shows before vs after with dates |
| Approval panel | Exact CRM/outbound diff, recipient, target, owner and authoriser |
| Duplicate warning | Shows likely duplicate and identity confidence; reviewer merge/split |
| Review feedback | Accept/reject with compact reason, optional note |
| Coverage status | Sources monitored and not monitored; failed source indicator |

## 10. Workflow examples and acceptance
**Morning:** seller can assess a lead in approximately 30 seconds (UX design goal) with one click to original evidence. **Bank account:** sees a single programme rather than repeated articles. **Urgent tender:** exact timezone deadline, last status recheck and eligibility caveat. **Dismiss:** mandatory reason in dropdown and optional free note; don't resurface unchanged. **Re-open:** material new event triggers new alert referencing old dismissal. **Buyer data:** contact fields only visible to authorised roles. **CRM approval:** shows the diff before execute and success/failure outcome. **Empty:** distinguish healthy no-news from failed monitoring.

## 11. Accessibility and internationalisation
Keyboard navigation, screen-reader labels, WCAG-aligned colour contrast, pagination/virtualisation for large research sets, localisation for EN/PL initially, date/time display in user's timezone plus original tender timezone, preservation of source-language excerpts and multilingual names. Avoid exposing internal legal claims in unreviewed external copy.

## 12. Performance and degradation
Use cached account summaries and lazy-load deep evidence. UI surfaces last updated timestamp. If a research job takes longer, create durable task and show status without blocking browsing; no invented completion time. If Pipedrive is unavailable, show stale owner indicator and disable dependent write actions. If source coverage degraded, show warning on account, not only admin screen.

## 13. Usability validation
Conduct task-based seller reviews: can user distinguish fact from hypothesis; choose a next action; identify route blocker; find source; recognise old news; validate contact owner; understand why lead disappeared/reappeared; approve CRM diff. Collect task success, time, error, action acceptance and qualitative feedback. Do not ship purely on visual appearance.
