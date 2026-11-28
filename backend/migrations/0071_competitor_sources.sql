-- More ways to watch competitors (ri/competitor_sources.py): sitemap, own feed, GDELT news, Clutch widget, job systems, registers (KRS,
-- Companies House), public tenders in Poland and the UK, AWS partner finder and vendor partner pages. Each check keeps its last run per firm here.
ALTER TABLE competitor_watch DROP CONSTRAINT IF EXISTS competitor_watch_check_name_check;
ALTER TABLE competitor_watch ADD CONSTRAINT competitor_watch_check_name_check CHECK (check_name IN
  ('homepage','news','jobs','awards','sitemap','feed','gdelt','clutch','careers','krs','ch','tenders','aws','partners'));
