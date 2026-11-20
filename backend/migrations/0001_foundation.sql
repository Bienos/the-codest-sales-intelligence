-- R0 Foundation: identity, lineage, staging, users, audit, job queue.
-- Spec refs: 04 §3-§6, 11 §3/§5, 13 §4, 03 §2 (immutable 64-rule baseline).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- users & access
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  display_name text NOT NULL,
  role text NOT NULL CHECK (role IN ('bdm','sales_manager','research_analyst','marketing','delivery_expert','admin','compliance')),
  team text NOT NULL DEFAULT 'default',
  pipedrive_user_id bigint UNIQUE,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- audit (append-only)
CREATE TABLE audit_events (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  operation text NOT NULL,
  record_type text NOT NULL,
  record_id text,
  before_hash text,
  after_hash text,
  approval_id text,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_events_record_idx ON audit_events (record_type, record_id);
CREATE INDEX audit_events_at_idx ON audit_events (at);
CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% on % is forbidden (append-only / immutable)', TG_OP, TG_TABLE_NAME; END $$;
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- ---------------------------------------------------------------- canonical organisations
CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name text NOT NULL,
  legal_name text,
  canonical_domain text,
  country_code char(2),
  entity_type text NOT NULL DEFAULT 'company' CHECK (entity_type IN ('company','subsidiary','group','vendor','si','public_body','other')),
  segment text,
  verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','domain_verified','registry_verified','manual_verified')),
  employees_estimate integer,
  employees_basis text,            -- official_report | provider_estimate | sheet_recorded
  employees_as_of date,
  employees_uncertain boolean NOT NULL DEFAULT false,
  parent_id uuid REFERENCES organizations(id),
  in_master_list boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX organizations_domain_idx ON organizations (canonical_domain);   -- NOT unique: groups share domains
CREATE INDEX organizations_name_trgm ON organizations USING gin (display_name gin_trgm_ops);

CREATE TABLE organization_identifiers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  scheme text NOT NULL,                       -- LEI | COMPANIES_HOUSE | KRS | HRB | CVR | ...
  jurisdiction text NOT NULL DEFAULT '',
  identifier text NOT NULL,
  evidence_claim_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (scheme, jurisdiction, identifier)
);
CREATE INDEX org_ident_org_idx ON organization_identifiers (organization_id);

CREATE TABLE organization_aliases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  alias text NOT NULL,
  alias_norm text NOT NULL,
  locale text,
  source_ref text,
  UNIQUE (organization_id, alias_norm)
);
CREATE INDEX org_alias_norm_idx ON organization_aliases (alias_norm);

-- parent/subsidiary & other relationships are EDGES, never merges (spec 04 §5)
CREATE TABLE organization_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_org uuid NOT NULL REFERENCES organizations(id),
  to_org uuid NOT NULL REFERENCES organizations(id),
  relation_type text NOT NULL CHECK (relation_type IN ('parent_of','subsidiary_of','same_group','vendor_of','si_of','former_name_of')),
  scope text,
  valid_from date,
  valid_to date,
  claim_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (from_org <> to_org)
);

-- ---------------------------------------------------------------- Google Sheets import lineage
CREATE TABLE import_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workbook_key text NOT NULL,            -- 'A' | 'B'
  workbook_id text NOT NULL,
  status text NOT NULL CHECK (status IN ('running','completed','paused_schema_drift','failed','blocked')),
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  stats jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text
);

CREATE TABLE sheet_schema_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_run_id uuid NOT NULL REFERENCES import_runs(id),
  workbook_id text NOT NULL,
  sheet_id text NOT NULL,
  sheet_title text NOT NULL,
  headers jsonb NOT NULL,
  header_hash text NOT NULL,
  row_count integer NOT NULL,
  status text NOT NULL CHECK (status IN ('ok','drift','missing','unknown_tab')),
  drift_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  observed_at timestamptz NOT NULL DEFAULT now()
);

-- immutable raw staging: original values preserved exactly (spec 04 §6)
CREATE TABLE staging_sheet_rows (
  id bigserial PRIMARY KEY,
  import_run_id uuid NOT NULL REFERENCES import_runs(id),
  workbook_id text NOT NULL,
  sheet_id text NOT NULL,
  sheet_title text NOT NULL,
  row_number integer NOT NULL,
  row_identity text NOT NULL,
  row_hash text NOT NULL,
  raw_values jsonb NOT NULL,
  links jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {header: url} for hyperlink cells (text export loses targets)
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (import_run_id, sheet_id, row_number)
);
CREATE INDEX staging_identity_idx ON staging_sheet_rows (workbook_id, sheet_id, row_identity);
CREATE TRIGGER staging_immutable BEFORE UPDATE ON staging_sheet_rows
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE account_source_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid REFERENCES organizations(id),
  source_system text NOT NULL,
  source_file_id text NOT NULL,
  sheet_id text NOT NULL,
  sheet_title text NOT NULL,
  row_identity text NOT NULL,
  row_number integer NOT NULL,           -- last observed position (may move; identity is authoritative)
  imported_hash text NOT NULL,
  raw_company_name text NOT NULL,
  observed_at timestamptz NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, source_file_id, sheet_id, row_identity)
);
CREATE INDEX asl_org_idx ON account_source_links (organization_id);

-- preserved historical research per account, original values untouched (spec 04 §6)
CREATE TABLE legacy_account_research (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid REFERENCES organizations(id),
  source_link_id uuid NOT NULL REFERENCES account_source_links(id),
  kind text NOT NULL,                    -- master_account | buying_signal_log | outreach_account | priority_outreach | ...
  original jsonb NOT NULL,
  observed_at timestamptz NOT NULL,
  UNIQUE (source_link_id, kind)
);

-- identity review queue (spec 04 §5 phase D)
CREATE TABLE identity_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_link_id uuid REFERENCES account_source_links(id),
  raw_company_name text NOT NULL,
  candidate_org_ids uuid[] NOT NULL DEFAULT '{}',
  reason text NOT NULL,                  -- ambiguous_match | contradictory_ids | parent_subsidiary | low_confidence
  features jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved_link','resolved_new','resolved_reject')),
  resolved_org_id uuid REFERENCES organizations(id),
  reviewer text,
  reviewer_note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
CREATE UNIQUE INDEX identity_reviews_open_uniq ON identity_reviews (source_link_id) WHERE status = 'open';

CREATE TABLE data_conflicts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  field text NOT NULL,
  existing_value text,
  incoming_value text,
  source_link_id uuid REFERENCES account_source_links(id),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted_existing','accepted_incoming','dismissed')),
  detected_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, field, incoming_value)
);

-- ---------------------------------------------------------------- people (contacts, minimal PII)
CREATE TABLE people (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name text NOT NULL,
  data_class text NOT NULL DEFAULT 'personal',
  suppressed boolean NOT NULL DEFAULT false,
  suppression_reason text,
  work_email text,
  email_verification_status text NOT NULL DEFAULT 'unknown',   -- preserved from sheets: 'Found — Unverified', 'Not Found', ...
  linkedin_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  erased_at timestamptz
);
CREATE TABLE employment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id uuid NOT NULL REFERENCES people(id),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  title text,
  buyer_lane text,
  valid_from date,
  valid_to date,
  verification_status text NOT NULL DEFAULT 'unverified',
  evidence_url text,
  source_link_id uuid REFERENCES account_source_links(id),
  observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX employment_org_idx ON employment (organization_id);
CREATE INDEX employment_person_idx ON employment (person_id);

-- ---------------------------------------------------------------- 64-rule baseline (immutable)
CREATE TABLE legacy_signals (
  id text PRIMARY KEY,                   -- original ID, e.g. REL-03
  ordinal integer NOT NULL UNIQUE,
  grp text NOT NULL,
  signal text NOT NULL,
  strength text NOT NULL,                -- stored verbatim, '' allowed for NEG
  fresh_for_days text NOT NULL,
  window_note text NOT NULL,
  how_to_spot text NOT NULL,
  who_to_contact text NOT NULL,
  check_it_is_real text NOT NULL,
  pairs_best_with text NOT NULL,
  stop_effect text NOT NULL,
  source_sha256 text NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER legacy_signals_immutable BEFORE UPDATE OR DELETE ON legacy_signals
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- ---------------------------------------------------------------- durable job queue (spec 13 §4)
CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload_hash text NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN
    ('scheduled','claimed','fetching','parsing','verifying','committing','completed',
     'retryable_failed','permanent_failed','requires_approval','quarantined','cancelled')),
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 5,
  run_after timestamptz NOT NULL DEFAULT now(),
  deadline_at timestamptz,
  claimed_by text,
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  correlation_id text,
  connector_version text,
  provider_budget numeric,
  last_error text,
  last_error_class text,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim_idx ON jobs (status, run_after);
CREATE TABLE dead_letters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid REFERENCES jobs(id),
  kind text NOT NULL,
  payload jsonb NOT NULL,
  error_class text NOT NULL,
  error text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  replayed_at timestamptz
);
