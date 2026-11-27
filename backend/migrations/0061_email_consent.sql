-- A person's consent to be emailed (round 13). In countries where B2B cold email needs prior consent (rules/email_rules.yaml: Germany,
-- Austria, Switzerland, Denmark, the Netherlands, Poland, and personal addresses in Finland and Norway), only people with consent recorded
-- here are added to a Lemlist campaign. A seller records it on the person's page with a note of how and when it was given; withdrawing it
-- sets email_consent_at back to NULL and keeps the note of who withdrew it. Additive and safe to re-run: three nullable columns, nothing else.
ALTER TABLE people ADD COLUMN IF NOT EXISTS email_consent_at timestamptz;
ALTER TABLE people ADD COLUMN IF NOT EXISTS email_consent_by text;           -- the signed-in person who recorded or withdrew it
ALTER TABLE people ADD COLUMN IF NOT EXISTS email_consent_note text;         -- how and when it was given, in the seller's words

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'people_email_consent_note_len') THEN
    ALTER TABLE people ADD CONSTRAINT people_email_consent_note_len CHECK (email_consent_note IS NULL OR char_length(email_consent_note) <= 500);
  END IF;
END $$;
