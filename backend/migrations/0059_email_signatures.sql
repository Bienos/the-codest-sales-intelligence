-- The signature (footer) under a reply sent from this app (round 11). Lemlist adds none to an email sent through POST /inbox/email (the
-- endpoint has no signature field; a Lemlist user's own signature is only put where a template says {{signature}}), so each person who
-- uses the app keeps one plain-text signature here, and ri.outbound.replies.send_reply appends it once to the reply.
-- Additive only and safe to re-run: one new table, one new nullable column and one partial index. Nothing is deleted or rewritten.

CREATE TABLE IF NOT EXISTS user_email_signatures (
  user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  signature  text NOT NULL CHECK (char_length(signature) <= 600),   -- plain text, line breaks kept; '' means "no signature" on purpose
  source     text NOT NULL DEFAULT 'saved',                          -- saved (written by the person) | the suggestion they accepted
  updated_by text NOT NULL,                                          -- the signed-in person's email
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- The signature appended to a reply sent from here (NULL: none). body_text stays the words the person typed, so the duplicate checks and
-- the timeline keep working on the words alone; the thread shows both.
ALTER TABLE lemlist_replies ADD COLUMN IF NOT EXISTS signature_text text;

-- "Step 2 of 3" on a campaign email: the furthest step Lemlist has sent in each campaign.
CREATE INDEX IF NOT EXISTS lemlist_activities_campaign_step_idx ON lemlist_activities (campaign_id, sequence_step)
  WHERE type = 'emailsSent' AND sequence_step IS NOT NULL;

-- Same baseline as every other table: no access through the public REST roles.
DO $$
BEGIN
  EXECUTE 'ALTER TABLE public.user_email_signatures ENABLE ROW LEVEL SECURITY';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE 'REVOKE ALL ON public.user_email_signatures FROM anon'; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE 'REVOKE ALL ON public.user_email_signatures FROM authenticated'; END IF;
END $$;
