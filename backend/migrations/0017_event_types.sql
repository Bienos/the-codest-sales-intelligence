-- Three event types for business events that create new work (signal group COM): market launches, new licences, cost programmes.
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_event_type_check;
ALTER TABLE events ADD CONSTRAINT events_event_type_check CHECK (event_type = ANY (ARRAY[
 'procurement_published','procurement_amended','procurement_awarded','vendor_selected','implementation_launched','programme_announced','programme_completed',
 'integration_release','product_launch','engineering_job_opened','contractor_job_opened','job_closed','technology_leader_appointed','acquisition_announced',
 'funding_announced','supplier_panel_opened','supplier_panel_closed','regulatory_change','deadline_approaching','crm_buyer_response','relationship_changed',
 'negative_cancellation','programme_delayed','supplier_exit','market_launch','licence_granted','cost_programme']));
