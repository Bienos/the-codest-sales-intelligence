-- Accent-insensitive search: "krakow" finds "Kraków", "wisla" finds "Wisła", "zurich" finds "Zürich". Pure SQL (no extension needed),
-- IMMUTABLE so it can be indexed. Covers Polish, German, Nordic and the common Western European letters.
CREATE OR REPLACE FUNCTION ri_fold(t text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT translate(replace(lower(coalesce(t, '')), 'ß', 'ss'),
    'ąćęłńóśźżäöüåæøéèêëáàâãíìîïóòôõúùûçñýÿšžčřěůđő',
    'acelnoszzaouaaoeeeeaaaaiiiioooouuucnyyszcreudo')
$$;

-- Trigram indexes on the folded text, so the common searches stay fast (looked up the same way as 0012).
DO $$
DECLARE ns text;
BEGIN
  SELECT n.nspname INTO ns FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm';
  IF ns IS NULL THEN
    RAISE NOTICE 'pg_trgm not installed; skipping folded trigram indexes';
    RETURN;
  END IF;
  EXECUTE format('CREATE INDEX IF NOT EXISTS organizations_name_fold_trgm ON public.organizations USING gin (public.ri_fold(display_name) %I.gin_trgm_ops)', ns);
  EXECUTE format('CREATE INDEX IF NOT EXISTS events_summary_fold_trgm ON public.events USING gin (public.ri_fold(summary) %I.gin_trgm_ops)', ns);
  EXECUTE format('CREATE INDEX IF NOT EXISTS legacy_research_fold_trgm ON public.legacy_account_research USING gin (public.ri_fold(original::text) %I.gin_trgm_ops)', ns);
END $$;
