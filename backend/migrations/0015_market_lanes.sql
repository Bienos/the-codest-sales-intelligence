-- Separate "is this fact proven" from "is it linked to one of our accounts", and tag where and for whom an event matters.
ALTER TABLE events ADD COLUMN IF NOT EXISTS proven boolean NOT NULL DEFAULT false;
ALTER TABLE events ADD COLUMN IF NOT EXISTS market_tier text;            -- core | secondary | other | unknown
ALTER TABLE events ADD COLUMN IF NOT EXISTS lane text;                   -- private | public
ALTER TABLE events ADD COLUMN IF NOT EXISTS market_country text;         -- ISO alpha-2 used for the tier
ALTER TABLE events ADD COLUMN IF NOT EXISTS market_tagged_at timestamptz;
CREATE INDEX IF NOT EXISTS events_market_idx ON events (market_tier, lane) WHERE proven;
CREATE INDEX IF NOT EXISTS events_untagged_idx ON events (market_tagged_at) WHERE market_tagged_at IS NULL;
