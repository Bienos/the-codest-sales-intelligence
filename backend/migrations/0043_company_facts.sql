-- Missing company facts are filled in automatically with Exa (owner feedback, 25 Nov 2026: "why is ownership needed to check, for what?
-- If you need information, populate that with Exa research"). See src/ri/research/company_facts.py.
--
--  * ownership gains 'mutual_or_cooperative' (building societies, cooperative and savings banks owned by their members): fine for a target
--    account, like privately held or listed; state-owned is not;
--  * facts_checked_at: when Exa last looked at the company (it never looks again within 30 days);
--  * facts_status: what that look found ('found' = at least one fact written, 'nothing', 'failed' = the answer could not be read);
--  * facts_source: where each filled-in fact came from, per fact: {"by": "exa" | "person", "url", "quote", "at", ...}, plus "_last"
--    (the pages Exa looked at last time). Page text is never stored.
--
-- Nullable columns, or NOT NULL with a constant default: no table rewrite. The widened CHECK accepts every existing row.
-- Reversible: drop the three columns and restore the old CHECK (after setting any 'mutual_or_cooperative' row back to 'unknown').

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_company_ownership_check;
ALTER TABLE organizations ADD CONSTRAINT organizations_company_ownership_check
  CHECK (company_ownership IN ('unknown', 'private', 'listed', 'state_owned', 'mutual_or_cooperative'));

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS facts_checked_at timestamptz,
  ADD COLUMN IF NOT EXISTS facts_status text,
  ADD COLUMN IF NOT EXISTS facts_source jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_facts_status_check;
ALTER TABLE organizations ADD CONSTRAINT organizations_facts_status_check
  CHECK (facts_status IS NULL OR facts_status IN ('found', 'nothing', 'failed'));
ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_facts_source_object;
ALTER TABLE organizations ADD CONSTRAINT organizations_facts_source_object CHECK (jsonb_typeof(facts_source) = 'object');

-- the daily limit counts today's looks; the progress note counts this week's
CREATE INDEX IF NOT EXISTS organizations_facts_checked_idx ON organizations (facts_checked_at) WHERE facts_checked_at IS NOT NULL;
