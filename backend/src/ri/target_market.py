"""Who is a target account, and who is still being filled in.

There is one list of companies. Every company the workspace knows from the lead list, the workbooks, research or a person is in
exactly one of three states:

* **target account** (``target_sql()``): the lead list marks it "Eligible"; or the lead list marks it "Review", or it is not on the list,
  and its facts fit. Facts fit = privately held, listed, or a mutual or cooperative, with the page that says so; financial services; a
  known country; more than 500 staff in Europe (2,000 elsewhere). State-owned companies and public bodies are never targets.
* **still being filled in** (``target_sql(review=True)``): nothing known rules it out, but a fact is missing (ownership, headcount,
  country). Exa looks these up automatically (``research.company_facts``) and the company becomes a target account as soon as its facts
  fit. Lead-list "Review" rows, and financial companies found by research that are not on the list.
* **not a fit** (``not_fit_sql()``): every other company on the lead list or in the workbooks (marked "Out of scope" by the list,
  state-owned, too small, not financial services, or removed by a person). ``not_fit_reason`` says which, in a word the page translates.

The lead list's own "Eligible" stays decisive: research never takes a company the list chose off the target list.
"""
from __future__ import annotations

from typing import Any

from .market import CORE, EUROPE, region_for, sector_sql

TARGET_REGIONS = [
    ("uk", "United Kingdom"), ("dach", "DACH"), ("nordics", "Nordics"),
    ("poland", "Poland"), ("europe", "Rest of Europe"), ("international", "International"),
]
TARGET_REGION_ORDER = {key: index for index, (key, _) in enumerate(TARGET_REGIONS)}
EUROPE_CODES = frozenset(CORE | EUROPE)
_EUROPE = ", ".join(f"'{code}'" for code in sorted(EUROPE_CODES))
# Ownership that is fine for a target account. State-owned (and unknown) is not.
OK_OWNERSHIP = ("private", "listed", "mutual_or_cooperative")
OWNERSHIP_VALUES = ("unknown", *OK_OWNERSHIP, "state_owned")
MIN_STAFF_EUROPE, MIN_STAFF_ELSEWHERE = 500, 2000
_OK = ", ".join(f"'{v}'" for v in OK_OWNERSHIP)


# These aliases are fixed SQL identifiers supplied by internal call sites.
def commercial_sql(alias: str = "o") -> str:
    return (
        f"({alias}.entity_type IN ('company','group','subsidiary') "
        f"AND concat_ws(' ', {alias}.display_name, {alias}.legal_name) "
        r"!~* '\m(zus|zakład ubezpieczeń społecznych|social insurance institution)\M' "
        f"AND coalesce({alias}.canonical_domain, '') !~* '(^|[.])zus[.]pl$')"
    )


def _kept(alias: str) -> str:
    """Not removed by a person (a research find someone rejected)."""
    return f"NOT EXISTS (SELECT 1 FROM new_company_candidates nc WHERE nc.organization_id={alias}.id AND nc.status='rejected')"


def country_ok_sql(alias: str = "o") -> str:
    return f"coalesce({alias}.country_code ~ '^[A-Z][A-Z]$' AND {alias}.country_code NOT IN ('XX','ZZ','EU'), false)"


def min_staff_sql(alias: str = "o") -> str:
    return f"CASE WHEN {alias}.country_code IN ({_EUROPE}) THEN {MIN_STAFF_EUROPE} ELSE {MIN_STAFF_ELSEWHERE} END"


def facts_fit_sql(alias: str = "o") -> str:
    """Every fact a target needs is known and fits: ownership with its source, financial services, the country, the size. Never NULL."""
    return (
        f"coalesce({alias}.company_ownership IN ({_OK}) AND {alias}.company_profile_source_url IS NOT NULL "
        f"AND ({sector_sql(alias + '.segment')}) = 'finance' AND {country_ok_sql(alias)} "
        f"AND {alias}.employees_estimate > {min_staff_sql(alias)}, false)"
    )


def could_fit_sql(alias: str = "o") -> str:
    """Nothing we know rules it out yet: not state-owned, not outside financial services, not known to be too small. Never NULL."""
    return (
        f"coalesce({alias}.company_ownership <> 'state_owned' AND ({sector_sql(alias + '.segment')}) IN ('finance','unclassified') "
        f"AND ({alias}.employees_estimate IS NULL OR {alias}.employees_estimate > "
        f"CASE WHEN {country_ok_sql(alias)} THEN {min_staff_sql(alias)} ELSE {MIN_STAFF_EUROPE} END), false)"
    )


def target_sql(alias: str = "o", *, review: bool = False) -> str:
    """``review=False``: target accounts. ``review=True``: companies still being filled in (they could fit; a fact is missing).
    Never NULL: callers read it as a flag."""
    if review:
        waiting = (
            f"(coalesce({alias}.list_eligibility, '') = 'Review' OR ({alias}.list_eligibility IS NULL "
            f"AND ({sector_sql(alias + '.segment')}) = 'finance'))"
        )
        return f"({waiting} AND NOT {facts_fit_sql(alias)} AND {could_fit_sql(alias)} AND {commercial_sql(alias)} AND {_kept(alias)})"
    chosen = (
        f"(coalesce({alias}.list_eligibility, '') = 'Eligible' OR ((coalesce({alias}.list_eligibility, 'Review') = 'Review') "
        f"AND {facts_fit_sql(alias)}))"
    )
    return f"({chosen} AND {commercial_sql(alias)} AND {_kept(alias)})"


def _in_workbook(alias: str) -> str:
    # uncorrelated, so Postgres hashes the list once instead of scanning the workbook rows for every company
    return (f"{alias}.id IN (SELECT r.organization_id FROM legacy_account_research r "
            f"WHERE r.kind IN ('priority_outreach','master_account'))")


def not_fit_sql(alias: str = "o") -> str:
    """Companies on the lead list or in the workbooks that are neither target accounts nor being filled in."""
    return (
        f"({commercial_sql(alias)} AND ({alias}.list_eligibility IS NOT NULL OR {_in_workbook(alias)}) "
        f"AND NOT {target_sql(alias)} AND NOT {target_sql(alias, review=True)})"
    )


def state_sql(alias: str = "o") -> str:
    """'target', 'review' (being filled in), 'other' (not a fit) or NULL (not in the workspace)."""
    return (f"CASE WHEN {target_sql(alias)} THEN 'target' WHEN {target_sql(alias, review=True)} THEN 'review' "
            f"WHEN {commercial_sql(alias)} AND ({alias}.list_eligibility IS NOT NULL OR {_in_workbook(alias)}) THEN 'other' END")


def workbook_company_sql(alias: str = "o") -> str:
    """Allow users to see their imported commercial records before qualification (state-owned companies stay out)."""
    return f"({commercial_sql(alias)} AND {alias}.company_ownership <> 'state_owned' AND {_kept(alias)})"


def account_workspace_sql(alias: str = "o", *, research: bool = False) -> str:
    """Every company the Accounts page can list: target accounts, those being filled in, and the rest of the lead list and workbooks."""
    if research:
        return commercial_sql(alias)
    return (
        f"({target_sql(alias)} OR {target_sql(alias, review=True)} OR ({commercial_sql(alias)} AND "
        f"({alias}.list_eligibility IS NOT NULL OR {_in_workbook(alias)})))"
    )


def target_region(country: str | None) -> str:
    region = region_for(country)
    return "international" if region in {"us", "other"} else region


def _country_ok(code: Any) -> bool:
    return isinstance(code, str) and len(code) == 2 and code.isalpha() and code.isupper() and code not in {"XX", "ZZ", "EU"}


def min_staff(country: Any) -> int:
    return MIN_STAFF_EUROPE if country in EUROPE_CODES else MIN_STAFF_ELSEWHERE


def missing_facts(r: dict[str, Any]) -> list[str]:
    """What a company still being filled in lacks, as codes the page turns into words: ownership, employees, country, sector, website."""
    out = []
    if r.get("company_ownership") not in OK_OWNERSHIP or not r.get("company_profile_source_url"):
        out.append("ownership")
    if r.get("employees") is None:
        out.append("employees")
    if not _country_ok(r.get("country_code")):
        out.append("country")
    if r.get("sector") == "unclassified":
        out.append("sector")
    if not r.get("canonical_domain"):
        out.append("website")
    return out


def not_fit_reason(r: dict[str, Any]) -> str:
    """Why a company on the list is not a target account, as a code the page turns into words."""
    if r.get("candidate_status") == "rejected":
        return "removed"
    if r.get("list_eligibility") == "Out of scope":
        return "list_out_of_scope"
    if r.get("company_ownership") == "state_owned":
        return "state_owned"
    if r.get("sector") == "other":
        return "not_finance"
    employees = r.get("employees")
    if employees is not None:
        country = r.get("country_code")
        if employees <= MIN_STAFF_EUROPE or (_country_ok(country) and employees <= min_staff(country)):
            return "too_small" if employees <= MIN_STAFF_EUROPE else "too_small_outside_europe"
    if r.get("sector") == "unclassified" and r.get("list_eligibility") is None:
        return "sector_unknown"
    return "other"
