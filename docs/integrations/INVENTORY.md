# Integration inventory — status as of 2026-11-20 (build sandbox)

Legend: **Built** = adapter + tests on fixtures · **Live-verified** = exercised against the real service · **Blocked** = cannot run here (reason).

| Integration | Direction | Built | Live-verified | Blocker / what is needed |
|---|---|---|---|---|
| **Supabase Postgres** (`the-codest-sales-intelligence`, eu-central-1, PG 17) | store | ✅ migrations 0001–0008 applied via connector, checksums recorded in `schema_migrations` | ✅ verified: 69 tables, RLS on all, 0 grants to `anon`/`authenticated`, security advisor clean apart from intended deny-all INFO | App host needs the **session-pooler** connection string as a secret. Run `python -m ri.cli seed` once (loads the 64 rules + source registry). Performance advisor: 48 unindexed FKs to index once real data exists. Tested locally on PG 16 only. |
| Google Sheets A/B | in (read-only) | ✅ client, 15 contracts, drift pause, lineage | ⚠️ **Schemas inspected live** via Drive connector; **no API sync run** | Needs a service account with Viewer on both workbooks → `docs/runbooks/connect-google-sheets.md`. Egress to `sheets.googleapis.com` returned 403 here. |
| Pipedrive read sync/webhooks | in | ✅ | ⚠️ Payload shape confirmed by a live read via MCP (v2: `data[]`, `next_cursor`, `owner_id`) | Needs API token + `PIPEDRIVE_DOMAIN`; webhook basic-auth secret; field-key mapping (custom keys are account-specific hashes). Egress blocked. |
| Pipedrive write executor | out | ✅ approval-gated, kill-switch default OFF, fetch-before-retry | ❌ | Separate write token; needs CRM-admin approval of allowed operations (only `create_activity` is implemented). |
| EU TED | in | ✅ (search v3, pagination) | ❌ | Egress blocked; endpoint/field names are from documentation and **unverified**. Qualify per spec 02 §11. |
| UK Find a Tender (OCDS) | in | ✅ | ❌ | Egress blocked; OCDS semantics standard, endpoint params unverified. |
| Lever / Greenhouse / Ashby | in | ✅ | ❌ | Egress blocked; need per-employer board slugs (not in the sheets). |
| GLEIF | in | ✅ lookup/search | ❌ | Egress returned empty response. |
| Companies House, Contracts Finder, regulators | in | registry entries only | ❌ | Not implemented (candidates). |
| Page / RSS monitor | in | ✅ robots-respecting, section-hashed | ❌ | Needs per-account URLs + legal review per site. |
| Exa | in | ✅ adapter, plan IDs, budget gate | ❌ | No key; **price list needed** before any cap is set. |
| LLM extraction (Anthropic) | internal | ✅ schema + anchor verification (fake extractor tested) | ❌ | `ANTHROPIC_API_KEY`; prompt/gold-set regression must pass before enabling. |
| Apollo | in (credit-consuming) | ✅ people search/enrich, approval + single-use | ❌ | Key, entitlement, **price**, and explicit approvals. Disabled by default. |
| Slack / email | out | ✅ Slack webhook sink, signed links | ❌ | Webhook URL; email sink not built. |
| Lemlist | out | ❌ not built | — | Intentionally: no sequence enrolment without the ownership/suppression/approval controls being signed off. |
| Approved proof / collateral | in | ✅ `proof_assets` governance | — | Marketing must load approved assets; none exist yet. |

No synthetic or fixture response is presented as intelligence anywhere in the UI or API: demo data is labelled SYNTHETIC and seeded only with `RI_ENV=development`.
