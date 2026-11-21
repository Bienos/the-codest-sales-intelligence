# Integration inventory — status as of 2026-11-21 (build sandbox)

Legend: **Built** = adapter + tests on fixtures · **Live-verified** = exercised against the real service · **Blocked** = cannot run here (reason).

| Integration | Direction | Built | Live-verified | Blocker / what is needed |
|---|---|---|---|---|
| **Supabase Postgres** (`the-codest-sales-intelligence`, eu-central-1, PG 17) | store | ✅ migrations 0001–0008 applied via connector, checksums recorded in `schema_migrations` | ✅ verified: 69 tables, RLS on all, 0 grants to `anon`/`authenticated`, security advisor clean apart from intended deny-all INFO | App host needs the **session-pooler** connection string as a secret. Run `python -m ri.cli seed` once (loads the 64 rules + source registry). Performance advisor: 48 unindexed FKs to index once real data exists. Tested locally on PG 16 only. |
| Google Sheets A/B | in (read-only) | ✅ API client, **.xlsx upload client**, 15 contracts, drift pause, lineage | ✅ **Both real workbooks imported from .xlsx exports**: 18 tabs, no drift, 1,313 organisations, 2,256 contacts, 322 identity reviews, re-import changes nothing | Hourly API sync still needs a read-only service account. Until then: Administration, Imports (runbook `import-workbooks.md`). |
| Pipedrive read sync/webhooks | in | ✅ | ⚠️ Payload shape confirmed by a live read via MCP (v2: `data[]`, `next_cursor`, `owner_id`) | Needs API token + `PIPEDRIVE_DOMAIN`; webhook basic-auth secret; field-key mapping (custom keys are account-specific hashes). Egress blocked. |
| Pipedrive write executor | out | ✅ approval-gated, kill-switch default OFF, fetch-before-retry | ❌ | Separate write token; needs CRM-admin approval of allowed operations (only `create_activity` is implemented). |
| EU TED | in | ✅ (search v3, pagination) | ❌ | Egress blocked; endpoint/field names are from documentation and **unverified**. Qualify per spec 02 §11. |
| UK Find a Tender (OCDS) | in | ✅ | ❌ | Egress blocked; OCDS semantics standard, endpoint params unverified. |
| Lever / Greenhouse / Ashby | in | ✅ | ❌ | Egress blocked; need per-employer board slugs (not in the sheets). |
| GLEIF | in | ✅ lookup/search | ❌ | Egress returned empty response. |
| Companies House, Contracts Finder, regulators | in | registry entries only | ❌ | Not implemented (candidates). |
| Page / RSS monitor | in | ✅ robots-respecting, section-hashed; **admin UI to set URL, test from server, activate** | ❌ | URLs pre-filled for 4 vendors from links in your workbooks (unvalidated); 6 sources still need a URL. Page tests run from the server, which this sandbox cannot reach. |
| Exa | in | ✅ adapter, **24 discovery plans (6 themes x 4 regions) mapped to active rules, daily scheduler, hard monthly cap, per-call cost recorded from Exa's own response** | ❌ not yet run against the live API | Key and $20 cap are set on Render. First live run is the first proof; check Administration, Usage. Hits only ever become verification requests, never events. |
| LLM extraction (Anthropic) | internal | ✅ schema + quote-anchor verification, token budget, **usage rows with input/output tokens per call**, fresh HTTP client per call | ❌ not yet run against the live API | Key and token cap set on Render. Dollar cost shows only if `RI_ANTHROPIC_PRICE_IN_PER_MTOK` and `_OUT_` are set. Prompt/gold-set regression still to run. |
| Apollo | in (credit-consuming) | ✅ people search/enrich, approval + single-use | ❌ | Key, entitlement, **price**, and explicit approvals. Disabled by default. |
| Slack / email | out | ✅ Slack webhook sink, signed links | ❌ | Webhook URL; email sink not built. |
| Lemlist | out | ❌ not built | — | Intentionally: no sequence enrolment without the ownership/suppression/approval controls being signed off. |
| Approved proof / collateral | in | ✅ `proof_assets` governance | — | Marketing must load approved assets; none exist yet. |

No synthetic or fixture response is presented as intelligence anywhere in the UI or API: demo data is labelled SYNTHETIC and seeded only with `RI_ENV=development`.
| Verification of sheet signals | internal | ✅ SSRF-safe fetch (public addresses only, redirect hops re-checked, robots.txt, 2 MB cap, no social sites) -> quote-anchored extraction -> gates | ❌ | Sellers can only verify URLs cited in their own account's sheet rows. Discovery checks leave 30% of the model budget for sellers. PDFs are not read yet. |
