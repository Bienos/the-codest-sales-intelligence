-- Outbound: who an approved draft sequence was enrolled for in Lemlist, and what happened next. Nothing is sent without a two-person approval.
CREATE TABLE outbound_enrolments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_draft_id uuid NOT NULL REFERENCES campaign_drafts(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid REFERENCES people(id),
  email text NOT NULL,
  status text NOT NULL DEFAULT 'pushed' CHECK (status IN ('pushed','replied','interested','not_interested','bounced','unsubscribed','failed')),
  lemlist_campaign_id text,
  lemlist_lead_id text,
  approval_id uuid REFERENCES approvals(id),
  last_event_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (campaign_draft_id, email)
);
CREATE INDEX outbound_enrolments_org_idx ON outbound_enrolments (organization_id);
CREATE INDEX outbound_enrolments_email_idx ON outbound_enrolments (lower(email));
CREATE TABLE outbound_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enrolment_id uuid REFERENCES outbound_enrolments(id) ON DELETE CASCADE,
  kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  dedupe_key text UNIQUE,
  received_at timestamptz NOT NULL DEFAULT now()
);
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['outbound_enrolments','outbound_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
