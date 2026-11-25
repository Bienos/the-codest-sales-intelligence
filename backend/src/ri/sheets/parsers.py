"""Tolerant, lossless-by-design cell parsers. Originals are always kept in staging; parsed values are derived."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

_COUNTRIES = {
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB", "great britain": "GB",
    "germany": "DE", "deutschland": "DE", "switzerland": "CH", "austria": "AT", "poland": "PL", "polska": "PL",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "iceland": "IS", "ireland": "IE",
    "netherlands": "NL", "the netherlands": "NL", "france": "FR", "spain": "ES", "italy": "IT", "belgium": "BE",
    "luxembourg": "LU", "united states": "US", "usa": "US", "canada": "CA", "estonia": "EE", "latvia": "LV",
    "lithuania": "LT", "greece": "GR", "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ", "portugal": "PT", "singapore": "SG",
    "hong kong": "HK", "australia": "AU", "india": "IN", "turkey": "TR", "malta": "MT", "liechtenstein": "LI",
}


def parse_country(raw: str | None) -> str | None:
    """'Sweden; Stockholm', 'United Kingdom — London', 'Germany' -> ISO2 (None when not recognised)."""
    if not raw:
        return None
    first = re.split(r"[;—–\-,/|]", raw, maxsplit=1)[0].strip().lower()
    return _COUNTRIES.get(first)


@dataclass(frozen=True)
class Employees:
    value: int | None
    basis: str            # sheet_recorded | provider_estimate | unknown
    uncertain: bool


def parse_employees(raw: str | None) -> Employees:
    """'218 271' -> 218271; '1,027 employees (Exa estimate, reverify)' -> estimate/uncertain; '580 Exa estimate'."""
    if raw is None or not raw.strip():
        return Employees(None, "unknown", False)
    s = raw.strip()
    m = re.search(r"\d[\d\s,.  ]*", s)
    if not m:
        return Employees(None, "unknown", False)
    digits = re.sub(r"[^\d]", "", m.group(0))
    if not digits:
        return Employees(None, "unknown", False)
    est = bool(re.search(r"estimate|exa|reverify|approx|~|about", s, re.I))
    return Employees(int(digits), "provider_estimate" if est else "sheet_recorded", est)


def parse_date(raw: str | None) -> date | None:
    """ISO date, tolerating annotations ('2026-11-13 (amended EU notice)'). Unknown stays None - never guessed."""
    if not raw:
        return None
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_MULTI_TLD = {"co.uk", "org.uk", "ac.uk", "com.pl", "com.au", "co.nz", "com.tr", "com.sg", "com.hk", "co.za"}


def normalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if not s or s in {"n/a", "-"}:
        return None
    if "//" not in s:
        s = "https://" + s
    host = (urlparse(s).hostname or "").removeprefix("www.")
    if not host or "." not in host:
        return None
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_LEGAL = {
    "plc", "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation", "co", "gmbh", "ag", "se", "sa",
    "nv", "bv", "ab", "as", "asa", "oy", "oyj", "abp", "spa", "sas", "kg", "ug", "sp", "zoo", "zo", "oo", "s", "a",
    "publ", "aps", "lp", "llp", "pte", "bhd", "kk",
}
_NOISE = {"the", "and", "&", "group", "holdings", "holding", "bank", "banking"}


def normalize_name(raw: str | None, *, keep_bank: bool = True) -> str:
    """Casefolded, accent-folded, legal-form-stripped. 'bank' is kept by default: 'Nordea Bank' != 'Nordea'."""
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^\w\s&]", " ", s)
    toks = [t for t in s.split() if t not in _LEGAL]
    noise = {"the", "and", "&"} if keep_bank else _NOISE
    toks = [t for t in toks if t not in noise]
    return " ".join(toks)


def split_alternatives(raw: str) -> list[str]:
    """'Nordea Bank Abp / Nordea Group' -> both spellings become aliases (never separate orgs)."""
    return [p.strip() for p in re.split(r"\s+/\s+", raw) if p.strip()]
