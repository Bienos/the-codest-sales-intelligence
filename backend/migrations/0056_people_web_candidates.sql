-- Decision makers found on the web (the company's own pages, news) and not only through LinkedIn.
-- Additive and safe to re-run: a candidate may now have no LinkedIn address when a public page names them; that page is kept as evidence.
ALTER TABLE people_candidates ALTER COLUMN linkedin_url DROP NOT NULL;
ALTER TABLE people_candidates ADD COLUMN IF NOT EXISTS evidence_url text;          -- the page that names the person (web finds)
ALTER TABLE people_candidates ADD COLUMN IF NOT EXISTS evidence_quote text;        -- the exact line on that page
ALTER TABLE people_candidates DROP CONSTRAINT IF EXISTS people_candidates_company_match_check;
ALTER TABLE people_candidates ADD CONSTRAINT people_candidates_company_match_check CHECK (company_match IN ('headline', 'profile', 'web'));
-- one web-only candidate per name at an account (LinkedIn candidates stay unique by their address)
CREATE UNIQUE INDEX IF NOT EXISTS people_candidates_web_name_uq ON people_candidates (organization_id, lower(name)) WHERE linkedin_url IS NULL;
