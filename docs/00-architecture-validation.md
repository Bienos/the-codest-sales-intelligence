# Architecture validation, conflicts and decisions

Validated 2026-11-20 against all 15 specifications, the master architecture V4.0, the README and the unchanged 64-rule CSV (SHA-256 `c2ffda8e…b680`, verified by code and CI test).

## Result
The architecture is implementable as specified. No part was dropped. Where the package left something open, the decision is recorded below. Nothing here claims a third-party API works: the sandbox that built this blocked egress to every source and to Google (see `integrations/INVENTORY.md`).

## Conflicts / ambiguities found and how they were resolved
| # | Finding | Resolution |
|---|---|---|
| C1 | Spec 15 lists "R1 production source connectors", but 02 §11 forbids enabling unqualified connectors. | Connectors are built and tested on fixtures; **every source ships `pending_connector_validation`**. Activation requires a certification record (code-enforced). |
| C2 | Master §12 gives illustrative weights that sum to 100 but 07 §6 says "no probability". | Weights implemented as versioned, explicit rules (`SCORING_VERSION`), labelled *provisional* in API and UI. Confidence, completeness, negative severity are separate columns. |
| C3 | Master §5 says "validated subset of ~15–25 rules"; the brief says "import all 64 and build an executable catalogue". | All 64 imported immutably (DB trigger). **17 have detectors** and ≥3 pos/≥3 neg/≥2 ambiguous/≥1 expiry fixtures (161 total, CI-gated). 47 stay `defined_not_activated`. |
| C4 | Spec 13 names Next.js; brief permits justified alternatives. | Vite + React + TS SPA served by the API (ADR-0003). |
| C5 | "Durable workflow/queue service" vs "no unnecessary infrastructure". | PostgreSQL job queue (`SKIP LOCKED`), ADR-0002. |
| C6 | Demand ladder D1–D4 vs `external_demand_status` (unknown/inferred/explicit/buyer_confirmed). | Mapping fixed in code: D1→explicit, D2→inferred, **D3/D4→unknown**. A verified programme is never external demand. |
| C7 | 10 §3 wants Today to show "existing vs new" while 07 §10 says membership must not inflate score. | Membership affects *coverage* and section placement only; it is not a score input (test: score components). |
| C8 | 04 says tab "gid" in links is unreliable. | Import is by verified tab *title* + header contract; contracts derived from live metadata (15 tabs). Row identity = composite key + content hash (sheets have no row IDs). |
| C9 | Spec 06 allows Apollo; 14 says no invented prices. | Provider price unknown ⇒ spend cannot be approved (code path + test). Default budget caps are zero. |
| C10 | 09 wants "approval executor, not an LLM worker" yet UI offers an Execute button. | Execute endpoint is a human-authenticated call to a separate function with its own credential class; AI paths cannot reach it. |

## Live findings from the real workbooks (read-only inspection)
- `Priority Outreach` has 47 columns of which 28 trailing headers are blank → tolerated only if empty of data.
- `Contacts` has a duplicate header (`Buyer Priority`) → deterministic `#2` suffix.
- Employee counts appear as `218 271`, `1,027 employees (Exa estimate, reverify)`, `580 Exa estimate` → parsed with basis/uncertainty; ICP-boundary estimates are flagged, never rounded.
- Hyperlink cells (`Source`, `LinkedIn`) lose targets in text export → importer reads FORMULA rendering for contracted columns (unverified live).
- Workbook A is **read-only** for the inspecting account; B is editable → importer uses `spreadsheets.readonly` and has no write method.
- `BUYING SIGNAL LOG` references rows like `MASTER ACCOUNTS!A43` → row numbers drift on sort; lineage uses identity, not position.
