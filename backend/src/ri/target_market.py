"""Eligibility for the private financial services sales workspace."""
from __future__ import annotations

from .market import CORE, EUROPE, region_for, sector_sql

TARGET_REGIONS = [
    ("uk", "United Kingdom"), ("dach", "DACH"), ("nordics", "Nordics"),
    ("poland", "Poland"), ("europe", "Rest of Europe"), ("international", "International"),
]
TARGET_REGION_ORDER = {key: index for index, (key, _) in enumerate(TARGET_REGIONS)}
_EUROPE = ", ".join(f"'{code}'" for code in sorted(CORE | EUROPE))

# These aliases are fixed SQL identifiers supplied by internal call sites.
def commercial_sql(alias: str = "o") -> str:
    return (
        f"({alias}.entity_type IN ('company','group','subsidiary') "
        f"AND concat_ws(' ', {alias}.display_name, {alias}.legal_name) "
        r"!~* '\m(zus|zakład ubezpieczeń społecznych|social insurance institution)\M' "
        f"AND coalesce({alias}.canonical_domain, '') !~* '(^|[.])zus[.]pl$')"
    )


def target_sql(alias: str = "o", *, review: bool = False) -> str:
    """Companies on the current lead list follow the list's own Eligibility column ("Eligible" are targets, "Review" wait for a
    check, "Out of scope" are neither). Companies not on the list (found by research or added by hand) must be privately held with
    a source, in financial services, and over 500 staff in Europe or 2,000 elsewhere."""
    ownership = f"{alias}.company_ownership = 'unknown'" if review else (
        f"{alias}.company_ownership = 'private' AND {alias}.company_profile_source_url IS NOT NULL"
    )
    by_rules = (
        f"{alias}.list_eligibility IS NULL AND {ownership} "
        f"AND ({sector_sql(alias + '.segment')}) = 'finance' "
        f"AND {alias}.country_code ~ '^[A-Z][A-Z]$' AND {alias}.country_code NOT IN ('XX','ZZ','EU') "
        f"AND {alias}.employees_estimate > CASE WHEN {alias}.country_code IN ({_EUROPE}) THEN 500 ELSE 2000 END"
    )
    by_list = f"coalesce({alias}.list_eligibility, '') = '{'Review' if review else 'Eligible'}'"     # never NULL: callers read it as a flag
    return (
        f"((({by_rules}) OR ({by_list})) AND {commercial_sql(alias)} "
        f"AND NOT EXISTS (SELECT 1 FROM new_company_candidates nc "
        f"WHERE nc.organization_id={alias}.id AND nc.status='rejected'))"
    )


def workbook_company_sql(alias: str = "o") -> str:
    """Allow users to see their imported commercial records before qualification."""
    return (
        f"({commercial_sql(alias)} AND {alias}.company_ownership IN ('unknown','private') "
        f"AND NOT EXISTS (SELECT 1 FROM new_company_candidates nc "
        f"WHERE nc.organization_id={alias}.id AND nc.status='rejected'))"
    )


def account_workspace_sql(alias: str = "o", *, research: bool = False) -> str:
    if research:
        return commercial_sql(alias)
    return (
        f"({target_sql(alias)} OR {target_sql(alias, review=True)} OR ({workbook_company_sql(alias)} AND "
        f"EXISTS (SELECT 1 FROM legacy_account_research r WHERE r.organization_id={alias}.id "
        f"AND r.kind IN ('priority_outreach','master_account'))))"
    )


def target_region(country: str | None) -> str:
    region = region_for(country)
    return "international" if region in {"us", "other"} else region
