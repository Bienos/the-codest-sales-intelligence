-- Company and person research written from Exa web searches, people found with Exa, and who owns an account and how we relate to it.
-- Every research statement keeps the address of the page it came from; a statement without one is dropped before it is stored.

-- One row per company research run. `content` is the structured profile; `sources` lists every page Exa returned (address, title, date).
CREATE TABLE account_profiles (
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
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX account_profiles_org_idx ON account_profiles (organization_id, created_at DESC);

-- One row per person research run. Professional information only, every item cited.
CREATE TABLE person_profiles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
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
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX person_profiles_person_idx ON person_profiles (person_id, created_at DESC);
CREATE INDEX person_profiles_org_idx ON person_profiles (organization_id);

-- "Find decision makers": one row per search run, and the LinkedIn profiles it found. A candidate becomes a person only when someone adds it.
CREATE TABLE people_searches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  searches integer NOT NULL DEFAULT 0,
  exa_cost_usd numeric NOT NULL DEFAULT 0,
  found integer NOT NULL DEFAULT 0,
  new_candidates integer NOT NULL DEFAULT 0,
  already_known integer NOT NULL DEFAULT 0,
  skipped integer NOT NULL DEFAULT 0,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX people_searches_org_idx ON people_searches (organization_id, created_at DESC);

CREATE TABLE people_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  search_id uuid REFERENCES people_searches(id) ON DELETE SET NULL,
  provider text NOT NULL DEFAULT 'exa',
  linkedin_url text NOT NULL,                 -- normalised https://www.linkedin.com/in/<handle>
  name text NOT NULL,
  title text,
  persona text,                               -- which part of the buying group the search was looking for
  seniority text,
  snippet text,
  company_match text NOT NULL DEFAULT 'headline' CHECK (company_match IN ('headline','profile')),
  status text NOT NULL DEFAULT 'found' CHECK (status IN ('found','added','dismissed','already_known')),
  person_id uuid REFERENCES people(id) ON DELETE SET NULL,
  found_by text NOT NULL,
  decided_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, linkedin_url)
);
CREATE INDEX people_candidates_search_idx ON people_candidates (search_id);
CREATE INDEX people_candidates_person_idx ON people_candidates (person_id);

-- Who in the team owns the account, and what our relationship with the company is. NULL relationship = nobody has recorded it.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS owner_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS owner_assigned_by text,
  ADD COLUMN IF NOT EXISTS owner_assigned_at timestamptz,
  ADD COLUMN IF NOT EXISTS relationship text CHECK (relationship IN ('none','prospect','in_talks','past_client','client')),
  ADD COLUMN IF NOT EXISTS relationship_note text,
  ADD COLUMN IF NOT EXISTS relationship_set_by text,
  ADD COLUMN IF NOT EXISTS relationship_set_at timestamptz;
CREATE INDEX IF NOT EXISTS organizations_owner_user_idx ON organizations (owner_user_id) WHERE owner_user_id IS NOT NULL;

-- Same baseline as every other table: no access through the public REST roles.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['account_profiles','person_profiles','people_searches','people_candidates'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
