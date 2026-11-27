-- A signature can keep its formatting (round 12): a logo, links, a bold line and small print, as in the person's Gmail. The server cleans what is
-- pasted or imported down to a short list of safe tags (ri.outbound.signature) and keeps both that HTML and its plain-text reading (`signature`,
-- used for the thread, the preview text and the "already ends with it" check). Additive and safe to re-run: one new nullable column, and the
-- plain-text limit is raised from 600 to 2000 characters (a legal footer is longer than a sign-off). Nothing is deleted or rewritten.
ALTER TABLE user_email_signatures ADD COLUMN IF NOT EXISTS signature_html text;

ALTER TABLE user_email_signatures DROP CONSTRAINT IF EXISTS user_email_signatures_signature_check;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_email_signatures_signature_len') THEN
    ALTER TABLE user_email_signatures ADD CONSTRAINT user_email_signatures_signature_len CHECK (char_length(signature) <= 2000);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_email_signatures_html_len') THEN
    ALTER TABLE user_email_signatures ADD CONSTRAINT user_email_signatures_html_len CHECK (signature_html IS NULL OR char_length(signature_html) <= 12000);
  END IF;
END $$;
