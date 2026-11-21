-- Uploaded workbook exports (.xlsx) awaiting import by the worker. Bytes are deleted as soon as the import finishes;
-- only the hash, size and outcome are kept. Deny-all RLS like every other table (backend connects as owner).
CREATE TABLE import_uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workbook_key text NOT NULL CHECK (workbook_key IN ('A','B')),
  filename text NOT NULL,
  sha256 text NOT NULL,
  size_bytes integer NOT NULL,
  content bytea,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','importing','completed','failed')),
  uploaded_by text NOT NULL,
  job_id uuid,
  import_run_id uuid,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz
);
ALTER TABLE import_uploads ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON import_uploads FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON import_uploads FROM authenticated; END IF;
END $$;
