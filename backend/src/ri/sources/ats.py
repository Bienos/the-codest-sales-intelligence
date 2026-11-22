"""Documented public ATS job-board APIs: Lever, Greenhouse, Ashby (spec 02 §7 jobs lifecycle, 09 §6).

Each board listing is an AFFIRMATIVE full listing (complete_listing=True): a job missing from a successful listing
is closed. A 404/403/timeout is a source-health problem and NEVER closes jobs (spec 13 §6).
STATUS: pending_connector_validation (docs-based; live calls blocked in the build sandbox).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import FetchBatch, HealthCheck, RawItem, SchemaDrift, SourceDescriptor, SourceDocumentDraft
from .http import request_json
from .util import excerpt, parse_dt, sha, utcnow

_CONTRACT = re.compile(r"\b(contract(or)?|freelance|interim|fixed[- ]term|temporary|b2b|6[- ]month|12[- ]month|day rate)\b", re.I)
_PERM = re.compile(r"\b(full[- ]time|permanent|part[- ]time)\b", re.I)
_TECH = ["ruby on rails", "rails", "python", "java", "kotlin", "react", "vue", "typescript", "node", "aws", "azure", "gcp", "kubernetes",
         "terraform", "kafka", "golang", ".net", "c#", "mainframe", "cobol", "temenos", "mambu", "thought machine", "finastra",
         "iso 20022", "sepa", "llm", "rag", "mlops", "langchain", "sre", "test automation", "playwright", "selenium"]


def classify_employment(commitment: str | None, title: str, body: str = "") -> str:
    """Contract wording, direct supplier route and hiring volume are SEPARATE concerns (spec 02 §7)."""
    blob = f"{commitment or ''} {title}"
    if _CONTRACT.search(blob):
        return "contract"
    if re.search(r"\bintern(ship)?\b", blob, re.I):
        return "internship"
    if _PERM.search(blob) or commitment:
        return "permanent" if not _CONTRACT.search(commitment or "") else "contract"
    return "unknown"


# "Go" is also an ordinary English word ("go further"): it counts as the language only in a clear technical phrase
_GO_LANG = re.compile(r"\b(go(?:lang)?\s*(?:/|,|\)|\(|developer|engineer|backend|services?|microservices?|programming)|\((?:go|golang)\)|(?:python|java|kotlin|rust)\s*(?:,|/)\s*go\b)", re.I)


def tech_keywords(text: str) -> list[str]:
    t = text.lower()
    found = {k for k in _TECH if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t)}
    if _GO_LANG.search(text):
        found.add("golang")
    return sorted(found)


class _AtsBase:
    provider = ""

    def __init__(self, client: httpx.AsyncClient, board: str, *, employer_name: str | None = None,
                 source_id: str | None = None, now_fn=utcnow):
        self.client, self.board, self.employer_name, self.now = client, board, employer_name or board, now_fn
        self.source_id = source_id or f"ats-{self.provider}-{board}"

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(self.source_id, self.provider, "official_api", "S2", "v1")

    async def health_check(self) -> HealthCheck:
        try:
            await self._list()
            return HealthCheck(True)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(False, str(exc), getattr(exc, "error_class", "transient_network"))

    async def _list(self) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        jobs = await self._list()
        items = [RawItem(external_id=str(j["_id"]), version=j.get("_updated"), payload=j) for j in jobs]
        return FetchBatch(items, None, False, self.now(), "v1", quota_usage={"requests": 1}, complete_listing=True)

    async def fetch_by_id(self, external_id: str) -> RawItem | None:
        for j in await self._list():
            if str(j["_id"]) == external_id:
                return RawItem(external_id, j.get("_updated"), j)
        return None  # absent from an affirmative listing => closed

    def canonicalize(self, item: RawItem) -> SourceDocumentDraft:
        j = item.payload
        rec = {"provider": self.provider, "board": self.board, "job_id": item.external_id, "employer_name": self.employer_name,
               "title": j["_title"], "team": j.get("_team"), "location": j.get("_location"),
               "employment_type": j["_employment"], "posted_at": j.get("_posted"), "updated_at_source": j.get("_updated"),
               "url": j.get("_url"), "tech_keywords": j.get("_tech", []), "open_status": "open"}
        vkey = sha(item.external_id, j.get("_updated"), j["_title"], j["_employment"])
        return SourceDocumentDraft(item.external_id, vkey, j.get("_url"), j["_title"], j.get("_posted"), None,
                                   excerpt(j.get("_desc")), structured=rec, kind="job_posting")


class LeverConnector(_AtsBase):
    provider = "lever"
    API = "https://api.lever.co/v0/postings"

    async def _list(self):
        data = await request_json(self.client, "GET", f"{self.API}/{self.board}", params={"mode": "json"})
        if not isinstance(data, list):
            raise SchemaDrift("Lever postings response is not a list")
        out = []
        for p in data:
            cats = p.get("categories") or {}
            desc = p.get("descriptionPlain") or ""
            out.append({"_id": p["id"], "_title": p["text"], "_team": cats.get("team") or cats.get("department"),
                        "_location": cats.get("location"), "_employment": classify_employment(cats.get("commitment"), p["text"], desc),
                        "_posted": datetime.fromtimestamp(p["createdAt"] / 1000, tz=timezone.utc) if p.get("createdAt") else None,
                        "_updated": None, "_url": p.get("hostedUrl"), "_desc": desc, "_tech": tech_keywords(f"{p['text']} {desc}")})
        return out


class LeverEuConnector(LeverConnector):
    """Lever's EU instance: same listing API on its own host (host reachable; the data format is Lever's documented one)."""
    provider = "lever_eu"
    API = "https://api.eu.lever.co/v0/postings"


class GreenhouseConnector(_AtsBase):
    provider = "greenhouse"

    async def _list(self):
        data = await request_json(self.client, "GET", f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs", params={"content": "true"})
        if not isinstance(data, dict) or "jobs" not in data:
            raise SchemaDrift("Greenhouse response has no 'jobs'")
        out = []
        for j in data["jobs"]:
            desc = re.sub(r"<[^>]+>", " ", j.get("content") or "")
            depts = ", ".join(d.get("name", "") for d in j.get("departments", []))
            out.append({"_id": j["id"], "_title": j["title"], "_team": depts or None, "_location": (j.get("location") or {}).get("name"),
                        "_employment": classify_employment(None, j["title"], desc), "_posted": parse_dt(j.get("first_published")),
                        "_updated": j.get("updated_at"), "_url": j.get("absolute_url"), "_desc": desc,
                        "_tech": tech_keywords(f"{j['title']} {desc}")})
        return out


class AshbyConnector(_AtsBase):
    provider = "ashby"

    async def _list(self):
        data = await request_json(self.client, "GET", f"https://api.ashbyhq.com/posting-api/job-board/{self.board}")
        if not isinstance(data, dict) or "jobs" not in data:
            raise SchemaDrift("Ashby response has no 'jobs'")
        out = []
        for j in data["jobs"]:
            if j.get("isListed") is False:
                continue
            desc = j.get("descriptionPlain") or ""
            et = (j.get("employmentType") or "").lower()
            emp = {"fulltime": "permanent", "parttime": "permanent", "contract": "contract", "temporary": "contract",
                   "intern": "internship"}.get(et.replace("-", "").replace(" ", "")) or classify_employment(et, j["title"], desc)
            out.append({"_id": j["id"], "_title": j["title"], "_team": j.get("team") or j.get("department"), "_location": j.get("location"),
                        "_employment": emp,
                        "_posted": parse_dt(j.get("publishedAt")), "_updated": j.get("publishedAt"), "_url": j.get("jobUrl"),
                        "_desc": desc, "_tech": tech_keywords(f"{j['title']} {desc}")})
        return out



class SmartRecruitersConnector(_AtsBase):
    """SmartRecruiters public Posting API (checked against the live service): list pages with the company name, then one detail call per ENGINEERING posting
    (capped) for the text, so tech keywords come from the real advert. Only a fully read listing counts as complete, so a half-read board never closes jobs."""
    provider = "smartrecruiters"
    DETAIL_CAP = 80

    async def _list(self):
        base = f"https://api.smartrecruiters.com/v1/companies/{self.board}/postings"
        rows: list[dict[str, Any]] = []
        total = 0
        for off in range(0, 2000, 100):
            data = await request_json(self.client, "GET", base, params={"limit": 100, "offset": off})
            if not isinstance(data, dict) or "content" not in data:
                raise SchemaDrift("SmartRecruiters response has no 'content'")
            rows += data["content"]
            total = int(data.get("totalFound") or 0)
            if off + 100 >= total or not data["content"]:
                break
        self.complete = len(rows) >= total
        from ..events.classify import engineering_relevant
        details = 0
        out = []
        for p in rows:
            title = p.get("name") or ""
            desc = ""
            if engineering_relevant(title) and details < self.DETAIL_CAP:
                details += 1
                try:
                    d = await request_json(self.client, "GET", f"{base}/{p['id']}")
                    sec = ((d.get("jobAd") or {}).get("sections") or {})
                    desc = re.sub(r"<[^>]+>", " ", " ".join((sec.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications", "additionalInformation")))
                except Exception:  # noqa: BLE001 - a missing detail only loses keywords, never the posting
                    desc = ""
            et = ((p.get("typeOfEmployment") or {}).get("id") or "").lower()
            emp = {"permanent": "permanent", "contract": "contract", "temporary": "contract", "intern": "internship", "internship": "internship"}.get(et) or classify_employment(et, title, desc)
            loc = (p.get("location") or {}).get("fullLocation")
            out.append({"_id": p["id"], "_title": title, "_team": (p.get("function") or {}).get("label") or (p.get("department") or {}).get("label"), "_location": loc,
                        "_employment": emp, "_posted": parse_dt(p.get("releasedDate")), "_updated": p.get("releasedDate"),
                        "_url": f"https://jobs.smartrecruiters.com/{self.board}/{p['id']}", "_desc": desc, "_tech": tech_keywords(f"{title} {desc}"),
                        "_company": (p.get("company") or {}).get("name")})
        self.company_name = next((o["_company"] for o in out if o.get("_company")), None)
        return out

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        jobs = await self._list()
        items = [RawItem(external_id=str(j["_id"]), version=j.get("_updated"), payload=j) for j in jobs]
        return FetchBatch(items, None, False, self.now(), "v1", quota_usage={"requests": 1, "postings": len(items)}, complete_listing=bool(getattr(self, "complete", False)))
