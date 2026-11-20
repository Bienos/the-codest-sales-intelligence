-- R4 Sales platform: per-user decisions (dismissals never resurface unchanged), seen-change tracking, notifications, account shares.
CREATE TABLE hypothesis_user_state (
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  user_id uuid NOT NULL REFERENCES users(id),
  decision text NOT NULL CHECK (decision IN ('accepted','dismissed','monitor')),
  reason text,                                  -- mandatory for dismiss (enforced in API + CHECK below)
  note text,
  fingerprint text NOT NULL,                    -- material-state hash at decision time
  decided_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (hypothesis_id, user_id),
  CHECK (decision <> 'dismissed' OR (reason IS NOT NULL AND length(trim(reason)) > 0))
);
CREATE TABLE hypothesis_seen (
  hypothesis_id uuid NOT NULL REFERENCES opportunity_hypotheses(id),
  fingerprint text NOT NULL,
  first_presented_at timestamptz NOT NULL DEFAULT now(),
  snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,   -- for before/after material-change display
  PRIMARY KEY (hypothesis_id, fingerprint)
);
CREATE TABLE account_shares (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  user_id uuid NOT NULL REFERENCES users(id),
  granted_by text NOT NULL,
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id)
);
CREATE TABLE notifications_sent (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dedup_key text NOT NULL UNIQUE,               -- account+initiative+material_change+recipient+period (spec 13 §7)
  recipient text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('daily_digest','urgent_tender','source_failure')),
  channel text NOT NULL,
  hypothesis_id uuid,
  payload_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL CHECK (status IN ('sent','failed','blocked')),
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE product_events (                   -- spec 01 §8 instrumentation (no unnecessary contact PII)
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  user_id uuid REFERENCES users(id),
  event text NOT NULL,
  hypothesis_id uuid,
  organization_id uuid,
  rule_version text,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX product_events_event_idx ON product_events (event, at);
