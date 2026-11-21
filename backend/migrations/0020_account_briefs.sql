-- Research briefs: a cited summary of what the platform knows about one company. Every statement carries a verbatim quote from a stored source.
CREATE TABLE account_briefs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  model text,
  content jsonb,
  dropped integer NOT NULL DEFAULT 0,
  error text,
  tokens_in integer, tokens_out integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX account_briefs_org_idx ON account_briefs (organization_id, created_at DESC);
ALTER TABLE account_briefs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.account_briefs FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.account_briefs FROM authenticated; END IF;
END $$;
