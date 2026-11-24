-- Update the shipped endpoints. Operator-configured addresses keep their own test history.
UPDATE sources AS s
SET config = (s.config - 'validation') || jsonb_build_object('url', changes.new_url, 'url_basis', changes.basis),
    collection_method = 'change_monitor',
    status = CASE WHEN s.status IN ('active', 'degraded') THEN 'paused' ELSE s.status END
FROM (VALUES
  ('attention-bitkom', 'https://www.bitkom.org/EN/Press/Pressreleases', 'https://www.bitkom.org/Presse/Presseinformation', 'Official Bitkom press releases. Page check passed on 23 Nov 2026.'),
  ('si-nttdata-news', 'https://www.nttdata.com/global/en/news/press-release', 'https://www.nttdata.com/global/en/news', 'Official NTT DATA global newsroom. Page check passed on 23 Nov 2026.'),
  ('attention-fintekpl', 'https://fintek.pl/feed/', 'https://fintek.pl/aktualnosci/', 'Official Fintek.pl news page. Page check passed on 23 Nov 2026.')
) AS changes(id, old_url, new_url, basis)
WHERE s.source_id = changes.id AND s.config->>'url' = changes.old_url
  AND coalesce(s.config->>'url_basis', '') NOT LIKE 'set by %';
