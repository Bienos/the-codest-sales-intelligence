-- What someone wrote back to our Lemlist campaign, and what is needed to answer them in the same email thread.
-- Additive only and safe to re-run: new nullable columns on lemlist_activities and one new table. Nothing is deleted or rewritten.

-- The words of an email (plain text, quoted history removed, capped by the app) and the Lemlist ids that let us reply in the same thread
-- from the same mailbox that sent the campaign. Filled by the hourly Lemlist sync and when someone opens the person's thread.
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_text text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_source text;          -- activity | inbox | preview (Lemlist sent only its short preview)
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_checked_at timestamptz; -- last time we asked Lemlist for the words (NULL = never)
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS body_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS subject text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS message_id text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS lemlist_team_id text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS lemlist_contact_id text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS lemlist_lead_id text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS send_user_id text;          -- the Lemlist user whose mailbox sent the campaign
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS send_user_email text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS send_user_mailbox_id text;
ALTER TABLE lemlist_activities ADD COLUMN IF NOT EXISTS send_user_name text;

-- Replies written in this app and sent through the Lemlist inbox. One row per draft: the browser sends a key per draft, so a double
-- click (or a retry after a lost answer) can never send twice. The text is kept so the reply shows in the thread; the audit trail
-- only records its length.
CREATE TABLE IF NOT EXISTS lemlist_replies (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key      text NOT NULL UNIQUE,
  reply_to_activity_id text NOT NULL REFERENCES lemlist_activities(id),
  person_id            uuid REFERENCES people(id) ON DELETE SET NULL,
  body_text            text NOT NULL,
  body_hash            text NOT NULL,
  status               text NOT NULL DEFAULT 'sending' CHECK (status IN ('sending', 'sent', 'failed', 'unknown')),
  error                text,                                   -- plain words, shown to the person who sent it
  send_user_id         text,
  send_user_email      text,
  send_user_mailbox_id text,
  lemlist_contact_id   text,
  lemlist_lead_id      text,
  created_by           text NOT NULL,                          -- the signed-in person's email
  created_at           timestamptz NOT NULL DEFAULT now(),
  sent_at              timestamptz
);
CREATE INDEX IF NOT EXISTS lemlist_replies_person_idx ON lemlist_replies (person_id, created_at DESC);
CREATE INDEX IF NOT EXISTS lemlist_replies_activity_idx ON lemlist_replies (reply_to_activity_id, created_at DESC);

-- Same baseline as every other table: no access through the public REST roles.
DO $$
BEGIN
  EXECUTE 'ALTER TABLE public.lemlist_replies ENABLE ROW LEVEL SECURITY';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE 'REVOKE ALL ON public.lemlist_replies FROM anon'; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE 'REVOKE ALL ON public.lemlist_replies FROM authenticated'; END IF;
END $$;
