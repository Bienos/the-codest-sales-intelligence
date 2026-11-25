-- The lead list (workbook A, "Leads Master List" layout) carries its own account ID and its own judgement of each account.
-- They are owned by the list: every import overwrites them. NULL means the company is not on the current list.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS list_account_id text,
  ADD COLUMN IF NOT EXISTS list_eligibility text,
  ADD COLUMN IF NOT EXISTS list_priority text,
  ADD COLUMN IF NOT EXISTS list_score numeric,
  ADD COLUMN IF NOT EXISTS list_icp_segment text,
  ADD COLUMN IF NOT EXISTS list_region text;

CREATE INDEX IF NOT EXISTS organizations_list_account_id ON organizations (list_account_id) WHERE list_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS organizations_list_eligibility ON organizations (list_eligibility) WHERE list_eligibility IS NOT NULL;
