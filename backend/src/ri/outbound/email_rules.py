"""May we cold email this person? The country rule from ``rules/email_rules.yaml``, checked before any address goes to Lemlist.

The country is the company's headquarters country on the lead list: people records hold no country of their own. A country the file does not
list, and a company without a country, count as "not checked", so nobody there is emailed until a person adds the country (fail closed).
A person's recorded consent (``people.email_consent_at``) lifts any rule. Replies to someone who wrote to us are not cold email and are
not checked here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[3] / "rules" / "email_rules.yaml"
RULES = ("allowed", "consent", "consent_personal")
_CODE = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class Rules:
    countries: dict[str, dict[str, Any]]
    generic: frozenset[str]
    instead: str
    instead_pl: str


def parse(raw: dict[str, Any]) -> Rules:
    countries: dict[str, dict[str, Any]] = {}
    for code, c in (raw.get("countries") or {}).items():
        code = str(code).strip().upper()
        if not _CODE.match(code):
            raise ValueError(f"email rules: {code!r} is not a two-letter country code")
        if not isinstance(c, dict) or c.get("rule") not in RULES:
            raise ValueError(f"email rules: {code} needs a rule, one of {RULES}")
        if not c.get("name") or not c.get("note") or not c.get("sources") or any(not str(s).startswith("https://") for s in c["sources"]):
            raise ValueError(f"email rules: {code} needs a name, a note and https sources")
        countries[code] = {"rule": c["rule"], "name": str(c["name"]), "name_pl": str(c.get("name_pl") or c["name"]), "note": str(c["note"]),
                           "note_pl": str(c.get("note_pl") or c["note"]), "sources": [str(s) for s in c["sources"]]}
    generic = frozenset(str(g).strip().casefold() for g in raw.get("generic_local_parts") or [] if str(g).strip())
    return Rules(countries, generic, str(raw.get("instead") or ""), str(raw.get("instead_pl") or ""))


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> Rules:
    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def load(path: Path = PATH) -> Rules:
    return _load(str(path), path.stat().st_mtime)


def is_generic(email: str | None, rules: Rules | None = None) -> bool:
    """info@, sales@ and the like: a role address rather than a person's own."""
    local = (email or "").split("@", 1)[0].strip().casefold()
    return bool(local) and local in (rules or load()).generic


def rule_for(country: str | None, rules: Rules | None = None) -> dict[str, Any]:
    """The rule for one country, in a shape the pages can show as it is."""
    r = rules or load()
    code = (country or "").strip().upper()
    c = r.countries.get(code)
    if not c:
        if not code:
            note = "We do not know which country's rules apply: the company has no country on the lead list. Set it on the company page."
            note_pl = "Nie wiemy, które przepisy obowiązują: firma nie ma kraju na liście leadów. Ustaw go na stronie firmy."
        else:
            note = f"The email rules for {code} have not been checked yet. Ask for them to be added to rules/email_rules.yaml."
            note_pl = f"Zasad wysyłki e-maili dla {code} jeszcze nie sprawdzono. Poproś o dodanie ich do rules/email_rules.yaml."
        return {"country": code or None, "name": code or None, "name_pl": code or None, "rule": "not_checked", "note": note, "note_pl": note_pl,
                "sources": [], "instead": r.instead, "instead_pl": r.instead_pl}
    return {"country": code, **c, "instead": r.instead, "instead_pl": r.instead_pl}


def check(country: str | None, email: str | None, consent_at: Any = None, rules: Rules | None = None) -> tuple[bool, str | None]:
    """(may we cold email this address, the reason code when not). Reason codes: not_checked, needs_consent."""
    r = rules or load()
    rule = rule_for(country, r)["rule"]
    if rule == "allowed" or consent_at:                      # recorded consent is enough under every rule
        return True, None
    if rule == "consent_personal" and is_generic(email, r):
        return True, None
    return False, "not_checked" if rule == "not_checked" else "needs_consent"


def blocked_reason(rule: dict[str, Any], count: int) -> str:
    """One plain sentence for an account where everyone with an address is held back by the country rule."""
    who = "nobody here has" if count != 1 else "the one contact here has not"
    if rule["rule"] == "not_checked":
        return f"{rule['note']} {rule['instead']}"
    return f"Cold email to {rule['name']} needs the person's prior consent, and {who} given it. {rule['instead']}"


def consent_sql(person: str = "pe", org: str = "o", rules: Rules | None = None) -> str:
    """SQL twin of ``check`` for counts ("accounts with someone we may email"). Country codes come from the rules file and are checked to be two
    capital letters, so they are safe to inline."""
    r = rules or load()
    allowed = sorted(k for k, v in r.countries.items() if v["rule"] == "allowed")
    personal = sorted(k for k, v in r.countries.items() if v["rule"] == "consent_personal")
    generic = sorted(g for g in r.generic if re.match(r"^[a-z0-9._-]+$", g))

    def lst(xs: list[str]) -> str:
        return ", ".join(f"'{x}'" for x in xs) or "NULL"

    return (f"(upper(coalesce({org}.country_code, '')) IN ({lst(allowed)}) OR {person}.email_consent_at IS NOT NULL "
            f"OR (upper(coalesce({org}.country_code, '')) IN ({lst(personal)}) AND lower(split_part({person}.work_email, '@', 1)) IN ({lst(generic)})))")
