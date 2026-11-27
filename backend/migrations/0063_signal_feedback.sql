-- "Was this useful?" on every buying signal, tender and lead-list item a seller sees (round 13). One vote per person per item (the item
-- key Today and Buying signals already use), with a reason when it was not useful. The server fills in what the item was (rule and its
-- version, the finding and the model that read it) so the Library can show, per rule, how often sellers found it useful and why not.
-- A vote never hides or changes the item itself. Additive and safe to re-run: one new table and one index.
CREATE TABLE IF NOT EXISTS signal_feedback (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_key        text NOT NULL CHECK (char_length(item_key) <= 200),          -- e.g. signal:<company id>:<signal id>, tender:<notice id>
  item_kind       text NOT NULL CHECK (item_kind IN ('signal', 'tender', 'lead_list', 'deadline')),
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  signal_id       uuid REFERENCES signals(id) ON DELETE SET NULL,
  event_id        uuid REFERENCES events(id) ON DELETE SET NULL,
  rule_id         text,                                                         -- SRC-01, CAP-03 ...; NULL for lead-list and deadline items
  rule_version    text,
  model_version   text,                                                         -- the extractor that read the page behind the finding
  useful          boolean NOT NULL,
  reason          text CHECK (reason IS NULL OR reason IN ('wrong_company', 'out_of_date', 'not_our_work', 'ghost_job', 'already_talking',
                                                           'no_real_need', 'other')),
  note            text CHECK (note IS NULL OR char_length(note) <= 300),
  shown_on        text NOT NULL DEFAULT 'today' CHECK (shown_on IN ('today', 'signals', 'account', 'feed')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, item_key),
  CHECK (NOT useful OR reason IS NULL)                                          -- a reason only for "not useful"
);
CREATE INDEX IF NOT EXISTS signal_feedback_rule_idx ON signal_feedback (rule_id, updated_at);

-- Same baseline as every other table: no access through the public REST roles.
DO $$
BEGIN
  EXECUTE 'ALTER TABLE public.signal_feedback ENABLE ROW LEVEL SECURITY';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE 'REVOKE ALL ON public.signal_feedback FROM anon'; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE 'REVOKE ALL ON public.signal_feedback FROM authenticated'; END IF;
END $$;
