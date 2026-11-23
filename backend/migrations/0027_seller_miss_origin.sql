-- A seller can report a page we should have found ("report a miss"); it is read like any other verification request.
ALTER TABLE verification_requests DROP CONSTRAINT IF EXISTS verification_requests_origin_check;
ALTER TABLE verification_requests ADD CONSTRAINT verification_requests_origin_check CHECK (origin IN ('sheet_signal','exa_discovery','manual','seller_miss'));
