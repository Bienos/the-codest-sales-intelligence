-- Outbound sequence drafts for one company. Drafts only: nothing here sends. A person approves; sending is a separate, later step.
CREATE TABLE campaign_drafts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requested_by text NOT NULL,
  role text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','draft','needs_work','failed','approved','rejected')),
  steps jsonb,
  review jsonb,
  rounds integer NOT NULL DEFAULT 0,
  facts jsonb,
  model text,
  tokens_in integer, tokens_out integer,
  error text,
  decided_by text, decided_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX campaign_drafts_org_idx ON campaign_drafts (organization_id, created_at DESC);
ALTER TABLE campaign_drafts ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.campaign_drafts FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.campaign_drafts FROM authenticated; END IF;
END $$;
