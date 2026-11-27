-- Per-user connections (Gmail, Lemlist, Bluedot) and what they bring: a shared timeline of conversations with our contacts.
-- Credentials are stored ENCRYPTED (AES-256-GCM, see ri/vault.py) and never returned by the API. The shared database stays the master:
-- every interaction row says whose connection it came from and who may see the detail (owner only, or the whole team).
CREATE TABLE IF NOT EXISTS user_connections (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider      text NOT NULL CHECK (provider IN ('gmail', 'lemlist', 'bluedot')),
  status        text NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'needs_attention', 'disconnected')),
  account_label text,                                   -- the connected address or team name, for display only
  secret_enc    bytea,                                  -- sealed credentials (refresh token / API key); never leaves the server
  scopes        text,
  settings      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
  cursor        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(cursor) = 'object'),   -- where the last sync stopped
  webhook_token_hash text,                              -- Bluedot: sha256 of the personal webhook token (the token itself is shown once)
  connected_at  timestamptz NOT NULL DEFAULT now(),
  last_sync_at  timestamptz,
  last_error    text,
  disconnected_at timestamptz,
  UNIQUE (user_id, provider)
);
CREATE UNIQUE INDEX IF NOT EXISTS user_connections_webhook_token_idx ON user_connections (webhook_token_hash) WHERE webhook_token_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS contact_interactions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,        -- whose connection it came from (the owner)
  channel         text NOT NULL CHECK (channel IN ('email', 'lemlist', 'meeting')),
  external_id     text NOT NULL,                                                -- message id / campaign event id / meeting id: makes every sync repeatable
  person_id       uuid REFERENCES people(id) ON DELETE SET NULL,
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  direction       text CHECK (direction IN ('in', 'out', 'both')),
  occurred_at     timestamptz NOT NULL,
  title           text,                                                         -- email subject / campaign name / meeting title
  gist            text,                                                         -- email snippet / campaign event / meeting summary (short)
  visibility      text NOT NULL DEFAULT 'owner' CHECK (visibility IN ('owner', 'team')),
  meta            jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(meta) = 'object'),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, channel, external_id)
);
CREATE INDEX IF NOT EXISTS contact_interactions_person_idx ON contact_interactions (person_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS contact_interactions_org_idx ON contact_interactions (organization_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS contact_interactions_user_idx ON contact_interactions (user_id, channel, occurred_at DESC);

-- The last few raw Bluedot webhook deliveries per user, so the parser can be matched to the real format (kept short, trimmed by the app).
CREATE TABLE IF NOT EXISTS connection_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider    text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  payload     jsonb NOT NULL,
  handled     boolean NOT NULL DEFAULT false,
  note        text
);
CREATE INDEX IF NOT EXISTS connection_events_user_idx ON connection_events (user_id, provider, received_at DESC);

-- Same baseline as every other table: no access through the public REST roles.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['user_connections', 'contact_interactions', 'connection_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
