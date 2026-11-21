-- Extra context a person gives about a company (links, notes). Stored for research and for people to read; social pages are never scraped.
CREATE TABLE account_context (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('link','note','region')),
  value text NOT NULL,
  added_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_context_org_idx ON account_context (organization_id);
ALTER TABLE account_context ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.account_context FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.account_context FROM authenticated; END IF;
END $$;
