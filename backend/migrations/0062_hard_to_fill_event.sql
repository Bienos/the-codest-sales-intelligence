-- One more event type (round 13): job_hard_to_fill, made once a day from stored job ads (src/ri/signals/hiring.py) when an engineering ad at
-- one of our accounts is still listed after 60 days, or the same role is posted again after an earlier ad closed. The CAP-03 rule turns it
-- into a hiring signal. The list below is the one from 0017 plus this type; nothing else changes and no row is rewritten.
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_event_type_check;
ALTER TABLE events ADD CONSTRAINT events_event_type_check CHECK (event_type = ANY (ARRAY[
 'procurement_published','procurement_amended','procurement_awarded','vendor_selected','implementation_launched','programme_announced','programme_completed',
 'integration_release','product_launch','engineering_job_opened','contractor_job_opened','job_closed','technology_leader_appointed','acquisition_announced',
 'funding_announced','supplier_panel_opened','supplier_panel_closed','regulatory_change','deadline_approaching','crm_buyer_response','relationship_changed',
 'negative_cancellation','programme_delayed','supplier_exit','market_launch','licence_granted','cost_programme','job_hard_to_fill']));
