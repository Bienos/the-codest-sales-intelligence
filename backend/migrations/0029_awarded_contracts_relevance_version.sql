-- Which version of the relevance rule decided each award's flag, so a stricter rule can be re-applied to everything already stored.
ALTER TABLE awarded_contracts ADD COLUMN relevance_v text;
