-- R5 Buyer access graph, sales plays, proof governance, drafts, procurement tracking, outcomes.
CREATE TABLE proof_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title text NOT NULL,
  service_tags text[] NOT NULL DEFAULT '{}',
  approved_audience text NOT NULL CHECK (approved_audience IN ('external_approved','limited_audience','internal_only','restricted')),
  client_name text,                                  -- NEVER emitted into an external draft unless external_approved
  approval_owner text,
  approved_by text,
  source_doc text,
  effective_date date,
  restrictions text,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','withdrawn')),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (approved_audience <> 'external_approved' OR (approved_by IS NOT NULL AND approval_owner IS NOT NULL))
);
CREATE TABLE buyer_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  role text NOT NULL CHECK (role IN ('problem_owner','programme_owner','economic_buyer','technical_evaluator','procurement_owner','risk_security','champion','prime_partner')),
  person_id uuid REFERENCES people(id),
  role_title text,                                   -- role-first: a title without a person is a valid state
  role_is_verified boolean NOT NULL DEFAULT false,
  current_role_verified_at timestamptz,
  initiative_responsibility_verified boolean NOT NULL DEFAULT false,
  buyer_authority_inferred boolean NOT NULL DEFAULT true,   -- title never proves budget authority
  contactability text NOT NULL DEFAULT 'unknown' CHECK (contactability IN ('unknown','contactable','restricted','suppressed')),
  evidence_claim_ids uuid[] NOT NULL DEFAULT '{}',
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hypothesis_id, role, person_id)
);
CREATE TABLE access_routes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  kind text NOT NULL CHECK (kind IN ('existing_crm_relationship','buyer_confirmed_owner','official_procurement_channel','verified_prime_or_vendor_route','introduction_via_intermediary','targeted_functional_outreach','strategic_familiarity','public_event_context')),
  verified boolean NOT NULL DEFAULT false,
  person_id uuid REFERENCES people(id),
  intermediary text,
  evidence text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE procurement_tracking (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  item text NOT NULL CHECK (item IN ('eligibility_documents','security_review','decision_makers','internal_approval','legal_review','submission','contracting')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','blocked','done','not_applicable')),
  due_at timestamptz,
  owner text,
  notes text,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (hypothesis_id, item)
);
CREATE TABLE outreach_drafts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  person_id uuid REFERENCES people(id),
  play_id text NOT NULL,
  subject text NOT NULL,
  body text NOT NULL,
  proof_asset_ids uuid[] NOT NULL DEFAULT '{}',
  source_claim_ids uuid[] NOT NULL DEFAULT '{}',
  blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL CHECK (status IN ('draft','blocked')),   -- there is deliberately NO 'sent' status: sending is outside this system
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outcome_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  kind text NOT NULL CHECK (kind IN ('recommendation_accepted','meaningful_conversation','discovery_held','buyer_confirmed_need','supplier_route_validated','procurement_validated',
                                      'qualified_crm_opportunity','signed','delivery_started','revenue_observed','lost','no_decision')),
  crm_ref text,                                        -- Pipedrive deal/activity id: outcomes trace to CRM IDs (FR-016)
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  contributions jsonb NOT NULL DEFAULT '[]'::jsonb,    -- transparent multi-touch record: [{kind: source|signal|route, ref}] (attribution, not causation)
  recorded_by text NOT NULL,
  at timestamptz NOT NULL DEFAULT now(),
  CHECK (kind NOT IN ('qualified_crm_opportunity','signed','revenue_observed','delivery_started') OR crm_ref IS NOT NULL)
);
CREATE INDEX outcome_events_kind_idx ON outcome_events (kind, at);
