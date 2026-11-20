-- R1 Evidence radar: source registry/health, documents, claims, events, signals, negatives.
-- Spec refs: 02 §3-§8, 03 §3-§10, 04 §3, 13 §6/§8.

CREATE TABLE sources (
  source_id text PRIMARY KEY,
  display_name text NOT NULL,
  canonical_domain text,
  source_tier text NOT NULL CHECK (source_tier IN ('S1','S2','S3','S4','S5','internal')),
  source_type text NOT NULL,                 -- procurement | jobs | company_site | news | regulator | registry | vendor | discovery | crm | sheets
  source_family text NOT NULL,               -- coverage family: procurement | jobs | company_ir | vendor | regulatory | registry | attention | discovery
  provider text NOT NULL,
  countries text[] NOT NULL DEFAULT '{}',
  languages text[] NOT NULL DEFAULT '{}',
  signal_rule_ids text[] NOT NULL DEFAULT '{}',
  collection_method text NOT NULL,           -- official_api | feed | change_monitor | search | internal
  endpoint_template text,
  auth_mode text NOT NULL DEFAULT 'none',
  credential_ref text,
  licence_notes text,
  robots_policy text,
  retention_policy text NOT NULL DEFAULT 'metadata_only' CHECK (retention_policy IN ('metadata_only','excerpt','full_text')),
  poll_interval_s integer NOT NULL DEFAULT 86400,
  cursor_type text NOT NULL DEFAULT 'none',
  rate_limit_per_min integer,
  max_document_bytes integer NOT NULL DEFAULT 2000000,
  status text NOT NULL DEFAULT 'pending_connector_validation' CHECK (status IN
     ('pending_connector_validation','active','paused','degraded','failed','blocked','unconfigured')),
  owner text,
  reviewed_at timestamptz,
  cost_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  certification jsonb,                       -- connector certification record (spec 09 §12); required before activation
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_cursors (
  source_id text NOT NULL REFERENCES sources(source_id),
  cursor_key text NOT NULL DEFAULT 'default',
  cursor_value text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, cursor_key)
);

CREATE TABLE monitoring_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES sources(source_id),
  job_id uuid,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','partial','failed','skipped')),
  items_new integer NOT NULL DEFAULT 0,
  items_changed integer NOT NULL DEFAULT 0,
  items_unchanged integer NOT NULL DEFAULT 0,
  items_unavailable integer NOT NULL DEFAULT 0,
  error_class text,
  error text,
  usage jsonb NOT NULL DEFAULT '{}'::jsonb,
  cursor_before text,
  cursor_after text
);
CREATE INDEX monitoring_runs_source_idx ON monitoring_runs (source_id, started_at DESC);

CREATE TABLE source_health (
  source_id text PRIMARY KEY REFERENCES sources(source_id),
  state text NOT NULL DEFAULT 'unconfigured' CHECK (state IN ('healthy','degraded','failed','paused','unconfigured','blocked')),
  last_success_at timestamptz,
  last_attempt_at timestamptz,
  consecutive_failures integer NOT NULL DEFAULT 0,
  missed_due_runs integer NOT NULL DEFAULT 0,
  error_class text,
  detail text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE account_source_coverage (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  source_family text NOT NULL,
  coverage_state text NOT NULL CHECK (coverage_state IN
     ('monitored_healthy','monitored_degraded','source_not_found','access_restricted','not_applicable','not_configured')),
  source_ids text[] NOT NULL DEFAULT '{}',
  last_success_at timestamptz,
  last_material_change_at timestamptz,
  note text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, source_family)
);

-- ---------------------------------------------------------------- documents & normalised source records
CREATE TABLE source_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES sources(source_id),
  external_id text NOT NULL,
  version_key text NOT NULL,
  canonical_url text,
  title text,
  language text,
  published_at timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  content_hash text,
  change_status text NOT NULL DEFAULT 'new' CHECK (change_status IN ('new','changed','unchanged','unavailable')),
  excerpt text,                               -- limited permitted extract only
  content_text text,                          -- transient: purged after extraction unless source licence allows retention
  content_purged_at timestamptz,
  origin_document_id uuid REFERENCES source_documents(id),    -- syndication: points at the originating document
  independent_origin_group text,
  raw_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (source_id, external_id, version_key)
);
CREATE INDEX source_documents_ext_idx ON source_documents (source_id, external_id, fetched_at DESC);
CREATE INDEX source_documents_group_idx ON source_documents (independent_origin_group);

CREATE TABLE procurement_notices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES sources(source_id),
  notice_id text NOT NULL,                    -- official identifier
  buyer_name text,
  buyer_org_id uuid REFERENCES organizations(id),
  title text,
  scope text,
  procedure text,
  service_codes text[] NOT NULL DEFAULT '{}',
  country text,
  published_at timestamptz,
  amended_at timestamptz,
  deadline_at timestamptz,
  deadline_tz text,
  status text NOT NULL CHECK (status IN ('planning','open','amended','awarded','expired','cancelled','unknown')),
  official_url text,
  value_if_reported numeric,
  lots jsonb NOT NULL DEFAULT '[]'::jsonb,
  eligibility text,
  last_status_check_at timestamptz,
  document_id uuid REFERENCES source_documents(id),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, notice_id)
);

CREATE TABLE job_postings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,                     -- lever | greenhouse | ashby | ...
  board text NOT NULL,                        -- employer board slug
  job_id text NOT NULL,
  organization_id uuid REFERENCES organizations(id),
  employer_name text,
  title text NOT NULL,
  team text,
  location text,
  employment_type text NOT NULL DEFAULT 'unknown' CHECK (employment_type IN ('permanent','contract','interim','freelance','internship','unknown')),
  posted_at timestamptz,
  updated_at_source timestamptz,
  open_status text NOT NULL DEFAULT 'open' CHECK (open_status IN ('open','closed','unknown')),
  closed_at timestamptz,
  url text,
  tech_keywords text[] NOT NULL DEFAULT '{}',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, board, job_id)
);

-- ---------------------------------------------------------------- claims / events / signals
CREATE TABLE claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject text NOT NULL,
  predicate text NOT NULL,
  object_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_anchor text,                       -- verbatim excerpt that states the proposition
  anchor_verified boolean NOT NULL DEFAULT false,
  claim_kind text NOT NULL DEFAULT 'quote' CHECK (claim_kind IN ('quote','structured','inferred')),
  source_document_id uuid REFERENCES source_documents(id),
  status text NOT NULL DEFAULT 'unverified' CHECK (status IN ('supported','contested','superseded','unsupported','unverified')),
  observed_at timestamptz NOT NULL DEFAULT now(),
  valid_from timestamptz,
  valid_to timestamptz,
  extraction_model_version text,
  CHECK (claim_kind <> 'quote' OR evidence_anchor IS NOT NULL)
);
CREATE INDEX claims_doc_idx ON claims (source_document_id);

CREATE TABLE events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_key text NOT NULL UNIQUE,             -- idempotent event identity
  event_type text NOT NULL CHECK (event_type IN (
    'procurement_published','procurement_amended','procurement_awarded','vendor_selected','implementation_launched',
    'programme_announced','programme_completed','integration_release','product_launch','engineering_job_opened',
    'contractor_job_opened','job_closed','technology_leader_appointed','acquisition_announced','funding_announced',
    'supplier_panel_opened','supplier_panel_closed','regulatory_change','deadline_approaching','crm_buyer_response',
    'relationship_changed','negative_cancellation','programme_delayed','supplier_exit')),
  subject_org_id uuid REFERENCES organizations(id),
  subject_name_raw text,
  other_entity_roles jsonb NOT NULL DEFAULT '[]'::jsonb,      -- [{name, org_id?, role}]
  event_at timestamptz,                       -- NULL = unknown; never silently replaced by pub date
  event_at_known boolean NOT NULL DEFAULT false,
  pub_at timestamptz,
  first_observed_at timestamptz NOT NULL DEFAULT now(),
  last_checked_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','verified','failed','stale')),
  verification_notes jsonb NOT NULL DEFAULT '[]'::jsonb,
  lifecycle_status text NOT NULL DEFAULT 'active' CHECK (lifecycle_status IN ('active','superseded','expired')),
  supersedes_event_id uuid REFERENCES events(id),
  origin_document_id uuid REFERENCES source_documents(id),
  independent_origin_group text,
  summary text NOT NULL,
  attrs jsonb NOT NULL DEFAULT '{}'::jsonb,
  extraction_model_version text NOT NULL,
  is_historical_replay boolean NOT NULL DEFAULT false      -- reprocessed history is never presented as new
);
CREATE INDEX events_org_idx ON events (subject_org_id, event_type);
CREATE INDEX events_origin_idx ON events (independent_origin_group);

CREATE TABLE event_claims (
  event_id uuid NOT NULL REFERENCES events(id),
  claim_id uuid NOT NULL REFERENCES claims(id),
  role text NOT NULL DEFAULT 'supporting',
  PRIMARY KEY (event_id, claim_id)
);
CREATE TABLE event_documents (                -- every mirrored copy stays linked, only the origin counts as independent
  event_id uuid NOT NULL REFERENCES events(id),
  document_id uuid NOT NULL REFERENCES source_documents(id),
  PRIMARY KEY (event_id, document_id)
);

CREATE TABLE signal_rule_versions (
  rule_id text NOT NULL REFERENCES legacy_signals(id),
  version text NOT NULL,
  status text NOT NULL CHECK (status IN ('defined_not_activated','candidate_until_evaluated','active','retired')),
  definition jsonb NOT NULL,
  changelog text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id),
  rule_id text NOT NULL,
  rule_version text NOT NULL,
  organization_id uuid REFERENCES organizations(id),
  initiative_id uuid,
  strength integer NOT NULL,
  demand_tier text NOT NULL CHECK (demand_tier IN ('D1','D2','D3','D4')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','superseded','blocked')),
  verification_status text NOT NULL,
  effective_from timestamptz NOT NULL,
  expires_at timestamptz,
  negative_rule_ids text[] NOT NULL DEFAULT '{}',
  evidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  independent_origin_groups text[] NOT NULL DEFAULT '{}',
  reason_code text NOT NULL,
  summary_is_inference boolean NOT NULL DEFAULT false,
  commercial_questions jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, rule_id, rule_version)
);
CREATE INDEX signals_org_idx ON signals (organization_id, status);

CREATE TABLE negative_findings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_id text NOT NULL,
  rule_version text NOT NULL,
  scope_type text NOT NULL CHECK (scope_type IN ('account','initiative','workstream','route','contact')),
  scope_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('hard_block','route_block','demand_counterevidence','soft_risk','ownership_gate','expiring_warning')),
  effect text NOT NULL,                       -- stop | downgrade | change_route (original Stop effect)
  event_id uuid REFERENCES events(id),
  starts_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','cleared')),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (rule_id, scope_type, scope_id, event_id)
);
CREATE INDEX negative_scope_idx ON negative_findings (scope_type, scope_id, status);

-- human adjudication (spec 03 §13) and general feedback ground truth
CREATE TABLE feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_type text NOT NULL,
  target_id text NOT NULL,
  reviewer text NOT NULL,
  judgement text NOT NULL,
  reason text,
  note text,
  rule_id text,
  rule_version text,
  model_version text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_target_idx ON feedback (target_type, target_id);

-- ---------------------------------------------------------------- provider budgets (spec 14 §8): reserve BEFORE the call
CREATE TABLE provider_budgets (
  provider text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('daily','monthly','hypothesis','contact_enrichment')),
  period text NOT NULL,                       -- 2026-11 | 2026-11-20 | <hypothesis uuid>
  cap numeric NOT NULL CHECK (cap >= 0),
  reserved numeric NOT NULL DEFAULT 0,
  spent numeric NOT NULL DEFAULT 0,
  unit text NOT NULL DEFAULT 'units',
  PRIMARY KEY (provider, kind, period),
  CHECK (reserved + spent <= cap)
);
CREATE TABLE budget_ledger (
  id bigserial PRIMARY KEY,
  provider text NOT NULL,
  kind text NOT NULL,
  period text NOT NULL,
  entry text NOT NULL CHECK (entry IN ('reserve','settle','release','denied')),
  amount numeric NOT NULL,
  ref_type text,
  ref_id text,
  purpose text,
  idempotency_key text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
