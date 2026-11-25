"""Open tenders for software work from every official procurement source, sorted by who is buying: our ICP first.

Almost every open software notice on TED, Find a Tender, Contracts Finder and e-Zamówienia comes from a public body (ministries, cities,
hospitals, universities, utilities): banks and insurers rarely have to publish public tenders. So each notice carries its buyer's sector
(ri.tender_sector: banks, other financial services, other ICP sectors, not ICP), the list shows the ICP sectors by default, banks first,
and keeps every other notice one click away. A notice is never presented as checked unless an official source returned it; the last check
time is always shown. ICP notices carry a plain English and Polish summary and the facts that decide a bid (ri.tender_summary): advisory only.

Values are shown in euros (roughly, at the fixed rates below) next to the notice's own amount and currency."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .access import Principal, org_scope_sql
from .db import Conn, qa
from .market import alpha2, region_for
from .tender_sector import ICP, SECTOR_RANK, classify_open

# Main service code (CPV) for development and IT services: 72000000 IT services, 722xxxxx software programming, consultancy, development.
RELEVANT_CPV = r"^72(0|2)"
# These two collectors already keep only software notices when they read the feed.
SOFTWARE_ONLY_SOURCES = ("procurement-uk-fts", "procurement-uk-contracts-finder")
# "Our markets": where The Codest sells. The tender list starts here; every other region stays one click away.
CORE_REGIONS = ("uk", "dach", "nordics", "poland")
TENDER_SOURCES = ("procurement-ted", "procurement-uk-fts", "procurement-uk-contracts-finder", "procurement-ezamowienia")
SOURCE_LABEL = {"procurement-ted": "EU tenders (TED)", "procurement-uk-fts": "UK Find a Tender", "procurement-uk-contracts-finder": "UK Contracts Finder",
                "procurement-ezamowienia": "Poland e-Zamówienia"}
FIT_RANK = {"strong": 0, "possible": 1, "weak": 2, "none": 3}
SORTS = ("deadline", "fit", "value", "newest")
# Which sectors each `sector` choice shows. "" is every sector (ICP first), as the list did before sectors existed.
SECTOR_VIEWS: dict[str, tuple[str, ...]] = {"": ("banking", "financial_services", "other_icp", "not_icp"), "icp": ICP, "finance": ("banking", "financial_services"),
                                            "banking": ("banking",), "financial_services": ("financial_services",), "other_icp": ("other_icp",),
                                            "not_icp": ("not_icp",)}

# Euro rates, fixed on purpose (no rate service is called): units of each currency for one euro, the ECB euro reference rates of
# 25 November 2026 (BGN at its fixed conversion rate: Bulgaria adopted the euro on 1 January 2026). They are only used to show an approximate
# euro amount beside the notice's own amount and to sort and filter by value. Update the table and the date together, a few times a year.
EUR_RATES_DATE = "2026-11-25"
EUR_RATES_SOURCE = "ECB euro reference rates"
EUR_PER = {"EUR": 1.0, "SEK": 11.2385, "NOK": 10.7778, "DKK": 7.4743, "PLN": 4.48, "GBP": 0.8373, "CHF": 0.9085, "CZK": 24.435, "HUF": 373.44,
           "RON": 5.343, "BGN": 1.95583, "ISK": 137.2, "USD": 1.0935}
# What a search looks through: the notice as published, and the English and Polish summary.
SEARCH_TEXT = ("concat_ws(' ', n.title, n.buyer_name, n.scope, n.summary->>'title_en', n.summary->>'summary_en', n.summary->>'title_pl', n.summary->>'summary_pl', "
               "n.summary->>'wants', n.summary->>'wants_pl', n.summary->>'technologies', n.summary->>'buyer_note', n.source_detail->>'title_original', "
               "n.source_detail->>'description')")
SUMMARY_KEYS = ("title_en", "summary_en", "wants", "work_type", "technologies", "fit", "requirements", "title_pl", "summary_pl", "wants_pl", "requirements_pl",
                "buyer_sector", "buyer_note", "buyer_note_pl", "languages", "on_site", "renewal", "incumbent", "contract_months", "price_weight",
                "framework", "lots", "v")


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


def notice_value(source_id: str, value_if_reported: Any, detail: dict[str, Any] | None) -> tuple[float | None, str | None, str | None]:
    """The value the notice gives, its currency when known, and what it is (estimated, lots_total, framework_estimate, framework_max, reported).
    UK notices are in pounds; TED's values come from the extra fields read for the summary."""
    d = detail if isinstance(detail, dict) else {}
    if value_if_reported is not None and float(value_if_reported) >= 100:
        return float(value_if_reported), ("GBP" if source_id in SOFTWARE_ONLY_SOURCES else d.get("currency")), "reported"
    v = d.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 100:
        return float(v), d.get("currency"), d.get("value_basis") or "estimated"
    return None, None, None


def value_eur(value: float | None, currency: str | None) -> float | None:
    """Roughly in euros, at the fixed rates above. None when the currency is not given or not in the table."""
    if value is None or not currency:
        return None
    rate = EUR_PER.get(currency.upper())
    return round(value / rate) if rate else None


def public_summary(s: Any) -> dict[str, Any] | None:
    """The stored summary as the list shows it (unknown keys left out)."""
    if not isinstance(s, dict) or not s.get("title_en"):
        return None
    return {k: s[k] for k in SUMMARY_KEYS if s.get(k) not in (None, "", [])}


def worth(r: dict[str, Any], detail: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any]:
    """The facts that decide whether a bid is worth it, from the official fields first and the summary where the notice gives no field."""
    s = summary or {}
    out: dict[str, Any] = {}
    for key, field in (("lots", "lot_count"), ("lot_titles", "lots"), ("duration", "duration"), ("start", "start"), ("end", "end"), ("renewals", "renewals"),
                       ("framework", "framework"), ("languages", "languages"), ("place", "place"), ("award", "award"), ("recurring", "recurring"),
                       ("contact", "contact"), ("legal_type", "legal_type")):
        v = detail.get(field)
        if v not in (None, "", [], {}):
            out[key] = v
    proc = detail.get("procedure") or r.get("procedure")
    if proc:
        out["procedure"] = str(proc)[:80]
    for key in ("lots", "framework", "languages"):
        if key not in out and s.get(key) not in (None, "", []):
            out[key] = s[key]
    if "duration" not in out and isinstance(s.get("contract_months"), int):
        out["months"] = s["contract_months"]
    if "award" not in out and isinstance(s.get("price_weight"), int):
        out["award"] = {"price": s["price_weight"], "quality": 100 - s["price_weight"]}
    for key in ("on_site", "renewal", "incumbent"):
        if s.get(key) not in (None, ""):
            out[key] = s[key]
    return out


def open_tenders(conn: Conn, p: Principal, *, q: str = "", region: str = "", buyer: str = "", min_days: int = 3, limit: int = 50, offset: int = 0,
                 fit: str = "", work_type: str = "", max_days: int = 0, min_value: float = 0, sort: str = "deadline", sector: str = "") -> dict[str, Any]:
    """`sector`: "" for every buyer, "icp" for our ICP sectors (banks, other financial services, other ICP sectors), or one sector
    (banking, financial_services, other_icp, not_icp; "finance" is banks and other financial services). Whatever the choice, banks come
    first, then other financial services, then the other ICP sectors, then everyone else; `sort` orders the tenders within each sector.
    `region`: "" for every region, "core" for our markets (UK, DACH, Nordics, Poland), or one region key.
    `min_days`: leave out tenders closing sooner than this (too late to prepare a bid); they are still counted.
    `fit`: "strong" keeps strong fits, "possible" keeps possible or strong ones (by the AI summary). `work_type`: one kind of work.
    `max_days`: only tenders closing within this many days (0: no limit). `min_value`: only tenders whose stated value is at least this many
    euros (converted at the fixed rates; a tender without a stated value is left out when this is set). `sort`: deadline (soonest first), fit
    (best first, then deadline), value (largest first) or newest (latest published first). `buyer` ("finance" or "public") is the older
    buyer filter, kept for links that still use it."""
    classify_open(conn)
    shown = SECTOR_VIEWS.get(sector, SECTOR_VIEWS[""])
    where, params = org_scope_sql(p, "o.id")
    rel, rargs = open_relevant_sql("n")
    toks = _tokens(q)
    match, margs = _all_like(SEARCH_TEXT, toks) if toks else ("TRUE", [])
    rows = qa(conn, f"""
        SELECT n.id, n.source_id, n.notice_id, n.buyer_name, n.title, n.scope, n.country, n.deadline_at, n.deadline_tz, n.status, n.official_url,
               n.value_if_reported, n.published_at, n.last_status_check_at, n.service_codes, n.procedure, n.summary, n.summary_at, n.summary_skipped,
               n.source_detail, n.buyer_sector, n.buyer_kind, n.buyer_public, n.sector_sure, n.sector_source,
               o.id AS org_id, o.display_name AS org_name
        FROM procurement_notices n
        LEFT JOIN organizations o ON o.id = n.buyer_org_id
        WHERE {rel}
          AND (n.buyer_org_id IS NULL OR ({where}))
          AND ({match})
        ORDER BY n.deadline_at, n.id""", [*rargs, *params, *margs])      # noqa: S608
    now = datetime.now(timezone.utc)
    allowed_fit = {"strong": {"strong"}, "possible": {"strong", "possible"}}.get(fit)
    items, too_late, elsewhere = [], 0, 0
    by_fit: dict[str, int] = {}
    by_work: dict[str, int] = {}
    by_sector = {k: 0 for k in SECTOR_RANK}
    for r in rows:
        country = alpha2(r["country"])
        reg = region_for(country)
        sec = r["buyer_sector"] or "not_icp"
        kind = "finance" if sec in ("banking", "financial_services") else "public"
        if buyer and kind != buyer:
            continue
        days_left = max(0, int((r["deadline_at"] - now).total_seconds() // 86400))
        summary = public_summary(r["summary"])
        level = ((summary or {}).get("fit") or {}).get("level")
        wtype = (summary or {}).get("work_type")
        detail = r["source_detail"] if isinstance(r["source_detail"], dict) else {}
        value, currency, basis = notice_value(r["source_id"], r["value_if_reported"], detail)
        veur = value_eur(value, currency)
        kept = ((not allowed_fit or level in allowed_fit) and (not work_type or wtype == work_type)
                and (not min_value or (veur is not None and veur >= min_value)))
        in_time = days_left >= min_days and (not max_days or days_left <= max_days)
        if in_region(reg, region) and in_time and kept:
            by_sector[sec] += 1                       # what each sector choice would show
        if sec not in shown:
            continue
        if not in_region(reg, region):
            if in_time and kept:                      # counted, so the list can say how many more there are
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
        icp = sec in ICP or r["sector_sure"] is False
        state = "written" if summary else ("skipped" if r["summary_skipped"] else "waiting" if icp else "not_needed")
        published = r["published_at"] or datetime.min.replace(tzinfo=timezone.utc)
        items.append({"id": str(r["id"]), "notice_id": r["notice_id"], "title": own, "category": category, "buyer": r["buyer_name"], "buyer_kind": kind,
                      "sector": sec, "sector_kind": r["buyer_kind"], "sector_public": bool(r["buyer_public"]), "sector_sure": r["sector_sure"] is not False,
                      "sector_source": r["sector_source"],
                      "account": {"id": str(r["org_id"]), "name": r["org_name"]} if r["org_id"] else None,
                      "country": country, "region": reg, "deadline": r["deadline_at"], "deadline_tz": r["deadline_tz"],
                      "days_left": days_left, "status": r["status"], "url": r["official_url"],
                      "source": SOURCE_LABEL.get(r["source_id"], r["source_id"]), "scope": ((r["scope"] or detail.get("description") or "")[:400]) or None,
                      "value": value, "currency": currency, "value_eur": veur, "value_basis": basis,
                      "published": r["published_at"], "checked_at": checked,
                      "check_is_old": bool(checked and (now - checked).total_seconds() > 3 * 86400),
                      "summary": summary, "summary_at": r["summary_at"] if summary else None, "summary_state": state,
                      "lots": detail.get("lot_count"), "duration": detail.get("duration"), "worth": worth(r, detail, summary),
                      "_sort": (SECTOR_RANK.get(sec, 9), FIT_RANK.get(level or "", 4), -(veur if veur is not None else -1), -published.timestamp())})
    key = {"fit": lambda i: i["_sort"][:2], "value": lambda i: (i["_sort"][0], i["_sort"][2]),
           "newest": lambda i: (i["_sort"][0], i["_sort"][3])}.get(sort, lambda i: i["_sort"][0])
    items.sort(key=key)                              # stable: ties keep the soonest deadline first
    for i in items:
        del i["_sort"]
    counts = {"total": len(items), "finance": sum(1 for i in items if i["buyer_kind"] == "finance"),
              "closing_this_week": sum(1 for i in items if i["days_left"] <= 7), "closing_too_soon": too_late, "other_regions": elsewhere,
              "summarised": sum(1 for i in items if i["summary"]), "waiting_summary": sum(1 for i in items if i["summary_state"] == "waiting"),
              "by_sector": by_sector, "icp": sum(by_sector[k] for k in ICP), "not_icp": by_sector["not_icp"]}
    return {"items": items[offset: offset + limit], "counts": counts, "facets": {"fit": by_fit, "work_type": by_work}, "has_more": offset + limit < len(items),
            "eur_rates": {"date": EUR_RATES_DATE, "source": EUR_RATES_SOURCE, "per_eur": EUR_PER}}


def _tokens(q: str) -> list[str]:
    from .api.search import tokens
    return tokens(q)


def _all_like(expr: str, toks: list[str]) -> tuple[str, list[str]]:
    from .api.search import all_like
    return all_like(expr, toks)
