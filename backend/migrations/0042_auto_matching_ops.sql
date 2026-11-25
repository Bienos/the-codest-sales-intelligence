-- Administration does its own housekeeping (owner feedback, 25 Nov 2026):
--  * company matches are decided automatically (rules first, then one web search, then a small AI check) and a person only sees
--    the few that stay unsure, with the evidence;
--  * failed jobs can be dismissed in bulk once their cause is understood, keeping the audit trail.
--
-- Nullable columns, or NOT NULL with a constant default: adding them does not rewrite the tables. Reversible by dropping the columns.

ALTER TABLE identity_reviews
  ADD COLUMN IF NOT EXISTS resolved_by text,                                  -- 'person' | 'auto' (NULL on rows decided before this migration: a person)
  ADD COLUMN IF NOT EXISTS decision_reason text,                              -- one plain sentence: why this decision
  ADD COLUMN IF NOT EXISTS confidence numeric,                                -- 0..1 for automatic decisions
  ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '{}'::jsonb,       -- rules applied, web search result, AI answer, what the decision changed
  ADD COLUMN IF NOT EXISTS auto_checked_at timestamptz,                       -- last time the automatic matcher looked at it
  ADD COLUMN IF NOT EXISTS auto_attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS auto_hold boolean NOT NULL DEFAULT false,          -- a person undid an automatic decision: never decide it automatically again
  ADD COLUMN IF NOT EXISTS undone_at timestamptz,
  ADD COLUMN IF NOT EXISTS undone_by text;

ALTER TABLE identity_reviews DROP CONSTRAINT IF EXISTS identity_reviews_resolved_by_check;
ALTER TABLE identity_reviews ADD CONSTRAINT identity_reviews_resolved_by_check CHECK (resolved_by IS NULL OR resolved_by IN ('person', 'auto'));
ALTER TABLE identity_reviews DROP CONSTRAINT IF EXISTS identity_reviews_evidence_object;
ALTER TABLE identity_reviews ADD CONSTRAINT identity_reviews_evidence_object CHECK (jsonb_typeof(evidence) = 'object');

-- the automatic matcher takes the open reviews it has not looked at recently; the admin page lists recent decisions
CREATE INDEX IF NOT EXISTS identity_reviews_open_check_idx ON identity_reviews (auto_checked_at NULLS FIRST, created_at) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS identity_reviews_resolved_idx ON identity_reviews (resolved_at DESC) WHERE status <> 'open';

ALTER TABLE dead_letters
  ADD COLUMN IF NOT EXISTS dismissed_at timestamptz,                          -- a person marked it handled (the row and its error stay)
  ADD COLUMN IF NOT EXISTS dismissed_by text,
  ADD COLUMN IF NOT EXISTS dismiss_note text;

CREATE INDEX IF NOT EXISTS dead_letters_open_idx ON dead_letters (created_at DESC) WHERE replayed_at IS NULL AND dismissed_at IS NULL;
