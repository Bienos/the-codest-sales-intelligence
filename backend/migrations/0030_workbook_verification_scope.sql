-- A published page can mention several buyers. Checking it for one workbook
-- account must not satisfy a different account's verification request.
ALTER TABLE verification_requests DROP CONSTRAINT verification_requests_url_origin_key;

CREATE UNIQUE INDEX verification_requests_workbook_account_idx
    ON verification_requests (url, origin, organization_id)
    WHERE origin = 'sheet_signal' AND organization_id IS NOT NULL;

CREATE UNIQUE INDEX verification_requests_shared_url_idx
    ON verification_requests (url, origin)
    WHERE origin <> 'sheet_signal' OR organization_id IS NULL;
