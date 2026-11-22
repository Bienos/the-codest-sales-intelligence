-- One-off repair: news items stored in the last 21 days that were never read by the model, never triaged and never produced an event
-- (their run stopped before extraction, or no model was available). Queue them; the next source runs read them, oldest first, under the token cap.
UPDATE source_documents d
SET raw_meta = coalesce(d.raw_meta, '{}'::jsonb) || '{"pending_extraction": true}'::jsonb
WHERE d.content_text IS NOT NULL
  AND d.fetched_at > now() - interval '21 days'
  AND coalesce(d.raw_meta->>'structured', '') = ''
  AND coalesce(d.raw_meta->>'triage', '') = ''
  AND coalesce(d.raw_meta->>'pending_extraction', '') = ''
  AND NOT EXISTS (SELECT 1 FROM event_documents ed WHERE ed.document_id = d.id)
  AND NOT EXISTS (SELECT 1 FROM provider_usage u WHERE u.document_id = d.id);
