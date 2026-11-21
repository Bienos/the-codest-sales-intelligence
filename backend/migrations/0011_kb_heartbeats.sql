-- Knowledge-base documents uploaded by an administrator (kept in the database, never public) and component heartbeats
-- so the product can show what is actually running by itself. Deny-all RLS like every other table.
CREATE TABLE kb_documents (
  doc_id text PRIMARY KEY,
  html text NOT NULL,
  sha256 text NOT NULL,
  size_bytes integer NOT NULL,
  uploaded_by text NOT NULL,
  uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE system_heartbeats (
  component text PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['kb_documents','system_heartbeats'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
