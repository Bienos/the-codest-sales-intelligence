-- Past IT contract awards from official award notices (TED), with the supplier who won and when the contract probably ends.
-- Used by the renewal radar: contracts that end soon are often re-tendered, and the current supplier is known.
CREATE TABLE awarded_contracts (
  notice_id text PRIMARY KEY,
  buyer_name text,
  buyer_country text,
  buyer_org_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  title text,
  cpv text[] NOT NULL DEFAULT '{}',
  winners text[] NOT NULL DEFAULT '{}',
  winner_country text,
  winner_size text,
  awarded_on date,
  duration_months integer,
  expected_end date,
  end_basis text NOT NULL CHECK (end_basis IN ('stated','assumed')),
  relevant boolean NOT NULL DEFAULT false,
  official_url text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX awarded_contracts_end_idx ON awarded_contracts (expected_end) WHERE relevant;
CREATE INDEX awarded_contracts_country_idx ON awarded_contracts (buyer_country) WHERE relevant;
CREATE INDEX awarded_contracts_buyer_org_idx ON awarded_contracts (buyer_org_id);
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['awarded_contracts'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
