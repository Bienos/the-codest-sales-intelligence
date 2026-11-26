-- Company logos that Exa research finds on the company's own LinkedIn page. Only the address of the picture is kept (never the image itself);
-- the app shows the initials again when a logo no longer loads, and a new research run refreshes it.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS logo_url text CHECK (logo_url IS NULL OR (logo_url LIKE 'https://%' AND length(logo_url) <= 600));
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS logo_checked_at timestamptz;
