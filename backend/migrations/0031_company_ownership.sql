ALTER TABLE organizations
  ADD COLUMN company_ownership text NOT NULL DEFAULT 'unknown'
    CHECK (company_ownership IN ('unknown', 'private', 'listed', 'state_owned')),
  ADD COLUMN company_profile_source_url text;
