-- Source review of 26 Nov 2026: pause the dead weight, reversibly (round 7, sources).
--
-- Six sources read for days brought nothing for the accounts: heise online and Finansinspektionen were almost all off-topic, and the ECB,
-- BIS, LeadDev and Cashless.pl feeds were read many times without one finding. They are PAUSED, not deleted: everything they collected
-- stays, and switching one on again by hand in Administration, Sources works as for any source.
--
-- How the pause is kept. The daily automatic test (ri.sources.autovalidate) never switches on a source whose latest audit entry is a
-- pause. So each pause writes the same audit entry a person's pause writes (operation pause_source, here with the actor
-- system:source-review) and the plain reason in the source's config (review_note, and the verdict the Administration page shows).
--
-- Only rows still switched on are touched (a source someone has already paused or changed is left alone), and a source is paused by this
-- review once: after a person switches one on again, running this file again does not pause it again. `rules/sources.yaml` carries the
-- same notes (review_note) so a fresh database starts with these sources paused too; there the seed does it, as this finds no rows.
-- Updates and inserts only; reversible by switching the sources on again.
WITH review(source_id, note) AS (VALUES
  ('regulator-ecb', 'Paused on 26 Nov 2026 after a review: 20 of its pages were read, and it brought no finding for our accounts. Switch on again by hand if wanted.'),
  ('attention-leaddev', 'Paused on 26 Nov 2026 after a review: 10 of its pages were read, and it brought no finding for our accounts. Switch on again by hand if wanted.'),
  ('regulator-fi-se', 'Paused on 26 Nov 2026 after a review: most of its pages (90 of 102) were off-topic, so it brought almost nothing for our accounts. Switch on again by hand if wanted.'),
  ('regulator-bis', 'Paused on 26 Nov 2026 after a review: it brought no finding for our accounts. Switch on again by hand if wanted.'),
  ('attention-heise', 'Paused on 26 Nov 2026 after a review: almost all of its articles (254 of 268) were off-topic, so it brought almost nothing for our accounts. Switch on again by hand if wanted.'),
  ('attention-cashlesspl', 'Paused on 26 Nov 2026 after a review: 10 of its pages were read, and it brought no finding for our accounts. Switch on again by hand if wanted.')
), paused AS (
  UPDATE sources s
     SET status = 'paused',
         config = coalesce(s.config, '{}'::jsonb) || jsonb_build_object(
                    'review_note', r.note,
                    'auto', jsonb_build_object('state', 'paused', 'eligible', false, 'reason', r.note,
                                               'todo', 'Switch it on again by hand when it should run. It is never switched on automatically.',
                                               'at', now()))
    FROM review r
   WHERE s.source_id = r.source_id
     AND s.status IN ('active', 'degraded')
     AND NOT EXISTS (SELECT 1 FROM audit_events a
                      WHERE a.record_type = 'source' AND a.record_id = s.source_id
                        AND a.operation = 'pause_source' AND a.actor = 'system:source-review')
  RETURNING s.source_id, r.note
), health AS (
  UPDATE source_health h SET state = 'paused', detail = p.note, updated_at = now()
    FROM paused p WHERE h.source_id = p.source_id
  RETURNING h.source_id
)
INSERT INTO audit_events(actor, operation, record_type, record_id, detail)
SELECT 'system:source-review', 'pause_source', 'source', p.source_id, jsonb_build_object('reason', p.note, 'review', '2026-11-26')
  FROM paused p;
