-- Vendor intelligence: who an account buys technology and engineering from, and what is happening with those relationships.
-- One row per run (same shape as account_profiles). `content` holds suppliers (each with its quoted evidence), happenings, angles and the
-- technology named in job ads; `sources` lists every page Exa returned. Nothing in `content` is kept unless a returned page supports it.
CREATE TABLE account_vendor_intel (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  content jsonb,
  sources jsonb NOT NULL DEFAULT '[]'::jsonb,
  searches integer NOT NULL DEFAULT 0,
  exa_cost_usd numeric NOT NULL DEFAULT 0,
  model text,
  tokens_in integer,
  tokens_out integer,
  dropped integer NOT NULL DEFAULT 0,
  pages_queued integer NOT NULL DEFAULT 0,       -- pages sent on to the normal page check, so they can become findings
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX account_vendor_intel_org_idx ON account_vendor_intel (organization_id, created_at DESC);

-- Same baseline as every other table: no access through the public REST roles.
DO $$
BEGIN
  ALTER TABLE public.account_vendor_intel ENABLE ROW LEVEL SECURITY;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.account_vendor_intel FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.account_vendor_intel FROM authenticated; END IF;
END $$;
