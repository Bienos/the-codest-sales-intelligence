-- Open tenders, who is buying. The list showed about 1,100 open software notices, almost all from public bodies, and the owner asked for
-- our ICP only: banks first, then other financial services (insurers, payments, exchanges and clearing, asset managers, pension funds,
-- lenders, financial supervisors), then the other ICP sectors. Each open notice now carries its buyer's sector, decided by rules on the buyer
-- name, the linked account's segment and the CPV codes (ri.tender_sector); where the rules are not sure, the summary job's model decides
-- and only those notices (and the ICP ones) are summarised.
--
-- Nullable columns without defaults: adding them does not rewrite the table. The values are filled by ri.tender_sector.classify_open on the
-- next read of the list or the next summary run (a few milliseconds for the open notices), not here.
ALTER TABLE procurement_notices
  ADD COLUMN IF NOT EXISTS buyer_sector text,      -- banking, financial_services, other_icp, not_icp
  ADD COLUMN IF NOT EXISTS buyer_kind text,        -- within the sector: central_bank, savings_bank, insurance, financial_authority, public_body...
  ADD COLUMN IF NOT EXISTS buyer_public boolean,   -- owned or run by the state (central and development banks, supervisors, public insurers)
  ADD COLUMN IF NOT EXISTS sector_sure boolean,    -- false: the rules could not tell, the model decides when it summarises the notice
  ADD COLUMN IF NOT EXISTS sector_source text,     -- rules, account (the linked company's segment) or model
  ADD COLUMN IF NOT EXISTS sector_why text,        -- the words or field that decided it, for checking
  ADD COLUMN IF NOT EXISTS sector_basis text;      -- md5 of what it was decided from (rule version, name, account, CPV, model answer)

ALTER TABLE procurement_notices DROP CONSTRAINT IF EXISTS procurement_notices_buyer_sector_known;
ALTER TABLE procurement_notices ADD CONSTRAINT procurement_notices_buyer_sector_known
  CHECK (buyer_sector IS NULL OR buyer_sector IN ('banking', 'financial_services', 'other_icp', 'not_icp'));

-- the summary queue reads open ICP notices (and the ones the rules were not sure about) by deadline
CREATE INDEX IF NOT EXISTS procurement_notices_open_sector_idx ON procurement_notices (buyer_sector, deadline_at) WHERE status IN ('open', 'amended');
