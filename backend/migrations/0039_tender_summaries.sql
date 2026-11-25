-- Open tenders, plain-language summaries. Most notices are titled in the buyer's own language (Lithuanian, Greek, German, Polish...), so
-- the list could not be scanned. A budget-gated background job (tender_summaries) now writes, per open notice, an English and a Polish
-- short title and summary, what the buyer wants, the kind of work, technologies, stated requirements and a fit note for The Codest.
-- The summary is advisory only: the official notice stays the record and is always linked.
--
-- Nullable columns without defaults: adding them does not rewrite the table.
ALTER TABLE procurement_notices
  ADD COLUMN IF NOT EXISTS summary jsonb,                 -- the cleaned model answer (see ri.tender_summary.clean_summary)
  ADD COLUMN IF NOT EXISTS summary_at timestamptz,        -- when it was written (or when the model would not answer)
  ADD COLUMN IF NOT EXISTS summary_model text,
  ADD COLUMN IF NOT EXISTS summary_basis text,            -- md5 of the title and scope it was written from: a changed title or scope makes it due again
  ADD COLUMN IF NOT EXISTS summary_skipped text,          -- why no summary was written (the model gave no usable answer): kept out of the queue
  ADD COLUMN IF NOT EXISTS source_detail jsonb;           -- extra official fields read for the summary (TED: description, lots, estimated value, duration)

ALTER TABLE procurement_notices DROP CONSTRAINT IF EXISTS procurement_notices_summary_object;
ALTER TABLE procurement_notices ADD CONSTRAINT procurement_notices_summary_object
  CHECK ((summary IS NULL OR jsonb_typeof(summary) = 'object') AND (source_detail IS NULL OR jsonb_typeof(source_detail) = 'object'));

-- the tender list and the summary queue both read open notices by deadline
CREATE INDEX IF NOT EXISTS procurement_notices_open_deadline_idx ON procurement_notices (deadline_at) WHERE status IN ('open', 'amended');
