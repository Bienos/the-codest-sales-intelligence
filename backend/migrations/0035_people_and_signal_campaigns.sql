-- One people database across the sheets, Apollo, Pipedrive and Lemlist, and campaigns built from buying signals.
-- Nothing here sends anything: a signal campaign becomes a Lemlist campaign only through a two-person approval and the outbound switch.

-- Where each person came from (a person can come from several systems at once).
CREATE TABLE person_sources (
  person_id uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  system text NOT NULL,                               -- sheets | apollo | pipedrive | lemlist | manual
  external_id text NOT NULL DEFAULT '',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (person_id, system, external_id)
);
CREATE INDEX person_sources_system_idx ON person_sources (system);
CREATE INDEX people_work_email_idx ON people (lower(work_email)) WHERE work_email IS NOT NULL;

-- Engagement read back from Lemlist (activity sync and webhooks). Email is kept so unknown leads can still be suppressed.
CREATE TABLE lemlist_activities (
  id text PRIMARY KEY,
  type text NOT NULL,
  email text NOT NULL,
  campaign_id text,
  campaign_name text,
  person_id uuid REFERENCES people(id) ON DELETE SET NULL,
  happened_at timestamptz,
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX lemlist_activities_email_idx ON lemlist_activities (lower(email));
CREATE INDEX lemlist_activities_person_idx ON lemlist_activities (person_id);

-- A campaign aimed at every account showing one or more buying signals.
CREATE TABLE signal_campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  rule_ids text[] NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  steps jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_approval','approved','pushed','archived')),
  review jsonb,
  approval_id uuid REFERENCES approvals(id),
  lemlist_campaign_id text,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  pushed_at timestamptz
);
CREATE TABLE signal_campaign_members (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id uuid NOT NULL REFERENCES signal_campaigns(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid REFERENCES people(id) ON DELETE SET NULL,
  email text,
  included boolean NOT NULL DEFAULT true,
  exclusion text,                                     -- why this account or person is left out, in plain words
  signal jsonb NOT NULL DEFAULT '{}'::jsonb,          -- the evidence the personalisation is built on
  variables jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'planned',
  lemlist_lead_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX signal_campaign_members_uniq ON signal_campaign_members (campaign_id, organization_id, coalesce(person_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX signal_campaign_members_org_idx ON signal_campaign_members (organization_id);
CREATE INDEX signal_campaign_members_person_idx ON signal_campaign_members (person_id);
CREATE INDEX signal_campaigns_approval_idx ON signal_campaigns (approval_id);

-- A signal campaign is pushed to Lemlist through its own approval kind.
ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_kind_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_kind_check CHECK (kind IN ('crm_write','outbound_message','sequence_enrolment','contact_enrichment','credit_spend',
  'supplier_application','quote_or_commitment','proof_release','signal_campaign_push'));

-- Enrolments now come from per-account drafts OR signal campaigns; suppression and reply tracking stay in one place.
ALTER TABLE outbound_enrolments ALTER COLUMN campaign_draft_id DROP NOT NULL;
ALTER TABLE outbound_enrolments ADD COLUMN signal_campaign_id uuid REFERENCES signal_campaigns(id) ON DELETE SET NULL;
ALTER TABLE outbound_enrolments ADD CONSTRAINT outbound_enrolments_one_origin CHECK (campaign_draft_id IS NOT NULL OR signal_campaign_id IS NOT NULL);
CREATE UNIQUE INDEX outbound_enrolments_signal_email ON outbound_enrolments (signal_campaign_id, email) WHERE signal_campaign_id IS NOT NULL;

-- Backfill provenance from what is already stored.
INSERT INTO person_sources (person_id, system, external_id)
  SELECT DISTINCT e.person_id, 'sheets', '' FROM employment e WHERE e.source_link_id IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO person_sources (person_id, system, external_id)
  SELECT person_id, provider, external_id FROM contact_candidates WHERE person_id IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO person_sources (person_id, system, external_id)
  SELECT person_id, 'pipedrive', crm_id::text FROM crm_persons WHERE person_id IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO person_sources (person_id, system, external_id)
  SELECT DISTINCT person_id, 'lemlist', coalesce(lemlist_campaign_id, '') FROM outbound_enrolments WHERE person_id IS NOT NULL ON CONFLICT DO NOTHING;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['person_sources','lemlist_activities','signal_campaigns','signal_campaign_members'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
  END LOOP;
END $$;
