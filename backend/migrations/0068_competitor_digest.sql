-- Monday competitor digest (ri/competitor_digest.py): one message per recipient per ISO week, recorded here so a second scheduler tick never repeats it.
ALTER TABLE notifications_sent DROP CONSTRAINT notifications_sent_kind_check;
ALTER TABLE notifications_sent ADD CONSTRAINT notifications_sent_kind_check CHECK (kind IN ('daily_digest','urgent_tender','source_failure','competitor_digest'));
