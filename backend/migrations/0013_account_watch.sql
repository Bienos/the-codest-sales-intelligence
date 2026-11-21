-- Per-account discovery plans (the lead monitor): each shortlisted account gets its own weekly search.
ALTER TABLE discovery_plans ADD COLUMN IF NOT EXISTS organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS discovery_plans_org_idx ON discovery_plans (organization_id);
