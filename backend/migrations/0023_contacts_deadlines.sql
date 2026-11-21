-- Decision-maker candidates found through a provider (no contact data yet) and the regulatory deadline calendar (a person supplies and approves every date).
CREATE TABLE contact_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  provider text NOT NULL,
  external_id text NOT NULL,
  name text NOT NULL,
  title text,
  linkedin_url text,
  person_id uuid REFERENCES people(id),
  status text NOT NULL DEFAULT 'found' CHECK (status IN ('found','revealed','no_email','dismissed')),
  found_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, provider, external_id)
);
CREATE INDEX contact_candidates_org_idx ON contact_candidates (organization_id);
CREATE TABLE regulatory_deadlines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title text NOT NULL,
  regime text NOT NULL,
  deadline_date date NOT NULL,
  applies_to text,
  region text,
  source_url text NOT NULL,
  source_quote text NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','rejected','retired')),
  proposed_by text NOT NULL,
  decided_by text,
  decided_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX regulatory_deadlines_date_idx ON regulatory_deadlines (deadline_date) WHERE status = 'approved';
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['contact_candidates','regulatory_deadlines'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
