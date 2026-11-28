-- AI assistants: people connect their own Claude or ChatGPT to the app (a remote MCP server signed in with OAuth 2.1), and the in-app assistant.
-- The app is its own OAuth authorization server: the person signs in with Google as usual and approves the client on a consent page.
-- Every token is stored as a SHA-256 hash only (the plaintext is handed to the client once). A token always acts AS the person who
-- approved it, with that person's role and account scope, read-only. Revoking the grant (or deactivating the user) ends it at once.

-- Clients that registered themselves (Dynamic Client Registration, RFC 7591): Claude, ChatGPT, Claude Code, ...
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id          text PRIMARY KEY,
  client_secret_hash text,                                   -- only for clients that asked for client_secret_post / client_secret_basic
  client_name        text NOT NULL,
  redirect_uris      text[] NOT NULL CHECK (cardinality(redirect_uris) BETWEEN 1 AND 10),
  token_endpoint_auth_method text NOT NULL DEFAULT 'none' CHECK (token_endpoint_auth_method IN ('none', 'client_secret_post', 'client_secret_basic')),
  client_uri         text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  last_used_at       timestamptz
);

-- One person's approval of one client. Tokens hang off it; revoking it revokes all of them.
CREATE TABLE IF NOT EXISTS oauth_grants (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id    text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  scope        text NOT NULL,
  resource     text NOT NULL,                                -- the MCP server URL the tokens are bound to (RFC 8707 audience)
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  revoked_at   timestamptz,
  revoked_reason text
);
CREATE INDEX IF NOT EXISTS oauth_grants_user_idx ON oauth_grants (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS oauth_grants_client_idx ON oauth_grants (client_id);

-- Authorization codes: single use, a few minutes, PKCE S256 only.
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash      text PRIMARY KEY,
  grant_id       uuid NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  redirect_uri   text NOT NULL,
  code_challenge text NOT NULL,
  expires_at     timestamptz NOT NULL,
  used_at        timestamptz
);
CREATE INDEX IF NOT EXISTS oauth_codes_grant_idx ON oauth_codes (grant_id);

-- Access and refresh tokens. A refresh token is rotated on every use; presenting a rotated one again revokes the whole grant (it was stolen).
CREATE TABLE IF NOT EXISTS oauth_tokens (
  token_hash  text PRIMARY KEY,
  grant_id    uuid NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('access', 'refresh')),
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,                                   -- refresh tokens: when it was exchanged (then it is spent)
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant_idx ON oauth_tokens (grant_id, kind);
CREATE INDEX IF NOT EXISTS oauth_tokens_expiry_idx ON oauth_tokens (expires_at);

-- A person's own model API key for the in-app assistant (optional): sealed with ri/vault.py, never returned.
CREATE TABLE IF NOT EXISTS ai_personal_keys (
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider    text NOT NULL CHECK (provider IN ('anthropic')),
  secret_enc  bytea NOT NULL,
  hint        text,                                          -- last four characters, for display
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  PRIMARY KEY (user_id, provider)
);

-- The in-app assistant has its own monthly token allowance, apart from page reading and research (provider 'anthropic', kind 'assistant').
ALTER TABLE provider_budgets DROP CONSTRAINT IF EXISTS provider_budgets_kind_check;
ALTER TABLE provider_budgets ADD CONSTRAINT provider_budgets_kind_check CHECK (kind IN ('daily','monthly','hypothesis','contact_enrichment','assistant'));

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['oauth_clients', 'oauth_grants', 'oauth_codes', 'oauth_tokens', 'ai_personal_keys'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
