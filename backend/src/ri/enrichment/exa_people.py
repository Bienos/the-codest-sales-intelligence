"""Find the people who decide at one company with Exa's LinkedIn profile search (replaces the Apollo search on screen).

Five short searches, one per part of the buying group (technology leadership, engineering and delivery, data and AI, payments and digital
products, procurement and vendor management). Each LinkedIn result is parsed into name, job title and profile address, kept only when the
company is named in the headline or the profile text and the title looks like a decision role, and de-duplicated against the people we
already have at the account (by LinkedIn address and by name). Nothing becomes a person until someone presses "Add to people".
No email addresses are looked for or stored.
"""
from __future__ import annotations

import re
from typing import Any

from ..db import Conn, q1, qa
from ..people import ensure_person, record_source
from ..research.exa_search import ExaSearcher, ExaStopped, text_contents
from ..sources.base import BudgetExceeded

# (persona, query wording). Several phrasings for the same buying group find people whose titles differ by country and company size.
PERSONAS: list[tuple[str, str]] = [
    ("Technology leadership", "Chief Technology Officer, CIO or Chief Digital Officer at {name}"),
    ("Engineering & delivery", "VP or Head of Engineering, Head of Software Development or Head of Delivery and PMO at {name}"),
    ("Data & AI", "Head of Data, Chief Data Officer or Head of AI and Machine Learning at {name}"),
    ("Payments & digital", "Head of Payments, Head of Digital or Director of Digital Banking platforms at {name}"),
    ("Procurement & vendors", "IT procurement, vendor management or strategic sourcing lead for technology at {name}"),
]
NUM_RESULTS = 8
LEGAL = re.compile(r"\b(plc|ltd|limited|llp|inc|ag|se|sa|s\.a\.|asa|ab|oyj|a/s|as|gmbh|mbh|kg|nv|bv|spa|s\.p\.a\.|sp\.? z o\.?o\.?|group|holding|holdings|company|co)\b\.?", re.I)
SENIORITY = [("C-level", re.compile(r"\b(chief|c[tie]o|cdo|cfo|coo|cpo|ciso|founder|managing director|president)\b", re.I)),
             ("VP", re.compile(r"\b(vp|vice[- ]president|svp|evp)\b", re.I)),
             ("Head / Director", re.compile(r"\b(head|director|direktor|dyrektor|leiter|bereichsleiter|partner)\b", re.I)),
             ("Manager / Lead", re.compile(r"\b(manager|lead|leader|principal|architect|owner|kierownik)\b", re.I))]
NOT_DECIDER = re.compile(r"\b(intern|student|trainee|apprentice|graduate|recruit(er|ing)|talent acquisition|assistant|sales (rep|executive)|account executive)\b", re.I)


def normalise_linkedin(url: str) -> str | None:
    """https://www.linkedin.com/in/<handle> for a personal profile; None for company pages, posts, articles and anything else."""
    m = re.match(r"^https?://(?:[a-z]{2,3}\.|www\.)?linkedin\.com/in/([^/?#\s]+)", (url or "").strip(), re.I)
    if not m:
        return None
    from urllib.parse import unquote
    return f"https://www.linkedin.com/in/{unquote(m.group(1)).strip().lower()}"


def company_keys(name: str, domain: str | None) -> list[str]:
    base = re.sub(r"\s+", " ", LEGAL.sub(" ", name.replace("&", " and "))).strip().casefold()
    keys = {base} if len(base) >= 3 else set()
    if domain:
        stem = domain.split(".")[0].casefold()
        if len(stem) >= 4:
            keys.add(stem)
    return sorted(keys, key=len, reverse=True)


def _fold(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("&", " and ")).casefold()


def seniority(title: str | None) -> str | None:
    for label, rx in SENIORITY:
        if title and rx.search(title):
            return label
    return None


def parse_result(r: dict[str, Any], keys: list[str]) -> dict[str, Any] | None:
    """Name, job title and match from a LinkedIn search result. Titles look like 'Jane Doe - CTO - Acme Bank | LinkedIn'."""
    url = normalise_linkedin(r.get("url") or "")
    if not url:
        return None
    raw_title = re.sub(r"\s*\|\s*LinkedIn.*$", "", r.get("title") or "", flags=re.I).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", raw_title) if p.strip()]
    name = (r.get("author") or (parts[0] if parts else "")).strip()
    if not name or len(name) > 80 or "@" in name or not re.search(r"[^\W\d_]", name):
        return None
    headline_parts = parts[1:] if parts and parts[0].casefold() == name.casefold() else parts
    headline = " - ".join(headline_parts)
    text = r.get("text") or ""
    if not headline:                                     # some results carry the headline only as the first line of the text
        headline = text.strip().split("\n")[0][:200]
    title = re.split(r"\s+(?:at|@|bei|w)\s+", headline_parts[0], maxsplit=1)[0].strip() if headline_parts else re.split(r"\s+(?:at|@)\s+", headline, maxsplit=1)[0].strip()
    hk, tk = _fold(headline), _fold(text)
    match = "headline" if any(k in hk for k in keys) else "profile" if any(k in tk for k in keys) else None
    if not match:
        return None
    if not title or NOT_DECIDER.search(title) or not seniority(title):
        return {"skip": "not a decision role", "name": name}
    snippet = re.sub(r"\s+", " ", text).strip()[:400] or None
    return {"linkedin_url": url, "name": name[:120], "title": title[:160], "seniority": seniority(title), "snippet": snippet, "company_match": match}


def known_people(conn: Conn, org_id: str) -> tuple[dict[str, str], dict[str, str]]:
    rows = qa(conn, "SELECT p.id, p.full_name, p.linkedin_url FROM people p JOIN employment e ON e.person_id=p.id WHERE e.organization_id=%s AND p.erased_at IS NULL", (org_id,))
    by_url = {u: str(r["id"]) for r in rows if (u := normalise_linkedin(r["linkedin_url"] or ""))}
    by_name = {re.sub(r"\s+", " ", r["full_name"]).strip().casefold(): str(r["id"]) for r in rows}
    return by_url, by_name


def find_people(conn: Conn, org_id: str, search_id: str, *, searcher: ExaSearcher, by: str) -> dict[str, Any]:
    org = q1(conn, "SELECT id, display_name, canonical_domain FROM organizations WHERE id=%s", (org_id,))
    if not org:
        raise ValueError("account not found")
    keys = company_keys(org["display_name"], org["canonical_domain"])
    by_url, by_name = known_people(conn, org_id)
    out: dict[str, Any] = {"found": 0, "new": 0, "already_known": 0, "skipped": 0, "stopped": None}
    seen: set[str] = set()
    use_category = True
    for persona, q in PERSONAS:
        body: dict[str, Any] = {"query": q.format(name=org["display_name"]), "type": "auto", "numResults": NUM_RESULTS, "contents": text_contents(600)}
        if use_category:
            body["category"] = "linkedin profile"
        else:
            body["includeDomains"] = ["linkedin.com"]
        try:
            try:
                results = searcher.search(persona, body, text_cap=600)
            except ExaStopped as exc:
                if exc.error_class != "schema_drift" or not use_category:
                    raise
                use_category = False                    # Exa did not accept the category: same search restricted to linkedin.com instead
                body.pop("category", None)
                body["includeDomains"] = ["linkedin.com"]
                results = searcher.search(persona, body, text_cap=600)
        except BudgetExceeded:
            if searcher.calls == 0:
                raise
            out["stopped"] = "The monthly Exa budget ran out part-way, so some searches were not run."
            break
        except ExaStopped as exc:
            if searcher.calls == 0:
                raise
            out["stopped"] = str(exc)
            break
        for r in results:
            c = parse_result(r, keys)
            if not c:
                continue
            if "skip" in c:
                out["skipped"] += 1
                continue
            if c["linkedin_url"] in seen:
                continue
            seen.add(c["linkedin_url"])
            out["found"] += 1
            known = by_url.get(c["linkedin_url"]) or by_name.get(re.sub(r"\s+", " ", c["name"]).strip().casefold())
            status = "already_known" if known else "found"
            row = conn.execute(
                "INSERT INTO people_candidates(organization_id, search_id, provider, linkedin_url, name, title, persona, seniority, snippet, company_match, status, person_id, found_by) "
                "VALUES (%s,%s,'exa',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (organization_id, linkedin_url) DO UPDATE SET "
                "title=EXCLUDED.title, snippet=EXCLUDED.snippet, company_match=EXCLUDED.company_match, search_id=EXCLUDED.search_id, updated_at=now(), "
                "status=CASE WHEN people_candidates.status='found' THEN EXCLUDED.status ELSE people_candidates.status END, "
                "person_id=coalesce(people_candidates.person_id, EXCLUDED.person_id) RETURNING (xmax = 0) AS inserted",
                (org_id, search_id, c["linkedin_url"], c["name"], c["title"], persona, c["seniority"], c["snippet"], c["company_match"], status, known, by)).fetchone()
            if known:
                out["already_known"] += 1
            elif row and row["inserted"]:
                out["new"] += 1
        conn.commit()
    return out


def add_candidate(conn: Conn, cid: str, *, by: str) -> dict[str, Any]:
    """Creates (or finds) the person at the account with source 'exa'; fills a missing LinkedIn address; never invents an email."""
    c = q1(conn, "SELECT * FROM people_candidates WHERE id=%s", (cid,))
    if not c:
        raise LookupError("candidate not found")
    if c["status"] == "added" and c["person_id"]:
        return {"person_id": str(c["person_id"]), "created": False}
    org_id = str(c["organization_id"])
    by_url, _ = known_people(conn, org_id)
    pid = by_url.get(c["linkedin_url"])
    created = False
    if pid:
        record_source(conn, pid, "exa", c["linkedin_url"], {"persona": c["persona"], "added_by": by})
    else:
        before = q1(conn, "SELECT count(*) AS n FROM people")["n"]          # type: ignore[index]
        pid = ensure_person(conn, name=c["name"], email=None, org_id=org_id, title=c["title"], email_status="unknown", system="exa",
                            external_id=c["linkedin_url"], detail={"persona": c["persona"], "seniority": c["seniority"], "added_by": by})
        created = q1(conn, "SELECT count(*) AS n FROM people")["n"] > before      # type: ignore[index]
    if not pid:
        raise LookupError("the person could not be placed at this account")
    conn.execute("UPDATE people SET linkedin_url=%s WHERE id=%s AND linkedin_url IS NULL", (c["linkedin_url"], pid))
    conn.execute("UPDATE employment SET evidence_url=coalesce(evidence_url, %s) WHERE person_id=%s AND organization_id=%s", (c["linkedin_url"], pid, org_id))
    conn.execute("UPDATE people_candidates SET status='added', person_id=%s, decided_by=%s, updated_at=now() WHERE id=%s", (pid, by, cid))
    return {"person_id": pid, "created": created}
