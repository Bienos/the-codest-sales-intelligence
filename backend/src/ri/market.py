"""Where an event matters and for whom.

Main markets (UK, DACH, Nordics, Poland) come first. The rest of Europe and the US are a secondary view, not hidden.
Public-sector buyers (ministries, councils, agencies, hospitals, universities) are kept in their own lane: your audiences are scale-ups, enterprises and banks.
"""
from __future__ import annotations

import re

CORE = {"GB", "DE", "AT", "CH", "SE", "NO", "DK", "FI", "IS", "PL"}
EUROPE = {"AL", "AD", "AM", "BE", "BA", "BG", "BY", "CY", "CZ", "EE", "ES", "FR", "GE", "GR", "HR", "HU", "IE", "IT", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT",
          "NL", "PT", "RO", "RS", "SI", "SK", "SM", "TR", "UA", "XK", "VA"}
SECONDARY = EUROPE | {"US"}

ALPHA3 = {"GBR": "GB", "DEU": "DE", "AUT": "AT", "CHE": "CH", "SWE": "SE", "NOR": "NO", "DNK": "DK", "FIN": "FI", "ISL": "IS", "POL": "PL", "USA": "US",
          "ALB": "AL", "AND": "AD", "ARM": "AM", "BEL": "BE", "BIH": "BA", "BGR": "BG", "BLR": "BY", "CYP": "CY", "CZE": "CZ", "EST": "EE", "ESP": "ES", "FRA": "FR", "GEO": "GE",
          "GRC": "GR", "HRV": "HR", "HUN": "HU", "IRL": "IE", "ITA": "IT", "LIE": "LI", "LTU": "LT", "LUX": "LU", "LVA": "LV", "MCO": "MC", "MDA": "MD", "MNE": "ME", "MKD": "MK",
          "MLT": "MT", "NLD": "NL", "PRT": "PT", "ROU": "RO", "SRB": "RS", "SVN": "SI", "SVK": "SK", "SMR": "SM", "TUR": "TR", "UKR": "UA", "UNK": "XK"}


def alpha2(code: str | None) -> str | None:
    c = (code or "").strip().upper()
    if len(c) == 2:
        return c
    return ALPHA3.get(c)


def tier_for(country: str | None) -> str:
    c = alpha2(country)
    if not c:
        return "unknown"
    if c in CORE:
        return "core"
    if c in SECONDARY:
        return "secondary"
    return "other"


# ministries, councils, agencies, hospitals, universities, police and similar, in the languages of the markets above
_STRONG = re.compile(
    r"\b(ministry|ministerium|ministerie|ministerstwo|ministère|ministerio|home office|cabinet office|council|municipal\w*|municipality|borough|city of|"
    r"gemeinde|stadt|landkreis|landratsamt|kommune\w*|kommun|länsstyrelse\w*|gmina|powiat|urząd\w*|starostwo|miasto|województwo|"
    r"nhs|hospital|krankenhaus|klinik\w*|sjukhus|szpital\w*|university|universität|universitet\w*|uniwersytet|politechnika|"
    r"police|polizei|polis\w*|policja|fire (and rescue )?service|bundesamt|bundesagentur|government|regierung|regering|rząd|parliament|embassy|armed forces|bundeswehr|"
    r"transport for|network rail|crown commercial|national (health|archives|library)|federal (office|ministry|agency))\b", re.I)
_WEAK = re.compile(r"\b(department (of|for)|county|bezirk|region(en)?|college|school|schule|agency|agentur|amt|verwaltung|administration|authority|defence|defense|army|navy|"
                   r"public (health|transport|body)|sveriges|statens|státní|bund)\b", re.I)
# financial buyers are never public-sector noise even when the word "state" or "national" appears in the name
_FIN = re.compile(r"\b(bank|banco|banque|sparkasse|volksbank|raiffeisen|insur\w*|versicherung\w*|assicura\w*|pension\w*|payments?|payment|fintech|kredit\w*|credit|finans\w*|finance|"
                  r"capital|asset management|wealth|trading|exchange|clearing|settlement|swift|mortgage|lender|lending)\b", re.I)


def is_public_body(name: str | None) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if _STRONG.search(n):
        return True
    if _FIN.search(n):
        return False
    return bool(_WEAK.search(n))


def lane_for(name: str | None, entity_type: str | None = None) -> str:
    return "public" if (entity_type == "public_body" or is_public_body(name)) else "private"
