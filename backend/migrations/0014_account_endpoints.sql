-- Where each account publishes: news feeds, press and careers pages, job boards. Found by the account scan (button), re-used on later scans.
CREATE TABLE account_endpoints (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('feed','newsroom','careers','job_board')),
  url text NOT NULL,
  provider text,
  board text,
  found_at timestamptz NOT NULL DEFAULT now(),
  last_scanned_at timestamptz,
  last_status text,
  last_detail text,
  docs_seen integer NOT NULL DEFAULT 0,
  UNIQUE (organization_id, url)
);
CREATE INDEX account_endpoints_org_idx ON account_endpoints (organization_id);
ALTER TABLE account_endpoints ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.account_endpoints FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.account_endpoints FROM authenticated; END IF;
END $$;
