"""Market-level job search from open government data. These sources find who is hiring engineers in financial services even before they are an account.
Sweden: Arbetsförmedlingen JobTech open job-search API (public, keyless; fields checked against the live service).
Germany's job-agency API refuses ordinary requests (403) and is deliberately not used."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import httpx

from .ats import classify_employment, tech_keywords
from .base import FetchBatch, HealthCheck, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft
from .http import request_json
from .util import excerpt, parse_dt, sha, utcnow

JOBTECH_URL = "https://jobsearch.api.jobtechdev.se/search"
IT_FIELD = "apaJ_2ja_LuF"          # occupation field "Data/IT"
FINANCE = re.compile(r"\b(bank\w*|betal\w*|fintech|finans\w*|försäkring\w*|kredit\w*|värdepapper\w*|pension\w*|börs\w*|swish|clearing|payments?|insurance|lending)\b", re.I)
# Staffing and consulting firms advertise roles for their CLIENTS: the advert may mention a bank, but the employer is not the buyer. They are left out of the hiring signals.
AGENCY = re.compile(r"\b(academic work|agile resources|experis|randstad|adecco|manpower|tng|kelly|hays|michael page|robert half|bemanning|rekrytering|recruit\w*|staffing|konsult\w*|consult\w*|"
                    r"cognizant|capgemini|accenture|tietoevry|infosys|wipro|tcs|tata consultancy|hcl|ework|nikita|friday|cgi|sogeti|nordic investin|talent|headhunt\w*|search ab|interim)\b", re.I)
DEFAULT_QUERIES = ["bank", "betalningar", "fintech", "försäkring", "finans", "kredit", "payments"]
# adverts for any employer mention "pension" and "insurance" in the benefits text, so the advert body is judged on stricter words
BODY_FINANCE = re.compile(r"\b(bank\w*|betal\w*|fintech|finansiell\w*|finanssektor\w*|kredit\w*|värdepapper\w*|clearing|payments?|lending|swish)\b", re.I)
PAGE = 100
MAX_PER_QUERY = 300


def employment_from_label(label: str | None, title: str) -> str:
    l = (label or "").lower()
    if "frilans" in l or "behov" in l:
        return "freelance"
    if "tidsbegr" in l or "vikariat" in l:
        return "contract"
    if "vanlig" in l or "tillsvidare" in l:
        return "permanent"
    if "sommar" in l or "feriejobb" in l:
        return "internship"
    return classify_employment(label, title)


class JobTechConnector:
    def __init__(self, client: httpx.AsyncClient, *, queries: list[str] | None = None, source_id: str = "jobs-jobtech-se", now_fn=utcnow, backfill_days: int = 14):
        self.client, self.queries, self.source_id, self.now, self.backfill = client, queries or DEFAULT_QUERIES, source_id, now_fn, backfill_days

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, "Arbetsförmedlingen JobTech", "official_api", "S2", "v1")

    async def health_check(self) -> HealthCheck:
        try:
            d = await request_json(self.client, "GET", JOBTECH_URL, params={"q": "bank", "limit": 1}, retries=0)
            if not isinstance(d, dict) or "hits" not in d:
                raise SchemaDrift("JobTech response has no 'hits'")
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        started = self.now()
        since = cursor.removeprefix("since:") if cursor else (started - timedelta(days=self.backfill)).strftime("%Y-%m-%dT%H:%M:%S")
        seen: dict[str, dict[str, Any]] = {}
        seen_total = dropped_agency = 0
        for q in self.queries:
            for off in range(0, MAX_PER_QUERY, PAGE):
                d = await request_json(self.client, "GET", JOBTECH_URL, params={"q": q, "occupation-field": IT_FIELD, "published-after": since, "limit": PAGE, "offset": off})
                if not isinstance(d, dict) or "hits" not in d:
                    raise SchemaDrift("JobTech response has no 'hits'")
                for h in d["hits"]:
                    seen_total += 1
                    if h.get("removed"):
                        continue
                    employer = (h.get("employer") or {}).get("name") or ""
                    if AGENCY.search(employer):
                        dropped_agency += 1
                        continue
                    near = f"{employer} {h.get('headline') or ''}"
                    body = (h.get("description") or {}).get("text") or ""
                    # the employer or the job title is financial, or the advert itself is about finance (at least two different finance words)
                    if FINANCE.search(near) or len({m.group(0).lower() for m in BODY_FINANCE.finditer(body)}) >= 2:
                        seen[str(h["id"])] = h
                if len(d["hits"]) < PAGE:
                    break
        items = [RawItem(external_id=i, version=h.get("timestamp") and str(h["timestamp"]), payload=h) for i, h in seen.items()]
        return FetchBatch(items, f"since:{started.strftime('%Y-%m-%dT%H:%M:%S')}", False, started, "v1",
                          quota_usage={"requests": len(self.queries), "hits_seen": seen_total, "kept_financial": len(items), "dropped_agency": dropped_agency}, complete_listing=False)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        return None

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        h = item.payload
        title = h.get("headline") or ""
        text = (h.get("description") or {}).get("text") or ""
        emp = employment_from_label((h.get("employment_type") or {}).get("label"), title)
        addr = h.get("workplace_address") or {}
        loc = ", ".join(x for x in (addr.get("municipality"), addr.get("region")) if x) or None
        rec = {"provider": "jobtech", "board": "se", "job_id": item.external_id, "employer_name": (h.get("employer") or {}).get("name"), "title": title,
               "team": (h.get("occupation_group") or {}).get("label"), "location": loc, "employment_type": emp, "posted_at": parse_dt(h.get("publication_date")),
               "updated_at_source": h.get("timestamp") and str(h["timestamp"]), "url": h.get("webpage_url"), "tech_keywords": tech_keywords(f"{title} {text}"), "open_status": "open",
               "country": "SE", "application_deadline": h.get("application_deadline")}
        return SourceDocumentDraft(item.external_id, sha(item.external_id, h.get("timestamp"), title), h.get("webpage_url"), title, parse_dt(h.get("publication_date")), None,
                                   excerpt(text), structured=rec, kind="job_posting")
