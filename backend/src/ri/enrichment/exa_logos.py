"""Company logos from the company's own LinkedIn page, found by one extra Exa search during company research.

The same idea as the LinkedIn profile photos of people (``exa_people``), the other way round: here a ``company-logo`` picture IS wanted and
everything else (a banner, LinkedIn's grey placeholder, an icon, a person's photo) is not. A wrong logo is worse than initials, so a logo is
accepted only when ALL of these hold:
  * the result is a LinkedIn company page (``linkedin.com/company/<handle>``), not a person, a post, a job or a school;
  * the page name is the company: the whole normalised name (parentheses and legal suffixes removed, accents and case ignored), or the
    LinkedIn handle equals the company's website name (``kestrel.example`` -> ``kestrel``). A partial or "similar" name never counts;
  * the picture is a plain https address on a ``licdn.com`` host (not ``static.licdn.com``) whose path says ``company-logo``.
Only the address is kept, never the image. The app draws initials again when a logo no longer loads, and a new research run refreshes it.
Nothing here spends money: the search itself is made (and budget-gated) by ``research.company_profile``.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from ..db import Conn
from .exa_people import LEGAL

LOGO_RECHECK_DAYS = 60          # a logo (or "no logo found") is not looked for again within this time, so repeated research costs nothing extra
LOGO_SEARCH_CHARS = 200         # page text asked for with the logo search: only the title and the picture matter

# Addresses that are never a company logo: banners and backgrounds, LinkedIn's grey placeholder, ghost and default pictures, icons, vector files.
NOT_A_LOGO = re.compile(r"displaybackgroundimage|background|banner|/sc/h/|ghost|default|placeholder|favicon|\.svg(\?|$)|\.ico(\?|$)|profile-(displayphoto|framedphoto)", re.I)
# Words that may follow the company's own name in the LinkedIn page name without it being another company ("Monzo" -> "Monzo Bank").
GENERIC_AFTER_NAME = {"bank", "banking", "group", "holding", "holdings", "uk", "europe", "international", "global", "financial", "finance", "services"}


def normalise_company_page(url: Any) -> str | None:
    """``https://www.linkedin.com/company/<handle>`` for a LinkedIn company page (any country subdomain, any sub-page); None for anything else
    (people, posts, jobs, schools, showcase pages, look-alike hosts)."""
    m = re.match(r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/company/([^/?#\s]+)", url.strip() if isinstance(url, str) else "", re.I)
    if not m:
        return None
    handle = unquote(m.group(1)).strip().lower()
    return f"https://www.linkedin.com/company/{handle}" if handle else None


def company_handle(url: Any) -> str | None:
    page = normalise_company_page(url)
    return page.rsplit("/", 1)[1] if page else None


def name_key(name: Any) -> str:
    """A company name reduced for comparing: no accents, nothing in parentheses, no legal suffix (Ltd, AG, Group, ...), no punctuation, lower case,
    no leading 'the'. '' when nothing useful is left."""
    s = unicodedata.normalize("NFKD", name if isinstance(name, str) else "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\([^)]*\)", " ", s).replace("&", " and ")
    s = LEGAL.sub(" ", s)
    s = re.sub(r"[\W_]+", " ", s.casefold()).strip()
    return re.sub(r"^the\s+", "", s)


def domain_stem(domain: Any) -> str | None:
    """'kestrel' for kestrel.example or www.kestrel.co.uk. Only a stem of four characters or more is a usable name."""
    if not isinstance(domain, str):
        return None
    labels = domain.strip().lower().removeprefix("www.").split(".")
    return labels[0] if len(labels[0]) >= 4 and re.fullmatch(r"[a-z0-9-]+", labels[0]) else None


def _head(title: Any) -> str:
    """The company name part of a LinkedIn page title ('Kestrel Pay | LinkedIn', 'Kestrel Pay: Overview', 'Kestrel Pay - Company Profile')."""
    t = re.sub(r"\s*\|\s*LinkedIn.*$", "", title if isinstance(title, str) else "", flags=re.I)
    return re.split(r"\s+[-–—|·]\s+|:\s+", t.strip(), maxsplit=1)[0].strip()


def page_matches(title: Any, url: Any, name: str, domain: str | None) -> str | None:
    """'name' when the page name is the company's whole name (or the name plus generic words such as 'Bank' or 'Group'), 'domain' when the LinkedIn
    handle equals the company's website name, else None. Never a partial match."""
    handle = company_handle(url)
    key, head = name_key(name), name_key(_head(title))
    if len(key) >= 3 and head:
        if head == key:
            return "name"
        if head.startswith(key + " ") and set(head[len(key):].split()) <= GENERIC_AFTER_NAME:
            return "name"
    stem = domain_stem(domain)
    if handle and stem and handle == stem:
        return "domain"
    return None


def company_logo(image: Any) -> str | None:
    """The company logo Exa returns with a LinkedIn company page, or None. Only an https address on licdn.com (not the static placeholder host)
    whose path says company-logo, never a banner, placeholder, icon or person's photo."""
    if not isinstance(image, str):
        return None
    u = image.strip()
    if not u.startswith("https://") or len(u) > 600 or re.search(r"\s", u):          # the column accepts exactly 'https://%' up to 600 characters
        return None
    try:
        p = urlparse(u)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if not (host == "licdn.com" or host.endswith(".licdn.com")) or host == "static.licdn.com":
        return None
    if "company-logo" not in p.path.lower() or NOT_A_LOGO.search(p.path):
        return None
    return u


def pick_logo(results: list[dict[str, Any]], name: str, domain: str | None) -> tuple[str | None, dict[str, str]]:
    """The logo to keep from the logo search's results, and why (a plain state, never names or addresses): found | no_linkedin_page |
    name_mismatch (company pages came back but none is clearly this company) | no_logo (the page came without a picture) |
    not_a_logo (only a placeholder, banner or icon came back; ``kind`` says which)."""
    from ..research.exa_search import image_kind
    pages = [r for r in results if normalise_company_page(r.get("url"))]
    if not pages:
        return None, {"state": "no_linkedin_page"}
    own = [r for r in pages if page_matches(r.get("title"), r.get("url"), name, domain)]
    if not own:
        return None, {"state": "name_mismatch"}
    pictured = [r for r in own if r.get("image")]
    if not pictured:
        return None, {"state": "no_logo"}
    for r in pictured:
        logo = company_logo(r["image"])
        if logo:
            return logo, {"state": "found", "kind": image_kind(logo)}
    return None, {"state": "not_a_logo", "kind": image_kind(pictured[0]["image"])}


def logo_due(checked_at: datetime | None, now: datetime | None = None) -> bool:
    """True when the logo was never looked for, or the last look was more than LOGO_RECHECK_DAYS ago."""
    if checked_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < now - timedelta(days=LOGO_RECHECK_DAYS)


def set_logo(conn: Conn, org_id: str, logo: str | None) -> bool:
    """Notes that the company's logo was looked for just now (so it is not searched again for LOGO_RECHECK_DAYS), and keeps the logo when one was
    accepted. With no logo the one already on file stays. Returns whether a logo was stored."""
    logo = company_logo(logo)
    if logo:
        conn.execute("UPDATE organizations SET logo_url=%s, logo_checked_at=now() WHERE id=%s", (logo, org_id))
    else:
        conn.execute("UPDATE organizations SET logo_checked_at=now() WHERE id=%s", (org_id,))
    return bool(logo)
