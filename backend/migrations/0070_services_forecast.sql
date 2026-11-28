-- IT-services forecast: the deal and organisation fields the app sets up in Pipedrive (deal type, billing model, forecast category,
-- planned start, duration, people, monthly value, four qualification checks, skills; client status, contract end, people at the client),
-- the deal products' billing schedule, Pipedrive users' names for the data-quality view, and pipeline names. Additive and safe to re-run.

-- Which Pipedrive field plays which role. The app creates the fields once (or finds ones with the same name) and records their keys here.
CREATE TABLE IF NOT EXISTS crm_field_map (
  role text PRIMARY KEY,
  entity text NOT NULL CHECK (entity IN ('deal', 'organization')),
  field_key text NOT NULL,
  name text NOT NULL,
  field_type text NOT NULL,
  created_by_app boolean NOT NULL DEFAULT false,
  synced_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS deal_type_raw text;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS billing_model_raw text;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS forecast_category_raw text;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS start_date date;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS duration_months numeric;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS fte numeric;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS monthly_value numeric;
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS skills text[];
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS qualification jsonb;        -- {pain, champion, decision_maker, decision_process}: the seller's evidence
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS products jsonb;             -- summary of the deal's products: {monthly, total, start, months, fte, n}

ALTER TABLE crm_organizations ADD COLUMN IF NOT EXISTS client_status_raw text;
ALTER TABLE crm_organizations ADD COLUMN IF NOT EXISTS contract_end date;
ALTER TABLE crm_organizations ADD COLUMN IF NOT EXISTS fte_now numeric;

CREATE TABLE IF NOT EXISTS crm_users (
  crm_id bigint PRIMARY KEY,
  name text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_pipelines (
  crm_id bigint PRIMARY KEY,
  name text NOT NULL,
  synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS crm_deals_pipeline_status ON crm_deals (pipeline_id, status);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['crm_field_map', 'crm_users', 'crm_pipelines'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
