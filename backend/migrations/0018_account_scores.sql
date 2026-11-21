-- One score per account, built from its active signals: strength, freshness, how many different KINDS of signal agree, and stop rules.
CREATE TABLE account_scores (
  organization_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  score numeric(6,1) NOT NULL,
  band text NOT NULL CHECK (band IN ('hot','warm','watch','none')),
  groups text[] NOT NULL DEFAULT '{}',
  signal_count integer NOT NULL DEFAULT 0,
  top jsonb NOT NULL DEFAULT '[]'::jsonb,
  stopped boolean NOT NULL DEFAULT false,
  downgraded boolean NOT NULL DEFAULT false,
  last_signal_at timestamptz,
  computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_scores_band_idx ON account_scores (band, score DESC);
ALTER TABLE account_scores ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.account_scores FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.account_scores FROM authenticated; END IF;
END $$;
