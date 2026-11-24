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
    ownership = f"{alias}.company_ownership = 'unknown'" if review else (
        f"{alias}.company_ownership = 'private' AND {alias}.company_profile_source_url IS NOT NULL"
    )
    return (
        f"({ownership} AND {commercial_sql(alias)} "
        f"AND ({sector_sql(alias + '.segment')}) = 'finance' "
        f"AND {alias}.country_code ~ '^[A-Z][A-Z]$' AND {alias}.country_code NOT IN ('XX','ZZ','EU') "
        f"AND {alias}.employees_estimate > CASE WHEN {alias}.country_code IN ({_EUROPE}) THEN 500 ELSE 2000 END "
        f"AND NOT EXISTS (SELECT 1 FROM new_company_candidates nc "
        f"WHERE nc.organization_id={alias}.id AND nc.status='rejected'))"
    )


def target_region(country: str | None) -> str:
    region = region_for(country)
    return "international" if region in {"us", "other"} else region
