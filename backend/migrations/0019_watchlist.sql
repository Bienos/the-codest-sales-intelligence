-- Accounts a person has starred. Their own site, news, careers page and job board are re-read every week without anyone pressing a button.
CREATE TABLE watchlist (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_email text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_email)
);
CREATE INDEX watchlist_user_idx ON watchlist (user_email);
ALTER TABLE watchlist ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.watchlist FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.watchlist FROM authenticated; END IF;
END $$;
