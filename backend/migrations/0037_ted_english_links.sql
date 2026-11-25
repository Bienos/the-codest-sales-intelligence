-- TED lists a notice's page once per EU language with upper-case keys ("BUL", "SPA", "ENG"...). The collector looked for "eng" only and
-- took the first entry instead, so every stored TED link opened the Bulgarian page. Point every copy of a TED notice link at the English
-- page (the tender list shows the Polish page to Polish users). Only TED notice pages are touched; other sites' links are left alone.

UPDATE procurement_notices
SET official_url = regexp_replace(official_url, '^https://ted\.europa\.eu/[a-z]{2}/notice/', 'https://ted.europa.eu/en/notice/')
WHERE official_url ~ '^https://ted\.europa\.eu/[a-z]{2}/notice/' AND official_url !~ '^https://ted\.europa\.eu/en/notice/';

UPDATE source_documents
SET canonical_url = regexp_replace(canonical_url, '^https://ted\.europa\.eu/[a-z]{2}/notice/', 'https://ted.europa.eu/en/notice/')
WHERE canonical_url ~ '^https://ted\.europa\.eu/[a-z]{2}/notice/' AND canonical_url !~ '^https://ted\.europa\.eu/en/notice/';

-- award notices read for the renewal radar came through the same lookup
UPDATE awarded_contracts
SET official_url = regexp_replace(official_url, '^https://ted\.europa\.eu/[a-z]{2}/notice/', 'https://ted.europa.eu/en/notice/')
WHERE official_url ~ '^https://ted\.europa\.eu/[a-z]{2}/notice/' AND official_url !~ '^https://ted\.europa\.eu/en/notice/';

-- the copies kept on each tender's claim ("official_url") and event ("authoritative_url")
UPDATE claims
SET object_json = jsonb_set(object_json, '{official_url}',
      to_jsonb(regexp_replace(object_json->>'official_url', '^https://ted\.europa\.eu/[a-z]{2}/notice/', 'https://ted.europa.eu/en/notice/')))
WHERE object_json->>'official_url' ~ '^https://ted\.europa\.eu/[a-z]{2}/notice/' AND object_json->>'official_url' !~ '^https://ted\.europa\.eu/en/notice/';

UPDATE events
SET attrs = jsonb_set(attrs, '{authoritative_url}',
      to_jsonb(regexp_replace(attrs->>'authoritative_url', '^https://ted\.europa\.eu/[a-z]{2}/notice/', 'https://ted.europa.eu/en/notice/')))
WHERE attrs->>'authoritative_url' ~ '^https://ted\.europa\.eu/[a-z]{2}/notice/' AND attrs->>'authoritative_url' !~ '^https://ted\.europa\.eu/en/notice/';
