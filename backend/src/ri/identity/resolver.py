"""Company identity resolution (spec 04 §5, FR-006).

Phase A deterministic (identifiers, exact normalised name/alias + compatible country)
Phase B candidate generation, Phase C fuzzy ranking (NEVER auto-links), Phase D policy decision.
Hard rules: contradictory legal IDs never auto-link; parent/subsidiary is an edge, not a merge; different known
countries are different legal entities; a shared domain alone never links (groups share domains).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from ..db import Conn, qa, q1
from ..sheets.parsers import normalize_name, split_alternatives

FUZZY_REVIEW_THRESHOLD = 88
DOMAIN_NAME_THRESHOLD = 80


@dataclass
class Decision:
    action: str                      # auto_link | create_new | review | unmatched
    org_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    reason: str = ""
    features: dict[str, Any] = field(default_factory=dict)


def name_variants(raw: str) -> list[str]:
    out: list[str] = []
    for alt in split_alternatives(raw) or [raw]:
        n = normalize_name(alt)
        if n and n not in out:
            out.append(n)
    return out


def _country_compatible(a: str | None, b: str | None) -> bool:
    return not a or not b or a.strip() == b.strip()


def resolve(
    conn: Conn,
    *,
    name: str,
    country: str | None = None,
    domain: str | None = None,
    identifiers: list[tuple[str, str, str]] | None = None,
    allow_create: bool = False,
) -> Decision:
    feats: dict[str, Any] = {"name": name, "country": country, "domain": domain}
    # ---- Phase A1: legal identifiers
    hits: set[str] = set()
    for scheme, juris, ident in identifiers or []:
        row = q1(conn, "SELECT organization_id FROM organization_identifiers WHERE scheme=%s AND jurisdiction=%s AND identifier=%s",
                 (scheme, juris, ident))
        if row:
            hits.add(str(row["organization_id"]))
    if len(hits) > 1:
        return Decision("review", candidates=sorted(hits), reason="contradictory_ids", features=feats)
    if len(hits) == 1:
        org_id = next(iter(hits))
        # an identifier of the same scheme/jurisdiction that differs from the stored one = contradiction
        for scheme, juris, ident in identifiers or []:
            clash = q1(conn, "SELECT 1 FROM organization_identifiers WHERE organization_id=%s AND scheme=%s AND jurisdiction=%s AND identifier<>%s",
                       (org_id, scheme, juris, ident))
            if clash:
                return Decision("review", candidates=[org_id], reason="contradictory_ids", features=feats)
        return Decision("auto_link", org_id, [org_id], "legal_identifier", feats)

    # ---- Phase A2/B: exact normalised name or alias, country compatible
    variants = name_variants(name)
    feats["name_variants"] = variants
    cands: list[dict[str, Any]] = []
    if variants:
        cands = qa(conn,
                   "SELECT DISTINCT o.id, o.display_name, o.canonical_domain, o.country_code FROM organizations o "
                   "JOIN organization_aliases a ON a.organization_id=o.id WHERE a.alias_norm = ANY(%s)", (variants,))
    # defence in depth: an org without an alias row must still match on its normalised display name (never duplicate an account)
    seen = {str(c["id"]) for c in cands}
    for c in qa(conn, "SELECT id, display_name, canonical_domain, country_code FROM organizations WHERE display_name %% %s LIMIT 25", (name,)):
        if str(c["id"]) not in seen and normalize_name(c["display_name"]) in variants:
            cands.append(c)
    compatible = [c for c in cands if _country_compatible(country, c["country_code"])]
    feats["country_excluded"] = [str(c["id"]) for c in cands if c not in compatible]
    if len(compatible) == 1:
        c = compatible[0]
        if domain and c["canonical_domain"] and domain != c["canonical_domain"]:
            feats["domain_conflict"] = [domain, c["canonical_domain"]]
            return Decision("review", candidates=[str(c["id"])], reason="domain_conflict", features=feats)
        return Decision("auto_link", str(c["id"]), [str(c["id"])], "exact_name", feats)
    if len(compatible) > 1:
        return Decision("review", candidates=[str(c["id"]) for c in compatible], reason="ambiguous_match", features=feats)

    # ---- Phase B/C: domain and fuzzy candidates (rank only; never auto-merge risky entities)
    ranked: list[tuple[float, str, str]] = []
    if domain:
        for c in qa(conn, "SELECT id, display_name, country_code FROM organizations WHERE canonical_domain=%s", (domain,)):
            if not _country_compatible(country, c["country_code"]):
                continue
            score = max((fuzz.token_set_ratio(v, normalize_name(c["display_name"])) for v in variants), default=0)
            ranked.append((score, str(c["id"]), "domain"))
    if variants:
        for c in qa(conn, "SELECT id, display_name, country_code FROM organizations WHERE display_name %% %s LIMIT 25", (name,)):
            if not _country_compatible(country, c["country_code"]):
                continue
            score = max(fuzz.token_set_ratio(v, normalize_name(c["display_name"])) for v in variants)
            if score >= FUZZY_REVIEW_THRESHOLD:
                ranked.append((score, str(c["id"]), "fuzzy"))
    if ranked:
        ranked.sort(reverse=True)
        feats["ranked"] = [{"score": s, "org_id": o, "via": v} for s, o, v in ranked[:5]]
        # domain alone never auto-links (group domains are shared); fuzzy never auto-links
        return Decision("review", candidates=list(dict.fromkeys(o for _, o, _ in ranked)), reason="fuzzy_or_domain_candidate", features=feats)

    if allow_create:
        return Decision("create_new", reason="no_candidate", features=feats)
    return Decision("unmatched", reason="no_candidate", features=feats)
