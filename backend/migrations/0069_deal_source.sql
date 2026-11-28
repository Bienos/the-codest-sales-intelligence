-- Where each deal came from, for the forecast's Sources view. The value is Pipedrive's own (an option id of the deal field named in
-- RI_PIPEDRIVE_SOURCE_FIELD, e.g. "Lead source"); crm_field_options maps option ids to their labels and is refreshed by the hourly sync.
-- Additive and safe to re-run.
ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS lead_source_raw text;

CREATE TABLE IF NOT EXISTS crm_field_options (
  field_key text NOT NULL,
  option_id text NOT NULL,
  label text NOT NULL,
  synced_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (field_key, option_id)
);

DO $$
BEGIN
  EXECUTE 'ALTER TABLE public.crm_field_options ENABLE ROW LEVEL SECURITY';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE 'REVOKE ALL ON public.crm_field_options FROM anon'; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE 'REVOKE ALL ON public.crm_field_options FROM authenticated'; END IF;
END $$;
