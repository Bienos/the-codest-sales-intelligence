# The Codest Revenue Intelligence Platform — Production Design Package V4.0

**Contents:** One comprehensive master architecture, **15 implementation-level technical specifications**, the original 64-rule buying-signal tracker CSV and a generated human-readable signal inventory. All files are Markdown except the original CSV. This is a **design handoff**, not working code or confirmation that any third-party API is connected.

## Start here

1. [Master Architecture V4.0](THE_CODEST_REVENUE_INTELLIGENCE_MASTER_ARCHITECTURE_V4.md) — commercial goals, all 27 architecture sections, system boundary, source strategies, seller workflow, data model, standards.
2. [15 — Implementation Plan](specifications/15_IMPLEMENTATION_PLAN.md) — dependencies, release gates and definition of done.
3. [04 — Data & Identity Model](specifications/04_DATA_AND_IDENTITY_MODEL.md), [02 — Sources](specifications/02_SOURCE_INTELLIGENCE.md), [03 — Signal Engine](specifications/03_SIGNAL_ENGINE.md), [07 — Opportunity Reasoning](specifications/07_OPPORTUNITY_REASONING.md) — technical critical path.
4. [12 — Reliability & Evaluation](specifications/12_RELIABILITY_AND_EVALUATION.md) and [11 — Security](specifications/11_SECURITY_AND_GOVERNANCE.md) — non-negotiable quality and permission gates.

## The 15 specifications

| No. | Specification | Covers |
|---:|---|---|
| 01 | [Product Requirements](specifications/01_PRODUCT_REQUIREMENTS.md) | Users, use cases, functional requirements, outcomes |
| 02 | [Source Intelligence](specifications/02_SOURCE_INTELLIGENCE.md) | Provider/source classification, contract, monitoring and coverage |
| 03 | [Signal Engine](specifications/03_SIGNAL_ENGINE.md) | Original signal tracker, event ontology, rule/schema, negatives |
| 04 | [Data & Identity Model](specifications/04_DATA_AND_IDENTITY_MODEL.md) | Canonical entities, lineage, temporal data, matching, SQL examples |
| 05 | [Initiative Intelligence](specifications/05_INITIATIVE_INTELLIGENCE.md) | Programme/workstream graph, suppliers and counterparties |
| 06 | [Enrichment Architecture](specifications/06_ENRICHMENT_ARCHITECTURE.md) | Six-stage waterfall, source precedence, provider interfaces, cost gates |
| 07 | [Opportunity Reasoning](specifications/07_OPPORTUNITY_REASONING.md) | Commercial gates, counterevidence, scoring, state machine |
| 08 | [Buyer Access & Sales](specifications/08_BUYER_ACCESS_AND_SALES.md) | Attention/intent/access, buying committee, supplier routes, sales plays |
| 09 | [Integrations](specifications/09_INTEGRATIONS.md) | Google Sheets, Pipedrive, Exa, ATS, Apollo, notifications and approvals |
| 10 | [User Experience](specifications/10_USER_EXPERIENCE.md) | Today, Accounts, Opportunities, Explorer and Administration |
| 11 | [Security & Governance](specifications/11_SECURITY_AND_GOVERNANCE.md) | Least privilege, privacy, data licensing, action approvals |
| 12 | [Reliability & Evaluation](specifications/12_RELIABILITY_AND_EVALUATION.md) | Gold data, thresholds, regression and fault injection |
| 13 | [Deployment & Operations](specifications/13_DEPLOYMENT_AND_OPERATIONS.md) | Job orchestration, recoverability, environments, runbooks |
| 14 | [Cost & Capacity Model](specifications/14_COST_AND_CAPACITY_MODEL.md) | Parameterised provider costs, budgets and value measurement |
| 15 | [Implementation Plan](specifications/15_IMPLEMENTATION_PLAN.md) | Work packages, staged production release gates, sign-off |

## Supporting reference

- [Original 64-signal CSV](reference/The_Codest_Buying_Signal_Tracker_Original_64_Rules.csv) — unmodified user-supplied tracker. Use this as canonical rule provenance, not the generated specification examples.
- [Signal Baseline Inventory](reference/SIGNAL_CATALOG_BASELINE.md) — a generated human-readable inventory of all IDs, categories and initial strength/freshness values.

## Existing operational source workbooks

- [Financial Sector Master List](https://docs.google.com/spreadsheets/d/1J9vDQK3lQzZLP2tx2TkoHnQQQSFEA3-qRmXtoU4TX9Y/edit), especially `MASTER ACCOUNTS`, `BUYER CONTACTS`, `BUYING SIGNAL LOG`, `BUYER RESEARCH STATUS`.
- [Codest Outreach Base](https://docs.google.com/spreadsheets/d/1wJKkovlp4oFGo8aj3ayRlAH-oXZ4O-RGDK8LZme9EMc/edit), especially `Accounts`, `Contacts`, `Priority Outreach`.

These remain live; their access rights and schemas require verification when implementation starts. They are read-only inputs by default. Pipedrive owns account/deal commercial truth.

## Use this package with Codex

In the working repository, provide Codex access to the entire package and the existing business documents (where authorised). Require it to follow the dependencies and acceptance gates in `15_IMPLEMENTATION_PLAN.md`, beginning with the data schema, source rights and gold evaluation set. It must verify API versions and entitlements, not assume that proposed candidate providers are connected. Require implementation commits, test results and deployment evidence separately from these design documents.

### Non-negotiable decisions

- Initiative/workstream is the central commercial record, not a generic company intent score.
- Discover events first, match companies to master lists and separately monitor existing accounts.
- Separate facts, claims, signals, hypotheses, buyer-confirmed needs and CRM deals.
- Evaluate direct bank, vendor and prime-contractor access independently.
- Use source-specific collectors and monitored coverage, not one AI search provider for everything.
- Human approval for outbound contact, material CRM writes, paid contact enrichment and commercial commitments.
- Report source failures and uncertainty rather than silently returning zero opportunities.
- Define success by buyer-confirmed, commercially feasible engineering opportunities and eventual revenue, not alerts.

## What remains intentionally undecided

Cloud host/region, exact provider entitlements/costs, final scoring weights, legal/privacy policies, contact-data purchase approvals, live CRM field mapping, production SLO values, approved external case references and rollout ownership. These require actual organisational decisions or live validation; no specification fabricates them.
