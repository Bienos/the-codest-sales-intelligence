-- Deal history for the funnel and the revenue forecast (docs/sales-metrics-and-forecast.md).
-- Until now the Pipedrive mirror kept only each deal's CURRENT stage, so conversion rates and time in stage could not be measured.
-- This adds: the deal dates Pipedrive already sends, the pipeline's stages in order, an append-only log of every stage and status
-- change (from webhooks, the hourly sync and a one-off backfill of Pipedrive's per-deal history), a daily copy of the open pipeline,
-- the forecast settings, and a log of past forecasts so each quarter's forecast can be scored against what actually closed.
-- Additive and safe to re-run.

ALTER TABLE crm_deals
  ADD COLUMN IF NOT EXISTS add_time timestamptz,
  ADD COLUMN IF NOT EXISTS stage_change_time timestamptz,
  ADD COLUMN IF NOT EXISTS won_time timestamptz,
  ADD COLUMN IF NOT EXISTS lost_time timestamptz,
  ADD COLUMN IF NOT EXISTS lost_reason text,
  ADD COLUMN IF NOT EXISTS expected_close_date date,
  ADD COLUMN IF NOT EXISTS origin text,                       -- Pipedrive's "source origin" when it sends one (API, Web forms, ...)
  ADD COLUMN IF NOT EXISTS channel text,                      -- Pipedrive's "source channel" when it sends one
  ADD COLUMN IF NOT EXISTS history_backfilled_at timestamptz; -- set once the deal's Pipedrive history has been read

CREATE TABLE IF NOT EXISTS crm_stages (
  crm_id bigint PRIMARY KEY,
  pipeline_id bigint,
  name text NOT NULL,
  order_nr integer,
  deal_probability integer,
  is_deleted boolean NOT NULL DEFAULT false,
  synced_at timestamptz NOT NULL DEFAULT now()
);

-- One row per change: "deal X was in stage S with status T from time `at`". Never updated or deleted.
CREATE TABLE IF NOT EXISTS deal_stage_transitions (
  id bigserial PRIMARY KEY,
  crm_deal_id bigint NOT NULL,
  pipeline_id bigint,
  from_stage_id bigint,
  to_stage_id bigint NOT NULL,
  from_status text,
  to_status text NOT NULL CHECK (to_status IN ('open', 'won', 'lost')),
  at timestamptz NOT NULL,
  source text NOT NULL CHECK (source IN ('created', 'first_seen', 'sync', 'webhook', 'backfill')),
  -- created: the deal never changed stage, so it entered its stage when it was added (exact)
  -- first_seen: the mirror met the deal already in this stage; the time is Pipedrive's last stage change (occupancy, not an entry we saw)
  value numeric,
  currency text,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (crm_deal_id, at, to_stage_id, to_status, source)
);
CREATE INDEX IF NOT EXISTS deal_stage_transitions_deal_idx ON deal_stage_transitions (crm_deal_id, at);
DROP TRIGGER IF EXISTS deal_stage_transitions_append_only ON deal_stage_transitions;
CREATE TRIGGER deal_stage_transitions_append_only BEFORE UPDATE OR DELETE ON deal_stage_transitions FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE IF NOT EXISTS deal_snapshots_daily (
  day date NOT NULL,
  crm_deal_id bigint NOT NULL,
  pipeline_id bigint,
  stage_id bigint,
  status text,
  value numeric,
  currency text,
  expected_close_date date,
  owner_id bigint,
  PRIMARY KEY (day, crm_deal_id)
);

CREATE TABLE IF NOT EXISTS forecast_settings (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS forecast_log (
  id bigserial PRIMARY KEY,
  made_at timestamptz NOT NULL DEFAULT now(),
  made_on date NOT NULL DEFAULT current_date,
  year integer NOT NULL,
  trigger text NOT NULL CHECK (trigger IN ('daily', 'settings_saved')),
  inputs jsonb NOT NULL,
  result jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS forecast_log_daily_idx ON forecast_log (made_on, year) WHERE trigger = 'daily';

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['crm_stages', 'deal_stage_transitions', 'deal_snapshots_daily', 'forecast_settings', 'forecast_log'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
