-- R2 Initiative graph: initiatives, workstreams, scoped supplier edges, route hypotheses, duplicate review.
-- Spec refs: 05 §2-§12, 04 §3 (initiatives/workstreams/supplier_edges/procurement_routes).
CREATE TABLE initiatives (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_org_id uuid NOT NULL REFERENCES organizations(id),   -- the exact legal entity; parent announcement != subsidiary procurement
  title text NOT NULL,
  title_is_official boolean NOT NULL DEFAULT false,          -- false => 'analyst working title' (shown as such)
  programme_domain text,
  business_objective jsonb,
  technical_objective jsonb,
  stage text NOT NULL DEFAULT 'candidate' CHECK (stage IN
    ('candidate','announced','planning','sourcing','implementation','transition_or_launch','completed','paused','cancelled','scope_unknown')),
  stage_evidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  stage_verified_at timestamptz,
  next_milestone_at timestamptz,
  last_material_event_at timestamptz,
  technology_tags text[] NOT NULL DEFAULT '{}',
  aliases text[] NOT NULL DEFAULT '{}',
  research_gaps jsonb NOT NULL DEFAULT '[]'::jsonb,
  review_status text NOT NULL DEFAULT 'research_needed' CHECK (review_status IN ('research_needed','reviewed','disputed')),
  budget_verified boolean NOT NULL DEFAULT false,
  revision integer NOT NULL DEFAULT 1,                       -- bumps on material change; hypotheses re-evaluate on bump
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','merged','split','superseded')),
  merged_into uuid REFERENCES initiatives(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX initiatives_owner_idx ON initiatives (owner_org_id, status);

CREATE TABLE initiative_events (
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  event_id uuid NOT NULL REFERENCES events(id),
  link_strength text NOT NULL CHECK (link_strength IN ('confirmed_link','probable_link','candidate_link','rejected_link')),
  role text NOT NULL DEFAULT 'evidence' CHECK (role IN ('evidence','capacity_evidence','counterevidence')),
  reason text,
  caveat text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (initiative_id, event_id)
);
CREATE INDEX initiative_events_event_idx ON initiative_events (event_id);

CREATE TABLE initiative_organizations (
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  organization_id uuid REFERENCES organizations(id),
  name text NOT NULL,
  role text NOT NULL,
  PRIMARY KEY (initiative_id, name, role)
);

CREATE TABLE workstreams (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  kind text NOT NULL,                                        -- ontology key, e.g. core_banking_customisation, payment_rails
  scope_statement text NOT NULL,
  scope_confidence text NOT NULL CHECK (scope_confidence IN ('confirmed_scope','implied_engineering_activity','inferred_possible_external')),
  delivery_stage text,
  needed_capabilities text[] NOT NULL DEFAULT '{}',
  owner_org_id uuid REFERENCES organizations(id),
  resource_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,      -- hiring clusters: capacity evidence WITH caveat
  milestones jsonb NOT NULL DEFAULT '[]'::jsonb,
  blocking_constraints jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (initiative_id, kind)
);

CREATE TABLE supplier_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_org_id uuid REFERENCES organizations(id),
  supplier_name text NOT NULL,
  buyer_org_id uuid NOT NULL REFERENCES organizations(id),   -- exact buyer entity; never inherited by a subsidiary/parent
  initiative_id uuid REFERENCES initiatives(id),
  workstream_id uuid REFERENCES workstreams(id),
  supplier_role text NOT NULL,                               -- vendor | si | prime | partner
  scope_text text,
  relation text NOT NULL CHECK (relation IN ('selected','contracted','mentioned','proposed')),
  exclusivity text NOT NULL DEFAULT 'unknown' CHECK (exclusivity IN ('unknown','exclusive','partial','none')),
  status text NOT NULL DEFAULT 'announced' CHECK (status IN ('announced','completed','proposed')),
  claim_id uuid REFERENCES claims(id),
  event_id uuid REFERENCES events(id),
  valid_from timestamptz,
  valid_to timestamptz,
  UNIQUE (buyer_org_id, initiative_id, supplier_name, supplier_role)
);

CREATE TABLE procurement_routes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  workstream_id uuid REFERENCES workstreams(id),
  route_type text NOT NULL CHECK (route_type IN ('buyer_direct','vendor_implementation','prime_subcontract','framework_panel','msp_vms','supplier_registration','relationship')),
  counterparty_org_id uuid REFERENCES organizations(id),
  counterparty_name text NOT NULL,
  access_status text NOT NULL DEFAULT 'unknown' CHECK (access_status IN ('unknown','potential','verified','restricted','blocked')),
  restriction text,
  portal_exists boolean,
  procurement_open boolean,                                  -- portal_exists and procurement_open are SEPARATE facts
  evidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  blocked_by_negative_id uuid REFERENCES negative_findings(id),
  last_verified_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (initiative_id, route_type, counterparty_name)
);

CREATE TABLE initiative_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('possible_duplicate_initiative','possible_split')),
  initiative_a uuid NOT NULL REFERENCES initiatives(id),
  initiative_b uuid REFERENCES initiatives(id),
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','merged','kept_separate','split')),
  reviewer text,
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
CREATE UNIQUE INDEX initiative_reviews_pair_open ON initiative_reviews (initiative_a, initiative_b) WHERE status = 'open';

CREATE TABLE initiative_stage_history (
  id bigserial PRIMARY KEY,
  initiative_id uuid NOT NULL REFERENCES initiatives(id),
  from_stage text, to_stage text NOT NULL,
  event_id uuid REFERENCES events(id),
  actor text NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);
