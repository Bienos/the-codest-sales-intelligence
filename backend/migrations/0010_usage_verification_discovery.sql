-- Provider usage tracker (every paid or metered call), verification requests (sheet signals and discovery hits turned into
-- verified events), and discovery plans (Exa). Deny-all RLS like every other table.
CREATE TABLE provider_usage (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  period text NOT NULL,                       -- YYYY-MM
  provider text NOT NULL,                     -- anthropic | exa
  operation text NOT NULL,                    -- extract | search
  units numeric NOT NULL DEFAULT 0,           -- tokens or searches
  unit text NOT NULL DEFAULT 'units',
  tokens_in integer,
  tokens_out integer,
  cost_usd numeric,                           -- NULL = unknown (never invented)
  cost_basis text NOT NULL DEFAULT 'unmetered' CHECK (cost_basis IN ('reported','estimated','unmetered')),
  source_id text,
  plan_id text,
  query text,
  organization_id uuid,
  hypothesis_id uuid,
  document_id uuid,
  request_id uuid,
  status text NOT NULL DEFAULT 'ok',
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX provider_usage_period_idx ON provider_usage (provider, period, at DESC);
CREATE INDEX provider_usage_source_idx ON provider_usage (source_id, at DESC);

CREATE TABLE verification_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid REFERENCES organizations(id),
  url text NOT NULL,
  origin text NOT NULL CHECK (origin IN ('sheet_signal','exa_discovery','manual')),
  claimed_text text,                          -- what the sheet or search said (unverified)
  claimed_date text,
  plan_id text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','fetching','extracting','verified','no_event','failed','waiting_budget','blocked')),
  job_id uuid,
  document_id uuid,
  events_created integer NOT NULL DEFAULT 0,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (url, origin)
);
CREATE INDEX verification_requests_org_idx ON verification_requests (organization_id, created_at DESC);
CREATE INDEX verification_requests_status_idx ON verification_requests (status, created_at DESC);

CREATE TABLE discovery_plans (
  plan_id text PRIMARY KEY,
  signal_rule_id text NOT NULL,
  theme text NOT NULL,
  geography text NOT NULL,
  language text NOT NULL DEFAULT 'en',
  recency_days integer NOT NULL,
  query text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  last_run_at timestamptz,
  runs integer NOT NULL DEFAULT 0,
  results_total integer NOT NULL DEFAULT 0,
  requests_created integer NOT NULL DEFAULT 0,
  cost_total_usd numeric NOT NULL DEFAULT 0,
  last_error text
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['provider_usage','verification_requests','discovery_plans'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
