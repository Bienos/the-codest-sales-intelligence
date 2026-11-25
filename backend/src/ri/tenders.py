"""Open tenders for software work, from every official procurement source, whoever the buyer is.

The account workspace only follows privately owned financial companies, and those rarely publish public tenders, so the tender list is
kept separate: any open notice with a future deadline whose main service is software development or IT services, labelled by buyer type.
A notice is never presented as checked unless an official source returned it; the last check time is always shown."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .access import Principal, org_scope_sql
from .db import Conn, qa
from .market import alpha2, region_for

# Main service code (CPV) for development and IT services: 72000000 IT services, 722xxxxx software programming, consultancy, development.
RELEVANT_CPV = r"^72(0|2)"
# These two collectors already keep only software notices when they read the feed.
SOFTWARE_ONLY_SOURCES = ("procurement-uk-fts", "procurement-uk-contracts-finder")
# "Our markets": where The Codest sells. The tender list starts here; every other region stays one click away.
CORE_REGIONS = ("uk", "dach", "nordics", "poland")
TENDER_SOURCES = ("procurement-ted", "procurement-uk-fts", "procurement-uk-contracts-finder", "procurement-ezamowienia")
SOURCE_LABEL = {"procurement-ted": "EU tenders (TED)", "procurement-uk-fts": "UK Find a Tender", "procurement-uk-contracts-finder": "UK Contracts Finder",
                "procurement-ezamowienia": "Poland e-Zamówienia"}
_FINANCE_BUYER = r"\m(bank\w*|banco|banque|sparkasse|kasa|insur\w*|versicher\w*|ubezpiecz\w*|pension\w*|payment\w*|financ\w*|fintech|clearing|exchange|börse|giełd\w*)\M"


def split_title(title: str | None) -> tuple[str, str | None]:
    """TED titles read "Country – Category – Own title": keep the buyer's own title, and the category separately."""
    parts = [p.strip() for p in (title or "").split(" – ")]
    if len(parts) >= 3:
        return " – ".join(parts[2:]), parts[1]
    return (title or "").strip(), None


def in_region(reg: str, region: str) -> bool:
    if not region:
        return True
    if region == "core":
        return reg in CORE_REGIONS
    if region == "international":
        return reg not in (*CORE_REGIONS, "europe")
    return reg == region


def open_tenders(conn: Conn, p: Principal, *, q: str = "", region: str = "", buyer: str = "", min_days: int = 3, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """`region`: "" for every region, "core" for our markets (UK, DACH, Nordics, Poland), or one region key.
    `min_days`: leave out tenders closing sooner than this (too late to prepare a bid); they are still counted."""
    from .api.search import all_like, tokens
    where, params = org_scope_sql(p, "o.id")
    toks = tokens(q)
    match, margs = all_like("concat_ws(' ', n.title, n.buyer_name, n.scope)", toks) if toks else ("TRUE", [])
    rows = qa(conn, f"""
        SELECT n.id, n.source_id, n.notice_id, n.buyer_name, n.title, n.scope, n.country, n.deadline_at, n.deadline_tz, n.status, n.official_url,
               n.value_if_reported, n.published_at, n.last_status_check_at, n.service_codes, o.id AS org_id, o.display_name AS org_name,
               (n.buyer_name ~* %s OR coalesce(o.segment, '') ~* %s) AS finance_buyer
        FROM procurement_notices n
        LEFT JOIN organizations o ON o.id = n.buyer_org_id
        WHERE n.status IN ('open', 'amended') AND n.deadline_at > now()
          AND n.official_url ~* '^https?://'
          AND (n.buyer_org_id IS NULL OR ({where}))
          AND (n.source_id = ANY(%s) OR coalesce(n.service_codes[1], '') ~ %s
               OR EXISTS (SELECT 1 FROM event_documents ed JOIN events e ON e.id = ed.event_id
                          WHERE ed.document_id = n.document_id AND e.attrs->>'relevant_services' = 'true'))
          AND ({match})
        ORDER BY n.deadline_at, n.id""", [_FINANCE_BUYER, _FINANCE_BUYER, *params, list(SOFTWARE_ONLY_SOURCES), RELEVANT_CPV, *margs])      # noqa: S608
    now = datetime.now(timezone.utc)
    items, too_late, elsewhere = [], 0, 0
    for r in rows:
        country = alpha2(r["country"])
        reg = region_for(country)
        kind = "finance" if r["finance_buyer"] else "public"
        if buyer and kind != buyer:
            continue
        days_left = max(0, int((r["deadline_at"] - now).total_seconds() // 86400))
        if not in_region(reg, region):
            if days_left >= min_days:                  # open in another region: counted, so the list can say how many more there are
                elsewhere += 1
            continue
        if days_left < min_days:
            too_late += 1
            continue
        own, category = split_title(r["title"])
        checked = r["last_status_check_at"]
        items.append({"id": str(r["id"]), "notice_id": r["notice_id"], "title": own, "category": category, "buyer": r["buyer_name"], "buyer_kind": kind,
                      "account": {"id": str(r["org_id"]), "name": r["org_name"]} if r["org_id"] else None,
                      "country": country, "region": reg, "deadline": r["deadline_at"], "deadline_tz": r["deadline_tz"],
                      "days_left": days_left, "status": r["status"], "url": r["official_url"],
                      "source": SOURCE_LABEL.get(r["source_id"], r["source_id"]), "scope": (r["scope"] or "")[:400] or None,
                      "value": float(r["value_if_reported"]) if r["value_if_reported"] is not None else None,
                      "published": r["published_at"], "checked_at": checked,
                      "check_is_old": bool(checked and (now - checked).total_seconds() > 3 * 86400)})
    counts = {"total": len(items), "finance": sum(1 for i in items if i["buyer_kind"] == "finance"),
              "closing_this_week": sum(1 for i in items if i["days_left"] <= 7), "closing_too_soon": too_late, "other_regions": elsewhere}
    return {"items": items[offset: offset + limit], "counts": counts, "has_more": offset + limit < len(items)}
