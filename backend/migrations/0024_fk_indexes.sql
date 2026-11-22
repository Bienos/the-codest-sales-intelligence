-- Indexes for foreign keys that the app joins or filters on (flagged by the database advisor). IF NOT EXISTS keeps it safe to re-run.
CREATE INDEX IF NOT EXISTS event_documents_document_idx ON event_documents (document_id);
CREATE INDEX IF NOT EXISTS events_origin_document_idx ON events (origin_document_id);
CREATE INDEX IF NOT EXISTS initiative_stage_history_initiative_idx ON initiative_stage_history (initiative_id);
CREATE INDEX IF NOT EXISTS opportunity_transitions_hypothesis_idx ON opportunity_transitions (hypothesis_id);
CREATE INDEX IF NOT EXISTS outcome_events_hypothesis_idx ON outcome_events (hypothesis_id);
CREATE INDEX IF NOT EXISTS hypothesis_user_state_user_idx ON hypothesis_user_state (user_id);
CREATE INDEX IF NOT EXISTS outbound_enrolments_person_idx ON outbound_enrolments (person_id);
CREATE INDEX IF NOT EXISTS outbound_events_enrolment_idx ON outbound_events (enrolment_id);
CREATE INDEX IF NOT EXISTS contact_candidates_person_idx ON contact_candidates (person_id);
CREATE INDEX IF NOT EXISTS organization_edges_from_idx ON organization_edges (from_org);
CREATE INDEX IF NOT EXISTS organization_edges_to_idx ON organization_edges (to_org);
CREATE INDEX IF NOT EXISTS supplier_edges_supplier_idx ON supplier_edges (supplier_org_id);
CREATE INDEX IF NOT EXISTS initiative_organizations_org_idx ON initiative_organizations (organization_id);
CREATE INDEX IF NOT EXISTS job_postings_org_idx ON job_postings (organization_id);
CREATE INDEX IF NOT EXISTS procurement_notices_buyer_idx ON procurement_notices (buyer_org_id);
