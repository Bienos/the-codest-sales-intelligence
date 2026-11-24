ALTER TABLE account_endpoints ADD COLUMN last_success_at timestamptz;
UPDATE account_endpoints SET last_success_at=last_scanned_at WHERE last_status='ok';
