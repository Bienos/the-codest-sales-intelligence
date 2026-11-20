# 04 — Canonical Data Model, Identity & Provenance
**ID:** RI-DATA-04 · **Version:** 1.0 · **Status:** Proposed schema contract · **Depends on:** 01 Product, 02 Sources, 03 Signals, 11 Governance, 13 Operations

## 1. Persistence decisions
Use PostgreSQL for canonical transactional data, versioned relationships and audit. Use object storage **only for content whose licence and privacy classification allow storage**. PostgreSQL full-text and, if justified, vector search support retrieval. Entity and initiative graph relationships use relational edge tables with provenance; a graph database is an optional future optimisation, not a prerequisite.

## 2. Domain boundaries
**Organisation identity** is not a buying committee, **event** is not a signal, **signal** is not a qualified sales lead, **initiative** is not necessarily funded, **supplier relationship** does not prove exclusivity, **research candidate** is not a Pipedrive deal, and **source text** does not grant permission for external use.

## 3. Canonical entities (minimum schema)
| Table | Fields (in addition to UUID PK and timestamps) | Uniqueness and ownership |
|---|---|---|
| `organizations` | legal_name, display_name, domain, country, type, verification_state, parent_id? | Verified legal ID unique per jurisdiction; domain not unique across subsidiary group |
| `organization_identifiers` | org_id, id_scheme, value, jurisdiction, source_claim_id | Unique `(id_scheme, jurisdiction, value)` when validated |
| `organization_aliases` | org_id, alias, locale, source_ref | Non-unique aliases; collision review |
| `organization_edges` | from_org, to_org, relation_type, scope, valid_from/to, claim_id | Versioned; never merge entities based solely on graph relation |
| `people` | name, data_class, suppression_flags | Person is not email address; retention policy |
| `employment` | person_id, org_id, title, valid_from/to, evidence_claim | Historical employment edge |
| `account_source_links` | org_id, source_system, workbook_id, sheet_id, row_id, source_hash, imported_at | Unique per source row; preserve original |
| `crm_links` | org_id/person_id, crm_type, crm_id, owner_id, last_synced | Unique external CRM entity IDs |
| `sources` | kind, tier, access, licence, health, cost | Stable source ID |
| `documents` | source_id, external_id, canonical_url, hash, published_at, fetched_at, blob_ref?, origin_id | Unique `(source_id, external_id, version)` |
| `claims` | subject, predicate, object_json, evidence_anchor, source_doc_id, confidence, status | Distinguish direct quote vs inferred relation |
| `events` | type, subject_org_id, event_at?, pub_at, verified_at, status | Source groups and idempotent event key |
| `event_claims` | event_id, claim_id, role | Many-to-many |
| `signals` | event_id, rule_id, rule_version, strength, demand_tier, status | Unique event/rule/version within scope |
| `initiatives` | owner_org_id, named_title?, working_title?, stage, scope, status | Company can have multiple initiatives |
| `initiative_events` | initiative_id, event_id, link_strength, claim_id | Evidence-backed link |
| `workstreams` | initiative_id, description, technology, stage, owner_org_id? | Separate technical scope |
| `supplier_edges` | supplier_org_id, buyer_org_id, initiative_id?, workstream_id?, relation, claim_id, valid_from/to | Relationship scoped and dated |
| `procurement_routes` | org_id, workstream_id?, route_type, restriction, status, evidence | Not assumed open |
| `opportunity_hypotheses` | initiative_id, workstream_id?, route_type, assessed_state, score_json, counterevidence_json | Not CRM deal |
| `opportunity_events` | hypothesis_id, prior_state, new_state, actor, evidence_ref | Event-sourced transition history |
| `action_recommendations` | hypothesis_id, owner, action, objective, due/trigger, rationale, status | One active principal action per recommendation context |
| `research_tasks` | target_id, research_question, provider, cost_budget, status, stop_reason | Idempotent task key |
| `runs` | job_id, connector, start/end, status, usage, errors | Retry and health history |
| `feedback` | target_type/id, reviewer, judgement, reason, timestamp | Ground truth for learning |
| `audit_events` | actor, operation, record, before/after hashes, approval, timestamp | Append-only operational log |

## 4. Representative SQL DDL
```sql
CREATE TABLE organizations (
  id uuid PRIMARY KEY,
  display_name text NOT NULL,
  legal_name text,
  canonical_domain text,
  country_code char(2),
  entity_type text NOT NULL DEFAULT 'company',
  verification_status text NOT NULL DEFAULT 'unverified',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE organization_identifiers (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organizations(id),
  scheme text NOT NULL,
  jurisdiction text NOT NULL DEFAULT '',
  identifier text NOT NULL,
  evidence_claim_id uuid,
  UNIQUE (scheme, jurisdiction, identifier)
);
CREATE TABLE account_source_links (
  id uuid PRIMARY KEY,
  organization_id uuid REFERENCES organizations(id),
  source_system text NOT NULL,
  source_file_id text NOT NULL,
  sheet_id text NOT NULL,
  row_identity text NOT NULL,
  imported_hash text NOT NULL,
  observed_at timestamptz NOT NULL,
  UNIQUE (source_system, source_file_id, sheet_id, row_identity)
);
CREATE TABLE source_documents (
  id uuid PRIMARY KEY,
  source_id text NOT NULL,
  external_id text NOT NULL,
  version_key text NOT NULL,
  canonical_url text,
  published_at timestamptz,
  fetched_at timestamptz NOT NULL,
  content_hash text,
  permitted_blob_ref text,
  UNIQUE (source_id, external_id, version_key)
);
```
Use migration tooling; the schema above is illustrative subset, **not complete deployable migration**. Full migration includes FKs, indexes, scopes, RLS/RBAC integration, audit, partitions/retention as necessary.

## 5. Identity resolution algorithm
**Phase A deterministic:** exact validated LEI/registry IDs; unique CRM link; verified official domain and known aliases; corporate group edge. **Phase B candidate generation:** normalised name + country + industry + website; preserve non-Latin and local suffix data. **Phase C probabilistic:** fuzzy string and semantic evidence may rank candidates, but cannot auto-merge risky bank/legal entities. **Phase D policy:** `auto_link`, `review`, `reject`; record feature scores, decision provenance and reviewer. Conflicts: contradictory legal IDs never auto-link; parent and subsidiary relationship is an edge, not a merge.

## 6. Google Sheets snapshot / sync
Read metadata first; record exact tabs, frozen/header rows, values, source record references and checksum; map to immutable staging, then reconcile with canonical records. Source A workbooks and tabs are listed in Master §7. Source B also has `Contacts — Compact`, `Priority Outreach`. Keep original contact verification labels such as `Found — Unverified` and source evidence unchanged. Read-only by default; schema drift -> paused import with alert, not best-effort column guessing. Rows without stable IDs require configurable composite identity + content hash and move-detection review.

## 7. Source precedence
| Domain | Preferred authority | Conflict handling |
|---|---|---|
| CRM ownership/deal status | Pipedrive | Never overwrite from Google Sheets |
| Legal identity | Official register/LEI | Compare recorded jurisdiction and valid dates |
| Current buyer role | Current authoritative company page / authorised source | Keep previous role with end date |
| Open tender status | Official notice amendment | Supersede archived or aggregator copies |
| Technical stack | Directly attributable public engineering evidence | Mark inference separately |
| Client proof permission | Internal approved collateral/access policy | Block external release absent approval |

## 8. Temporal and claims model
Facts need `valid_from`, `valid_to` when meaningfully known and `observed_at`; changes create a new assertion/version. `claim_status = supported | contested | superseded | unsupported | unverified`. A source citation can support a claim only when its evidence span actually states the proposition. Corporate group relationships and procurement route visibility are scoped to legal entity/jurisdiction where applicable.

## 9. Indexing, retention and backup
Index organisation identifiers, source row links, event subject/date, claim evidence, initiative status, active procurement deadlines and research tasks status. Partition raw document metadata/event tables if scale warrants. Configure sensitive contact retention and deletion propagation; backups encrypted, restore drills under 13. Do not retain full licensed source documents without explicit permission.

## 10. Acceptance tests
- Source A company and Source B variant reconcile to one canonical identity, both lineage links retained.
- Parent/regulated subsidiary are linked but not merged or assigned identical procurement route.
- Contradictory legal IDs block auto-link.
- Rename and contact role change preserve history.
- Supplier award valid for workstream A does not infer exclusive contract for B.
- Duplicate import and failed resume do not duplicate records.
- CRM ownership remains unchanged after nightly import.
- Deleting/suppressing sensitive contact data propagates to derived indexes per policy.
