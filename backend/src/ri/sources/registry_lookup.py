"""GLEIF LEI lookup - identity evidence (spec 02 §4 identity). Used by identity review, never auto-merges."""
from __future__ import annotations

import httpx

from .base import SchemaDrift
from .http import request_json

GLEIF = "https://api.gleif.org/api/v1/lei-records"


async def lookup_by_lei(client: httpx.AsyncClient, lei: str) -> dict | None:
    data = await request_json(client, "GET", f"{GLEIF}/{lei}")
    d = data.get("data") if isinstance(data, dict) else None
    if not d:
        return None
    a = d.get("attributes", {})
    ent = a.get("entity", {})
    if "legalName" not in ent:
        raise SchemaDrift("GLEIF record without legalName")
    return {"lei": a.get("lei", d.get("id")), "legal_name": ent["legalName"].get("name"),
            "country": (ent.get("legalAddress") or {}).get("country"), "status": ent.get("status"),
            "jurisdiction": ent.get("jurisdiction"), "registration": (a.get("registration") or {}).get("status")}


async def search_by_name(client: httpx.AsyncClient, name: str, size: int = 5) -> list[dict]:
    data = await request_json(client, "GET", GLEIF, params={"filter[entity.legalName]": name, "page[size]": size})
    out = []
    for d in (data.get("data") if isinstance(data, dict) else None) or []:
        ent = d.get("attributes", {}).get("entity", {})
        out.append({"lei": d.get("id"), "legal_name": (ent.get("legalName") or {}).get("name"),
                    "country": (ent.get("legalAddress") or {}).get("country")})
    return out
