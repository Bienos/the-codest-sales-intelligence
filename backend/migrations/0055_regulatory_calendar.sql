-- Regulatory deadline calendar (rules/regulatory_calendar.yaml, src/ri/regulatory_calendar.py).
--
-- The calendar itself lives in the repository, not in the database. A daily scheduler lane ("deadlines_pinned") records one
-- deadline_approaching event per target account per upcoming adopted deadline (event_key "deadline:<deadline id>:<company id>"),
-- through the normal event path. Today and the company page read those events by company and date; this partial index keeps that a
-- small index read instead of a scan of every event. The event type is already allowed (migrations 0002 and 0017).
--
-- Only adds an index: no data changes, nothing dropped. Safe to run again (IF NOT EXISTS). Reversible by dropping the index.
SET LOCAL lock_timeout = '30s';

CREATE INDEX IF NOT EXISTS events_deadline_org_idx ON events (subject_org_id, event_at) WHERE event_type = 'deadline_approaching';
