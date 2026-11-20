-- R3 Opportunity intelligence + approvals + CRM mirror (read side).
-- Spec refs: 06 §3/§11, 07 §2-§9, 09 §4/§10, 11 §5, 14 §6.

-- ---------------------------------------------------------------- approvals (generic, immutable payload hash)
CREATE TABLE approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('crm_write','outbound_message','sequence_enrolment','contact_enrichment','credit_spend','supplier_application','quote_or_commitment','proof_release')),
  target_system text NOT NULL,
  target_id text NOT NULL,                     -- exact target (CRM id, person id, hypothesis id ...)
  operation text NOT NULL,
  payload jsonb NOT NULL,
  payload_hash text NOT NULL,                  -- sha256 of canonical payload; executor rejects any mismatch
  requested_by text NOT NULL,
  approver text,
  justification text NOT NULL,
  scope jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired','executing','executed','failed','revoked')),
  created_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz,
  expires_at timestamptz,
  execution_id text,
  executed_at timestamptz,
  result jsonb,
  CHECK (approver IS NULL OR approver <> requested_by OR kind IN ('contact_enrichment','credit_spend'))   -- two-person rule for CRM/outbound
);
CREATE INDEX approvals_status_idx ON approvals (status, expires_at);

-- ---------------------------------------------------------------- field-level enrichment evidence (spec 06 §3)
CREATE TABLE enrichment_fields (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  field text NOT NULL,
  stage text NOT NULL CHECK (stage IN ('E1','E2','E3','E4','E5','E6')),
  state text NOT NULL CHECK (state IN ('present','missing','unknown','unavailable','contradicted','not_applicable','stale','not_found_in_provider')),
  value jsonb,
  source_claim_ids uuid[] NOT NULL DEFAULT '{}',
  provider text NOT NULL,
  method text NOT NULL,
  confidence text,
  verification_status text NOT NULL DEFAULT 'unverified',
  observed_at timestamptz NOT NULL DEFAULT now(),
  valid_from timestamptz,
  valid_to timestamptz,
  cost numeric NOT NULL DEFAULT 0,
  manual_override boolean NOT NULL DEFAULT false,
  UNIQUE (entity_type, entity_id, field, provider)
);

-- ---------------------------------------------------------------- research tasks (decision-value gated)
CREATE TABLE research_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_type text NOT NULL,
  target_id text NOT NULL,
  research_question text NOT NULL,
  evidence_gap text NOT NULL,
  decision_effect text NOT NULL,               -- what decision this could change (route / priority / action / disqualify)
  method text NOT NULL,                        -- existing_data | official_api | targeted_retrieval | semantic_search | deep_research | buyer_conversation
  provider text,
  estimated_cost numeric NOT NULL DEFAULT 0,
  max_cost numeric NOT NULL DEFAULT 0,
  approval_required boolean NOT NULL DEFAULT false,
  approval_id uuid REFERENCES approvals(id),
  status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','running','completed','skipped','budget_exceeded','blocked','needs_approval')),
  stop_reason text,
  idempotency_key text NOT NULL UNIQUE,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX research_tasks_target_idx ON research_tasks (target_type, target_id);

-- ---------------------------------------------------------------- CRM mirror (Pipedrive is authoritative; this is a read cache)
CREATE TABLE crm_organizations (
  crm_id bigint PRIMARY KEY,
  organization_id uuid REFERENCES organizations(id),
  name text NOT NULL,
  owner_id bigint,
  owner_name text,
  open_deals_count integer,
  address text,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now(),
  link_status text NOT NULL DEFAULT 'unlinked' CHECK (link_status IN ('linked','review','unlinked'))
);
CREATE INDEX crm_org_org_idx ON crm_organizations (organization_id);
CREATE TABLE crm_persons (
  crm_id bigint PRIMARY KEY,
  person_id uuid REFERENCES people(id),
  crm_org_id bigint,
  name text NOT NULL,
  owner_id bigint,
  email_optout boolean NOT NULL DEFAULT false,
  suppressed boolean NOT NULL DEFAULT false,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE crm_deals (
  crm_id bigint PRIMARY KEY,
  crm_org_id bigint,
  title text NOT NULL,
  owner_id bigint,
  stage_id bigint,
  stage_name text,
  pipeline_id bigint,
  status text,
  value numeric,
  currency text,
  update_time timestamptz,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE crm_activities (
  crm_id bigint PRIMARY KEY,
  crm_org_id bigint,
  crm_deal_id bigint,
  crm_person_id bigint,
  type text,
  subject text,
  done boolean,
  due_at timestamptz,
  done_at timestamptz,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE crm_webhook_inbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dedup_key text NOT NULL UNIQUE,              -- provider event id or composite stable key: replay-safe
  event text NOT NULL,
  payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  status text NOT NULL DEFAULT 'received' CHECK (status IN ('received','processed','quarantined')),
  error text
);
CREATE TABLE sync_state (
  system text NOT NULL,
  entity text NOT NULL,
  watermark text,
  last_full_reconcile_at timestamptz,
  PRIMARY KEY (system, entity)
);

-- ---------------------------------------------------------------- opportunity hypotheses (NOT CRM deals)
CREATE TABLE opportunity_hypotheses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  workstream_id uuid REFERENCES workstreams(id),
  route_id uuid REFERENCES procurement_routes(id),
  route_type text NOT NULL,
  counterparty_org_id uuid REFERENCES organizations(id),
  counterparty_name text NOT NULL,
  organization_id uuid NOT NULL REFERENCES organizations(id),   -- account the seller works (initiative owner)
  possible_service text,
  supporting_claim_ids uuid[] NOT NULL DEFAULT '{}',
  counterevidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  external_demand_status text NOT NULL DEFAULT 'unknown' CHECK (external_demand_status IN ('unknown','inferred','explicit','buyer_confirmed')),
  access_status text NOT NULL DEFAULT 'unknown' CHECK (access_status IN ('unknown','potential','verified','restricted','blocked')),
  buyer_status text NOT NULL DEFAULT 'none' CHECK (buyer_status IN ('none','role_identified','person_verified','buyer_confirmed')),
  critical_unknowns jsonb NOT NULL DEFAULT '[]'::jsonb,
  state text NOT NULL DEFAULT 'observed' CHECK (state IN ('observed','verified','relevant','investigating','actionable_hypothesis','engaged',
     'buyer_need_confirmed','commercially_qualified','proposal_procurement','contracted_delivery','monitor','blocked','disqualified','expired','superseded')),
  prior_active_state text,                     -- where to return when re-opened from monitor/blocked/expired
  gates jsonb NOT NULL DEFAULT '{}'::jsonb,    -- G1..G7: {status: pass|fail|unknown, reason}
  score_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  score_total integer NOT NULL DEFAULT 0,
  evidence_confidence text NOT NULL DEFAULT 'low' CHECK (evidence_confidence IN ('low','medium','high')),
  research_completeness numeric NOT NULL DEFAULT 0,
  negative_signal_severity text NOT NULL DEFAULT 'none' CHECK (negative_signal_severity IN ('none','soft','route','hard')),
  blockers jsonb NOT NULL DEFAULT '[]'::jsonb,
  priority text NOT NULL DEFAULT 'low' CHECK (priority IN ('urgent','high','medium','low','blocked')),
  review_json jsonb,                           -- counterevidence review output
  initiative_revision integer NOT NULL DEFAULT 0,
  scoring_rule_version text NOT NULL DEFAULT '1.0.0',
  last_checked_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (initiative_id, route_type, counterparty_name)
);
CREATE INDEX hypotheses_org_idx ON opportunity_hypotheses (organization_id, state);
CREATE INDEX hypotheses_priority_idx ON opportunity_hypotheses (priority, score_total DESC);

CREATE TABLE opportunity_transitions (
  id bigserial PRIMARY KEY,
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  prior_state text NOT NULL,
  new_state text NOT NULL,
  actor text NOT NULL,
  evidence_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
  checklist jsonb NOT NULL DEFAULT '{}'::jsonb,
  reason text,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER opportunity_transitions_append_only BEFORE UPDATE OR DELETE ON opportunity_transitions FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE recommended_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  owner_id bigint,                              -- Pipedrive user id of the account owner when known
  owner_note text,
  target_role text,
  action_type text NOT NULL,
  objective text NOT NULL,
  reason text NOT NULL,
  evidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  due_at timestamptz,
  trigger_condition text,
  approval_required boolean NOT NULL DEFAULT true,
  expected_outcome text NOT NULL,
  stop_condition text NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','dismissed','done','superseded')),
  dismiss_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (due_at IS NOT NULL OR trigger_condition IS NOT NULL)      -- every action needs a due date or a trigger
);
CREATE UNIQUE INDEX one_active_action_per_hypothesis ON recommended_actions (hypothesis_id) WHERE status IN ('proposed','accepted');

CREATE TABLE new_company_candidates (        -- companies discovered OUTSIDE the master lists: separate qualification workflow
  organization_id uuid PRIMARY KEY REFERENCES organizations(id),
  discovered_via_event_id uuid REFERENCES events(id),
  status text NOT NULL DEFAULT 'pending_qualification' CHECK (status IN ('pending_qualification','qualified','rejected')),
  icp_note text,
  created_at timestamptz NOT NULL DEFAULT now()
);
