-- Indexes for the scheduler's housekeeping and the account page (data pipeline review, 26 Nov 2026).
--
-- Measured on production statistics (pg_stat_statements, aggregate only):
--  * the awards relevance re-check scanned all 61k award notices on every 5-minute tick to find nothing (1.6 s each, 19 min a day);
--  * the re-check of skipped articles scanned every stored page on every tick (0.4 s each);
--  * "pages waiting to be read" (worker and Administration) scanned every stored page;
--  * the account page and the per-account lookups read the workbook rows of one company with a full scan (0.2 s each);
--  * "awards that matter: how many, since when" counted every award (1 s).
--
-- Plain CREATE INDEX (migrations run inside one transaction, so CONCURRENTLY is not possible). The largest table here is 54 MB, which
-- builds in a second or two. lock_timeout makes the migration stop cleanly, instead of queueing writers behind it, when a long worker
-- transaction holds one of these tables: deploy again at a quieter moment. Reversible by dropping the indexes; no data changes.
SET LOCAL lock_timeout = '30s';

-- the scheduler asks "is any award stored under an older relevance rule?" (relevance_v < current OR > current OR NULL): an index range test
CREATE INDEX IF NOT EXISTS awarded_contracts_relevance_v_idx ON awarded_contracts (relevance_v);

-- relevant awards by date: the renewals summary (count, first award) and the incumbents list (relevant AND awarded_on >= ...)
CREATE INDEX IF NOT EXISTS awarded_contracts_relevant_awarded_idx ON awarded_contracts (awarded_on) WHERE relevant;

-- articles skipped by the cheap relevance check in the last weeks (events.triage.retriage)
CREATE INDEX IF NOT EXISTS source_documents_skipped_idx ON source_documents (fetched_at)
  WHERE raw_meta->>'triage' = 'skipped_not_relevant' AND content_text IS NOT NULL;

-- pages waiting for the model to read them (ingest.process, Administration "pages waiting")
CREATE INDEX IF NOT EXISTS source_documents_pending_idx ON source_documents (fetched_at)
  WHERE raw_meta->>'pending_extraction' = 'true' AND content_text IS NOT NULL;

-- one company's workbook rows, newest first (account page, leads, Today brief, discovery plans)
CREATE INDEX IF NOT EXISTS legacy_research_org_kind_idx ON legacy_account_research (organization_id, kind, observed_at DESC);

-- a tender by its official notice number across sources (relinking an event to its notice, the findings join)
CREATE INDEX IF NOT EXISTS procurement_notices_notice_id_idx ON procurement_notices (notice_id);
