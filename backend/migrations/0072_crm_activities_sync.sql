-- Pipedrive activities (calls, meetings, tasks, emails logged in Pipedrive) are mirrored hourly into crm_activities, which existed but was
-- never filled. Who owns each activity and when it last changed, so the hygiene board can show each seller's next step and last contact
-- per deal. Notes, participants and attendees are not stored. Additive and safe to re-run.
ALTER TABLE crm_activities ADD COLUMN IF NOT EXISTS owner_id bigint;
ALTER TABLE crm_activities ADD COLUMN IF NOT EXISTS update_time timestamptz;
CREATE INDEX IF NOT EXISTS crm_activities_deal_idx ON crm_activities (crm_deal_id, done, due_at);
CREATE INDEX IF NOT EXISTS crm_activities_org_idx ON crm_activities (crm_org_id, done_at DESC);
