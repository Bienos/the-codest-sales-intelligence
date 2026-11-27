-- Competitor tracker (docs/competitor-tracker.md). A competitor's profile is kept whole as JSON: every value in it carries its source link, date
-- and grade (official, reported, estimate). Moves are rows so a person can review them (candidate -> confirmed or dismissed). Tiers, overlap
-- scores and pattern counts are NOT stored: the API works them out from these rows every time, so they never go stale.
CREATE TABLE competitors (
  key text PRIMARY KEY CHECK (key ~ '^[a-z0-9][a-z0-9-]{1,59}$'),
  name text NOT NULL,
  is_us boolean NOT NULL DEFAULT false,               -- The Codest's own row, compared field by field with the others
  reference boolean NOT NULL DEFAULT false,           -- tier 3: firms we learn from but rarely meet
  origin text NOT NULL CHECK (origin IN ('PL','UA','EU')),
  profile jsonb NOT NULL,
  updated_by text NOT NULL,                           -- 'seed' until a person edits the row; the seed never overwrites a person's edit
  updated_at timestamptz NOT NULL DEFAULT now(),
  retired_at timestamptz
);
CREATE UNIQUE INDEX competitors_one_us ON competitors (is_us) WHERE is_us;

CREATE TABLE competitor_moves (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_key text NOT NULL REFERENCES competitors(key) ON DELETE CASCADE,
  happened_on text NOT NULL CHECK (happened_on ~ '^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$'),     -- a year, a month or a day, as precise as the source
  move_type text NOT NULL CHECK (move_type IN ('positioning','offer','pricing','vertical','market','ma_buy','ma_sold','capital','leadership','hiring',
                                               'partner','client','content','results','award')),
  headline text NOT NULL CHECK (length(headline) BETWEEN 5 AND 300),
  detail text,
  so_what text,                                       -- what it means for The Codest; required to confirm a medium or high impact move
  source_url text NOT NULL CHECK (source_url ~ '^https://'),
  grade text NOT NULL CHECK (grade IN ('official','reported','estimate')),
  impact smallint NOT NULL CHECK (impact BETWEEN 1 AND 3),
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','dismissed')),
  added_by text NOT NULL,                             -- 'seed', a person's email, or a collector name
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_by text,
  reviewed_at timestamptz,
  CHECK (status <> 'confirmed' OR impact = 1 OR coalesce(length(trim(so_what)), 0) > 0),
  UNIQUE (competitor_key, happened_on, headline)
);
CREATE INDEX competitor_moves_status_idx ON competitor_moves (status, created_at DESC);

-- Who we met in which Pipedrive deal. Filled by sellers on the page, or from a Pipedrive deal field (RI_PIPEDRIVE_COMPETITOR_FIELD).
CREATE TABLE deal_competitors (
  crm_deal_id bigint NOT NULL,
  competitor_key text NOT NULL REFERENCES competitors(key) ON DELETE CASCADE,
  role text NOT NULL DEFAULT 'unknown' CHECK (role IN ('incumbent','shortlisted','won_against','lost_to','unknown')),
  reason text,
  noted_by text NOT NULL,
  noted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (crm_deal_id, competitor_key)
);
CREATE INDEX deal_competitors_key_idx ON deal_competitors (competitor_key);

-- Market context shown with the tracker: bellwether results, buyer studies, deals, rates, the briefing text and the method. One document per name.
CREATE TABLE competitor_market (
  name text PRIMARY KEY,
  content jsonb NOT NULL,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['competitors','competitor_moves','deal_competitors','competitor_market'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
