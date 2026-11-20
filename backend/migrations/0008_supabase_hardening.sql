-- Supabase advisor fixes: pin the trigger function's search_path; keep extensions out of public.
-- Guarded so the same migration also runs on plain PostgreSQL (CI / local), where the 'extensions' schema does not exist.
ALTER FUNCTION forbid_mutation() SET search_path = pg_catalog;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
    IF EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm' AND n.nspname = 'public') THEN
      ALTER EXTENSION pg_trgm SET SCHEMA extensions;
    END IF;
    EXECUTE format('ALTER DATABASE %I SET search_path = public, extensions', current_database());
  END IF;
END $$;
