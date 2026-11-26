-- Career systems the account scan can now read: Workday, Personio, Teamtailor, Recruitee and Workable (round 7, area 3).
--
-- Rows only, additive and safe to re-run. `rules/sources.yaml` carries the same rows (the seed adds them at start-up); this migration makes sure
-- they exist before the first scan and brings the old Workday row ("not built") in line with the new connector. A source a person has already
-- switched on, paused or changed is never touched: the Workday update applies only while it is still waiting for validation.
INSERT INTO sources(source_id, display_name, canonical_domain, source_tier, source_type, source_family, provider, signal_rule_ids, collection_method,
                    poll_interval_s, licence_notes, config)
VALUES
  ('ats-personio', 'Personio public job feed', 'personio.de', 'S2', 'jobs', 'jobs', 'Personio', ARRAY['CAP-01','CAP-02','CAP-05'], 'official_api', 172800,
   'Personio''s public XML job feed (no login), checked against the live service; robots.txt honoured; the account scan finds the board from the careers page',
   '{"note": "per-account: the scan finds the board"}'::jsonb),
  ('ats-teamtailor', 'Teamtailor public job feed', 'teamtailor.com', 'S2', 'jobs', 'jobs', 'Teamtailor', ARRAY['CAP-01','CAP-02','CAP-05'], 'official_api', 172800,
   'Teamtailor''s public job RSS feed (no login), checked against the live service; robots.txt honoured; the account scan finds the board from the careers page',
   '{"note": "per-account: the scan finds the board"}'::jsonb),
  ('ats-recruitee', 'Recruitee public job offers', 'recruitee.com', 'S2', 'jobs', 'jobs', 'Recruitee', ARRAY['CAP-01','CAP-02','CAP-05'], 'official_api', 172800,
   'Recruitee''s public offers list (no login), checked against the live service; robots.txt honoured; the account scan finds the board from the careers page',
   '{"note": "per-account: the scan finds the board"}'::jsonb),
  ('ats-workable', 'Workable public job widget', 'workable.com', 'S2', 'jobs', 'jobs', 'Workable', ARRAY['CAP-01','CAP-02','CAP-05'], 'official_api', 172800,
   'Workable''s public account widget (no login), read from Workable''s documentation: not yet checked against the live service; robots.txt honoured; the account scan finds the board from the careers page',
   '{"note": "per-account: the scan finds the board"}'::jsonb)
ON CONFLICT (source_id) DO NOTHING;

INSERT INTO source_health(source_id, state)
SELECT source_id, 'unconfigured' FROM sources
WHERE source_id IN ('ats-personio', 'ats-teamtailor', 'ats-recruitee', 'ats-workable')
ON CONFLICT DO NOTHING;

UPDATE sources
SET display_name = 'Workday career sites (public job list)',
    collection_method = 'official_api',
    signal_rule_ids = ARRAY['CAP-01','CAP-02','CAP-05'],
    licence_notes = 'the public job list that every Workday career page is built from (no login); robots.txt honoured; engineering roles only; the account scan finds the career site from the account''s careers page',
    config = config || '{"note": "per-account: the scan finds the career site"}'::jsonb
WHERE source_id = 'ats-workday' AND status = 'pending_connector_validation';
