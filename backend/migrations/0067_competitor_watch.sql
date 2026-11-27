-- Weekly collection for the competitor tracker (ri/competitor_watch.py): when each check last ran for each firm, and what it saw, so a firm is
-- checked on its own cadence and an unchanged homepage headline is not suggested twice. '_all' holds the checks that cover every firm at once.
CREATE TABLE competitor_watch (
  competitor_key text NOT NULL,
  check_name text NOT NULL CHECK (check_name IN ('homepage','news','jobs','awards')),
  last_run_at timestamptz,
  last_value jsonb,
  last_error text,
  PRIMARY KEY (competitor_key, check_name)
);
-- What a collector found that a confirmation should apply (a new homepage headline updates the profile once a person confirms it).
ALTER TABLE competitor_moves ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX competitor_moves_source_idx ON competitor_moves (competitor_key, source_url);

DO $$
BEGIN
  ALTER TABLE public.competitor_watch ENABLE ROW LEVEL SECURITY;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.competitor_watch FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.competitor_watch FROM authenticated; END IF;
END $$;
