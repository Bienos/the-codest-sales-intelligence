"""Open tenders for software work, from every official procurement source, whoever the buyer is.

The account workspace only follows privately owned financial companies, and those rarely publish public tenders, so the tender list is
kept separate: any open notice with a future deadline whose main service is software development or IT services, labelled by buyer type.
A notice is never presented as checked unless an official source returned it; the last check time is always shown.
Each notice can carry a plain English and Polish summary written by the tender_summaries job (ri.tender_summary): advisory only."""
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
FIT_RANK = {"strong": 0, "possible": 1, "weak": 2, "none": 3}
SORTS = ("deadline", "fit", "value", "newest")
# Rough euro rates, only to compare values given in different currencies (the minimum value filter and the value sort). The list always shows
# the notice's own amount and currency.
EUR_RATE = {"EUR": 1.0, "GBP": 1.17, "PLN": 0.23, "SEK": 0.088, "NOK": 0.086, "DKK": 0.134, "ISK": 0.0066, "CHF": 1.06, "CZK": 0.04, "HUF": 0.0025,
            "RON": 0.2, "BGN": 0.51, "USD": 0.92}
# What a search looks through: the notice as published, and the English and Polish summary.
SEARCH_TEXT = ("concat_ws(' ', n.title, n.buyer_name, n.scope, n.summary->>'title_en', n.summary->>'summary_en', n.summary->>'title_pl', n.summary->>'summary_pl', "
               "n.summary->>'wants', n.summary->>'wants_pl', n.summary->>'technologies', n.source_detail->>'title_original', n.source_detail->>'description')")


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


def open_relevant_sql(a: str = "n") -> tuple[str, list[Any]]:
    """Open notices with a future deadline and an official link, whose main service is software or IT work: the rule the list and the summary job share."""
    return (f"""{a}.status IN ('open', 'amended') AND {a}.deadline_at > now() AND {a}.official_url ~* '^https?://'
          AND ({a}.source_id = ANY(%s) OR coalesce({a}.service_codes[1], '') ~ %s
               OR EXISTS (SELECT 1 FROM event_documents ed JOIN events e ON e.id = ed.event_id
                          WHERE ed.document_id = {a}.document_id AND e.attrs->>'relevant_services' = 'true'))""",
            [list(SOFTWARE_ONLY_SOURCES), RELEVANT_CPV])


def notice_value(source_id: str, value_if_reported: Any, detail: dict[str, Any] | None) -> tuple[float | None, str | None]:
    """The value the notice gives, with its currency when known. UK notices are in pounds; TED's estimated value comes from the extra fields read for the summary."""
    d = detail if isinstance(detail, dict) else {}
    if value_if_reported is not None and float(value_if_reported) >= 100:
        return float(value_if_reported), ("GBP" if source_id in SOFTWARE_ONLY_SOURCES else d.get("currency"))
    v = d.get("value")
    if isinstance(v, (int, float)) and v >= 100:
        return float(v), d.get("currency")
    return None, None


def value_eur(value: float | None, currency: str | None) -> float | None:
    if value is None:
        return None
    return value * EUR_RATE.get((currency or "EUR").upper(), 1.0)


def public_summary(s: Any) -> dict[str, Any] | None:
    """The stored summary as the list shows it (unknown keys left out)."""
    if not isinstance(s, dict) or not s.get("title_en"):
        return None
    keys = ("title_en", "summary_en", "wants", "work_type", "technologies", "fit", "requirements", "title_pl", "summary_pl", "wants_pl", "requirements_pl")
    return {k: s[k] for k in keys if s.get(k) not in (None, "", [])}


def open_tenders(conn: Conn, p: Principal, *, q: str = "", region: str = "", buyer: str = "", min_days: int = 3, limit: int = 50, offset: int = 0,
                 fit: str = "", work_type: str = "", max_days: int = 0, min_value: float = 0, sort: str = "deadline") -> dict[str, Any]:
    """`region`: "" for every region, "core" for our markets (UK, DACH, Nordics, Poland), or one region key.
    `min_days`: leave out tenders closing sooner than this (too late to prepare a bid); they are still counted.
    `fit`: "strong" keeps strong fits, "possible" keeps possible or strong ones (by the AI summary). `work_type`: one kind of work.
    `max_days`: only tenders closing within this many days (0: no limit). `min_value`: only tenders whose stated value is at least this
    (roughly in euros; a tender without a stated value is left out when this is set). `sort`: deadline (soonest first), fit (best first, then
    deadline), value (largest first) or newest (latest published first)."""
    where, params = org_scope_sql(p, "o.id")
    rel, rargs = open_relevant_sql("n")
    toks = _tokens(q)
    match, margs = _all_like(SEARCH_TEXT, toks) if toks else ("TRUE", [])
    rows = qa(conn, f"""
        SELECT n.id, n.source_id, n.notice_id, n.buyer_name, n.title, n.scope, n.country, n.deadline_at, n.deadline_tz, n.status, n.official_url,
               n.value_if_reported, n.published_at, n.last_status_check_at, n.service_codes, n.summary, n.summary_at, n.summary_skipped, n.source_detail,
               o.id AS org_id, o.display_name AS org_name,
               (n.buyer_name ~* %s OR coalesce(o.segment, '') ~* %s) AS finance_buyer
        FROM procurement_notices n
        LEFT JOIN organizations o ON o.id = n.buyer_org_id
        WHERE {rel}
          AND (n.buyer_org_id IS NULL OR ({where}))
          AND ({match})
        ORDER BY n.deadline_at, n.id""", [_FINANCE_BUYER, _FINANCE_BUYER, *rargs, *params, *margs])      # noqa: S608
    now = datetime.now(timezone.utc)
    allowed_fit = {"strong": {"strong"}, "possible": {"strong", "possible"}}.get(fit)
    items, too_late, elsewhere = [], 0, 0
    by_fit: dict[str, int] = {}
    by_work: dict[str, int] = {}
    for r in rows:
        country = alpha2(r["country"])
        reg = region_for(country)
        kind = "finance" if r["finance_buyer"] else "public"
        if buyer and kind != buyer:
            continue
        days_left = max(0, int((r["deadline_at"] - now).total_seconds() // 86400))
        summary = public_summary(r["summary"])
        level = ((summary or {}).get("fit") or {}).get("level")
        wtype = (summary or {}).get("work_type")
        value, currency = notice_value(r["source_id"], r["value_if_reported"], r["source_detail"])
        veur = value_eur(value, currency)
        kept = ((not allowed_fit or level in allowed_fit) and (not work_type or wtype == work_type)
                and (not min_value or (veur is not None and veur >= min_value)))
        if not in_region(reg, region):
            if days_left >= min_days and kept and (not max_days or days_left <= max_days):    # counted, so the list can say how many more there are
                elsewhere += 1
            continue
        if days_left < min_days:
            too_late += kept
            continue
        if max_days and days_left > max_days:
            continue
        if not min_value or (veur is not None and veur >= min_value):          # what the fit and work type choices would show
            if not work_type or wtype == work_type:
                by_fit[level or "waiting"] = by_fit.get(level or "waiting", 0) + 1
            if not allowed_fit or level in allowed_fit:
                by_work[wtype or "waiting"] = by_work.get(wtype or "waiting", 0) + 1
        if not kept:
            continue
        own, category = split_title(r["title"])
        checked = r["last_status_check_at"]
        detail = r["source_detail"] if isinstance(r["source_detail"], dict) else {}
        items.append({"id": str(r["id"]), "notice_id": r["notice_id"], "title": own, "category": category, "buyer": r["buyer_name"], "buyer_kind": kind,
                      "account": {"id": str(r["org_id"]), "name": r["org_name"]} if r["org_id"] else None,
                      "country": country, "region": reg, "deadline": r["deadline_at"], "deadline_tz": r["deadline_tz"],
                      "days_left": days_left, "status": r["status"], "url": r["official_url"],
                      "source": SOURCE_LABEL.get(r["source_id"], r["source_id"]), "scope": ((r["scope"] or detail.get("description") or "")[:400]) or None,
                      "value": value, "currency": currency,
                      "published": r["published_at"], "checked_at": checked,
                      "check_is_old": bool(checked and (now - checked).total_seconds() > 3 * 86400),
                      "summary": summary, "summary_at": r["summary_at"] if summary else None,
                      "summary_state": "written" if summary else ("skipped" if r["summary_skipped"] else "waiting"),
                      "lots": detail.get("lot_count"), "duration": detail.get("duration"),
                      "_sort": (FIT_RANK.get(level or "", 4), -(veur or -1), r["published_at"])})
    if sort == "fit":
        items.sort(key=lambda i: i["_sort"][0])
    elif sort == "value":
        items.sort(key=lambda i: i["_sort"][1])
    elif sort == "newest":
        items.sort(key=lambda i: i["_sort"][2] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for i in items:
        del i["_sort"]
    counts = {"total": len(items), "finance": sum(1 for i in items if i["buyer_kind"] == "finance"),
              "closing_this_week": sum(1 for i in items if i["days_left"] <= 7), "closing_too_soon": too_late, "other_regions": elsewhere,
              "summarised": sum(1 for i in items if i["summary"]), "waiting_summary": sum(1 for i in items if i["summary_state"] == "waiting")}
    return {"items": items[offset: offset + limit], "counts": counts, "facets": {"fit": by_fit, "work_type": by_work}, "has_more": offset + limit < len(items)}


def _tokens(q: str) -> list[str]:
    from .api.search import tokens
    return tokens(q)


def _all_like(expr: str, toks: list[str]) -> tuple[str, list[str]]:
    from .api.search import all_like
    return all_like(expr, toks)
