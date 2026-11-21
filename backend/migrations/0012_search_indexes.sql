-- Trigram indexes so keyword search stays fast as the sheets grow. pg_trgm lives in "public" locally and in "extensions" on Supabase,
-- so the operator class schema is looked up rather than assumed.
DO $$
DECLARE ns text;
BEGIN
  SELECT n.nspname INTO ns FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm';
  IF ns IS NULL THEN
    RAISE NOTICE 'pg_trgm not installed; skipping trigram indexes';
    RETURN;
  END IF;
  EXECUTE format('CREATE INDEX IF NOT EXISTS legacy_research_text_trgm ON public.legacy_account_research USING gin ((original::text) %I.gin_trgm_ops)', ns);
  EXECUTE format('CREATE INDEX IF NOT EXISTS organizations_name_trgm ON public.organizations USING gin (display_name %I.gin_trgm_ops)', ns);
  EXECUTE format('CREATE INDEX IF NOT EXISTS events_summary_trgm ON public.events USING gin (summary %I.gin_trgm_ops)', ns);
END $$;
