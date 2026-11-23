-- A published tender is public procurement whoever the buyer is: every tender-derived event moves to the public lane.
UPDATE events SET lane = 'public' WHERE lane <> 'public' AND (event_type LIKE 'procurement%' OR (event_type = 'negative_cancellation' AND attrs ? 'notice_id'));
-- Recent private-lane events are re-tagged with the extended list of public-body words (schools, courts, hospitals, ministries, central banks);
-- the scheduler's tagging step does this in batches.
UPDATE events SET market_tagged_at = NULL WHERE lane = 'private' AND first_observed_at > now() - interval '120 days';
