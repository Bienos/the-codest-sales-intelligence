-- Each Lemlist email on its own (round 10): the email as Lemlist gave it, so its words can be cleaned again; the sequence step; and whether
-- it is already on the person's Conversations timeline. Additive only and safe to re-run: new nullable columns and partial indexes.
-- Nothing is deleted or rewritten here; the app re-cleans the stored words itself (ri.outbound.replies.reclean, run by the hourly Lemlist sync).

ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_raw text;               -- the email as Lemlist gave it (HTML or plain text), capped by the app
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_raw_format text;        -- 'html' | 'text'
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_clean_version smallint;  -- which cleaning produced body_text (NULL: before round 10)
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_text_original text;     -- the words as first stored, kept when a better cleaning replaced them
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS raw_refetched_at timestamptz; -- the inbox thread was read again once to get the email as Lemlist gave it
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS sequence_step integer;       -- Lemlist's sequenceStep as it sends it (counted from 0); shown as step + 1
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS step_id text;                -- Lemlist's stable id of the step
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS timeline_at timestamptz;     -- written to the person's Conversations timeline (NULL: still to do)
ALTER TABLE lemlist_replies ADD COLUMN IF NOT EXISTS timeline_at timestamptz;

-- The hourly sync looks only at emails still to clean again or still to put on the timeline: small partial indexes keep that cheap.
CREATE INDEX IF NOT EXISTS lemlist_activities_timeline_todo_idx ON lemlist_activities (happened_at DESC)
  WHERE timeline_at IS NULL AND type IN ('emailsSent', 'emailsReplied');
CREATE INDEX IF NOT EXISTS lemlist_activities_reclean_todo_idx ON lemlist_activities (happened_at DESC)
  WHERE body_text IS NOT NULL AND body_clean_version IS NULL;
-- A person's thread reads their emails and the opens and clicks that followed them, newest first.
CREATE INDEX IF NOT EXISTS lemlist_activities_person_time_idx ON lemlist_activities (person_id, happened_at DESC);
