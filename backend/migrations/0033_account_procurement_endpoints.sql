ALTER TABLE account_endpoints DROP CONSTRAINT account_endpoints_kind_check;
ALTER TABLE account_endpoints ADD CONSTRAINT account_endpoints_kind_check
  CHECK (kind IN ('feed','newsroom','careers','job_board','procurement'));
