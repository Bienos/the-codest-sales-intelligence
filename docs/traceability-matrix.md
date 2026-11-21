# Requirements traceability matrix
Spec → implementation → tests. "Partial" = implemented but live/acceptance evidence missing; "Not built" = deliberately not done.

## Product requirements (spec 01)
| ID | Requirement | Implementation | Tests | Status |
|---|---|---|---|---|
| FR-001 | Read two workbooks by metadata + row lineage | `sheets/{client,contracts,importer,reconcile}.py` | `test_r0_sheets_identity` (replay, move, drift, lineage) | Built; **live sync blocked** (no credentials) |
| FR-002 | Ingest 64-rule CSV with immutable IDs | `signals/baseline.py`, trigger `legacy_signals_immutable` | `test_r0_baseline` (hash, tamper, immutability) | Done |
| FR-003 | Source polling, freshness/coverage | `ingest/pipeline.py`, `scheduler.py`, `source_health` | `test_r1_pipeline` (401/429/503/drift/stale) | Done on fixtures |
| FR-004/005 | Atomic events vs claims; mirror dedup | `events/{store,origin,extract}.py` | `test_r1_events` (syndication ×3 = 1 origin) | Done |
| FR-006 | Org matching, parent/subsidiary caveats | `identity/resolver.py` | `test_r0_sheets_identity` (6 identity cases) | Done; gold set (≥100 pairs) not labelled |
| FR-007 | Events→initiatives/workstreams | `initiatives/link.py` | `test_r2_initiatives` (scenarios A–G) | Done |
| FR-008 | Demand vs access vs confidence separate | `opportunity/model.py` | `test_r3_opportunity` | Done |
| FR-009 | Research queue, cost + stop rules | `research/planner.py`, `budget.py` | planner + budget tests | Done |
| FR-010 | One actionable next step | `opportunity/actions.py` | DB constraints + tests | Done |
| FR-011 | No external/CRM write without authorisation | `approvals.py`, `crm/pipedrive.execute_approved` | kill switch, tamper, single-use, self-approval | Done |
| FR-012 | Digest suppresses unchanged | `notify/digest.py`, `readmodel.today` | digest dedup tests; Today reload test | Done |
| FR-013 | Reproduce prior recommendation | versions on rules/models/scoring; `opportunity_transitions`; "Why this recommendation" | trace endpoint tests | Partial (no snapshot-replay tool) |
| FR-014 | Buyer committee & supplier routes | `buyer/graph.py`, `initiatives/routes.py` | `test_r5_revenue` | Done (human-entered roles; no automated person research) |
| FR-015 | Pipedrive history with owner restrictions | `crm/pipedrive.py`, scope SQL | `test_r4_crm_notify`, `test_r4_api` | Built; **live blocked** |
| FR-016 | Feedback & conversion tracking | `outcomes.py`, `feedback`, `product_events` | `test_r5_revenue` | Done |
| FR-017 | Source admin/failure dashboards | `/api/admin/*`, Admin UI | API + Playwright | Done |
| FR-018 | Multi-language extraction | prompt/ontology multilingual; no EN/DE/PL/Nordic fixture matrix | — | **Partial** |
| FR-019 | Advanced partner graph/introductions | named-intermediary rule only | `test_r5_revenue` | Minimal (P2) |
| FR-020 | Proof matching from authorised docs | `proof_assets` + draft gate | proof tests | Governance done; **no assets loaded** |

## Work packages / release gates (spec 15)
| WP / Gate | Evidence | State |
|---|---|---|
| WP-00 gold corpus | 161 synthetic signal fixtures; **no real labelled corpus (300 docs / 100 pairs / 50 initiative cases)** | **Open — needs business labelling** |
| WP-01 foundation | migrations (checksummed), audit append-only, config validation, CI, Docker, backup + restore drill (passed locally) | Done except SSO/KMS (environment) |
| WP-02 import/identity | see FR-001/006 | Done on fixtures |
| WP-03 sources | registry (21 candidates), 6 connector families, health | Built; **0 sources certified** |
| WP-04 events/signals | 17/64 detectors, fixture gate, mutation-checked | Done for 17 |
| WP-05 initiative graph | scenarios A–G | Done |
| WP-06 enrichment | E1–E6 gates, approvals, single-use credits | Done; providers unverified |
| WP-07 qualification | gates G1–G7, state machine, counterevidence, actions | Done |
| WP-08 UI | Today/Accounts/Opportunity/Explorer/Admin, 25 browser checks | Done; **no BDM usability study** |
| WP-09 Pipedrive | read sync, webhooks, executor | Built; live blocked |
| WP-10 buyer/plays/deals | plays SP-01..10, drafts, deal view | Done |
| WP-11 hardening | metrics, runbooks | **Fault-injection beyond unit tests, SLOs, on-call: not done** |
| R0–R5 gates | `docs/releases/RELEASE_REPORT.md` | R0–R3 engineering-complete; R4–R5 complete in code, **not production-signed** |

## Added 21 Nov 2026
| Need | Implementation | Tests | Status |
|---|---|---|---|
| Sheet signal becomes verified evidence | `verify/{fetch,run}.py`, `verification_requests`, Today verify buttons | `test_r7_*` (quoted claim only, blocked, thin page, budget, SSRF) | Built; live model run pending first use |
| Exa discovery (spec 02 §7, 14 §5) | `discovery/{plans,run}.py`, `rules/discovery_plans.yaml`, scheduler | `test_r7_*` (budget reserve/settle, reported cost, auth stop, once per interval) | Built; live API run pending |
| Usage tracker (spec 14 §8, 13 §11) | `usage.py`, `provider_usage`, Administration, Usage (months, source, plan, every call, forecast) | `test_r7_*` | Built |
| Source library (spec 02 §3) | Administration, Sources (tier, cadence, licence, rules, documents, events, cost) | `test_r7_*` | Built |
| Instrumentation `source_opened` (spec 01 §8) | `/api/telemetry/source-opened` | `test_r7_*` | Built (was missing) |
| Find information by keyword | `api/search.py` (accounts and sheet text, 64 signals, sources, URLs, events, documents, claims, knowledge base), sidebar search box, Library (Signals, Sources, URLs and feeds) | `test_r8_*` (scope, injection, wildcards, filters) | Built |
| Knowledge base (ICP map, field guide) | `kb.py`, signed links, sandboxed frame, graceful when files are absent | `test_r8_*` (expiry, per-document, wrong key, not public, missing files) | Built; documents not yet loaded in production |
| Public landing and sign-in | `pages/Landing.tsx`, `design-system/landing.css` | browser check, desktop and phone | Built |
