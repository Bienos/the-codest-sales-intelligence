-- LinkedIn profile pictures that Exa returns with a person's profile page. Only the address of the picture is kept (never the image itself);
-- LinkedIn's picture addresses expire after a while, so the app shows the initials again when a picture no longer loads, and a new research run refreshes it.
ALTER TABLE people ADD COLUMN IF NOT EXISTS photo_url text;
ALTER TABLE people ADD COLUMN IF NOT EXISTS photo_checked_at timestamptz;
ALTER TABLE people_candidates ADD COLUMN IF NOT EXISTS photo_url text;
