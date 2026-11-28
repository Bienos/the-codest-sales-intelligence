"""The Pipedrive fields an IT-services forecast needs, set up by the app (docs/it-services-sales-data-pl.md).

`ensure_fields` lists the account's deal and organisation fields, reuses any field with the same name, creates the missing ones once
(never again after that, so a field someone deletes on purpose stays deleted) and records each field's key in crm_field_map.
`apply_deal_fields` / `apply_org_fields` copy the values of a synced record into crm_deals / crm_organizations.

Pipedrive value shapes (API v2 records, `custom_fields`): enum -> option id, set -> list of option ids, monetary -> {value, currency}
or a number, date -> 'YYYY-MM-DD', double -> number, text/varchar -> string. API v1 puts custom fields at the top level of the record.
"""
from __future__ import annotations

import re

from datetime import date
from typing import Any

from ..db import Conn, jb, q1, qa
from ..sheets.client import ConnectorBlocked

SKILLS = ["Java", "Python", "JavaScript / TypeScript", "React", "Vue", "Angular", "Node.js", "PHP", ".NET", "Ruby", "Go", "Mobile (iOS / Android)",
          "QA / Testing", "DevOps / Cloud", "Data engineering", "AI / ML", "UX / UI", "Project / Delivery management", "Shopify / e-commerce"]

# role, entity, name in Pipedrive, field type, options
SPEC: list[tuple[str, str, str, str, list[str]]] = [
    ("deal_type", "deal", "Deal type", "enum", ["New client", "New role at client", "Extension", "New project at client"]),
    ("billing_model", "deal", "Billing model", "enum", ["Time & materials", "Dedicated team", "Fixed price", "Retainer"]),
    ("forecast_category", "deal", "Forecast category", "enum", ["Pipeline", "Best case", "Commit"]),
    ("start_date", "deal", "Planned start", "date", []),
    ("duration_months", "deal", "Duration (months)", "double", []),
    ("fte", "deal", "People (FTE)", "double", []),
    ("monthly_value", "deal", "Monthly value", "monetary", []),
    ("q_pain", "deal", "Qualification: pain and budget", "text", []),
    ("q_champion", "deal", "Qualification: champion", "varchar", []),
    ("q_decision_maker", "deal", "Qualification: decision maker", "varchar", []),
    ("q_decision_process", "deal", "Qualification: decision process and date", "text", []),
    ("skills", "deal", "Skills", "set", SKILLS),
    ("client_status", "organization", "Client status", "enum", ["Active client", "Former client", "Prospect"]),
    ("contract_end", "organization", "Contract end", "date", []),
    ("fte_now", "organization", "People at client (FTE)", "double", []),
]
CREATE_MARK = "fields_created:v1"
QUAL = {"q_pain": "pain", "q_champion": "champion", "q_decision_maker": "decision_maker", "q_decision_process": "decision_process"}


def _v1(reader: Any) -> str:
    return reader.base.replace("/api/v2", "/api/v1")


def _list(reader: Any, entity: str) -> list[dict[str, Any]]:
    path = "dealFields" if entity == "deal" else "organizationFields"
    out, start = [], 0
    while True:
        body = reader._get_url(f"{_v1(reader)}/{path}", {"start": start, "limit": 500})
        out.extend(body.get("data") or [])
        pg = (body.get("additional_data") or {}).get("pagination") or {}
        if not pg.get("more_items_in_collection") or pg.get("next_start") is None or int(pg["next_start"]) <= start:
            return out
        start = int(pg["next_start"])


def _create(reader: Any, entity: str, name: str, ftype: str, options: list[str]) -> dict[str, Any]:
    path = "dealFields" if entity == "deal" else "organizationFields"
    body: dict[str, Any] = {"name": name, "field_type": ftype}
    if options:
        body["options"] = [{"label": o} for o in options]
    r = reader.http.post(f"{_v1(reader)}/{path}", headers={"x-api-token": reader.token}, json=body)
    if r.status_code in (401, 403):
        raise ConnectorBlocked(f"Pipedrive refused to create the field '{name}' ({r.status_code}): the token's user may not edit data fields")
    r.raise_for_status()
    return r.json().get("data") or {}


def ensure_fields(conn: Conn, reader: Any, *, create: bool | None = None) -> dict[str, Any]:
    """Map every field in SPEC to a Pipedrive field. Missing fields are created only on the first run (create=None) or when asked."""
    first = not q1(conn, "SELECT 1 FROM sync_state WHERE system='pipedrive' AND entity=%s", (CREATE_MARK,))
    create = first if create is None else create
    out: dict[str, Any] = {"found": [], "created": [], "missing": []}
    existing = {e: _list(reader, e) for e in ("deal", "organization")}
    known = {r["role"]: r for r in qa(conn, "SELECT role, field_key, created_by_app FROM crm_field_map")}
    for role, entity, name, ftype, options in SPEC:
        fields = existing[entity]
        hit = next((f for f in fields if known.get(role) and f.get("key") == known[role]["field_key"]), None) \
            or next((f for f in fields if (f.get("name") or "").strip().lower() == name.lower()), None)
        made = False
        if hit is None:
            if not create:
                out["missing"].append(name)
                continue
            hit = _create(reader, entity, name, ftype, options)
            made = True
            out["created"].append(name)
        else:
            out["found"].append(name)
        key = hit.get("key")
        if not key:
            continue
        conn.execute("INSERT INTO crm_field_map(role, entity, field_key, name, field_type, created_by_app, synced_at) VALUES (%s,%s,%s,%s,%s,%s,now()) "
                     "ON CONFLICT (role) DO UPDATE SET entity=EXCLUDED.entity, field_key=EXCLUDED.field_key, name=EXCLUDED.name, field_type=EXCLUDED.field_type, "
                     "created_by_app=crm_field_map.created_by_app OR EXCLUDED.created_by_app, synced_at=now()",
                     (role, entity, key, hit.get("name") or name, hit.get("field_type") or ftype, made))
        for o in hit.get("options") or []:
            conn.execute("INSERT INTO crm_field_options(field_key, option_id, label, synced_at) VALUES (%s,%s,%s,now()) "
                         "ON CONFLICT (field_key, option_id) DO UPDATE SET label=EXCLUDED.label, synced_at=now()", (key, str(o.get("id")), str(o.get("label") or "")))
    if create:
        conn.execute("INSERT INTO sync_state(system, entity, watermark) VALUES ('pipedrive',%s,'done') ON CONFLICT DO NOTHING", (CREATE_MARK,))
    return out


def field_map(conn: Conn, entity: str) -> dict[str, dict[str, Any]]:
    return {r["role"]: r for r in qa(conn, "SELECT role, field_key, field_type FROM crm_field_map WHERE entity=%s", (entity,))}


def _raw(rec: dict[str, Any], key: str) -> tuple[bool, Any]:
    cf = rec.get("custom_fields") or {}
    if key in cf:
        return True, cf[key]
    if key in rec:
        return True, rec[key]
    return False, None


def _num(v: Any) -> float | None:
    if isinstance(v, dict):
        v = v.get("value", v.get("amount"))
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _opt(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("id", v.get("value"))
    return None if v in (None, "") else str(v)[:80]


def _opts(v: Any) -> list[str] | None:
    if v in (None, "", []):
        return None
    if isinstance(v, str):
        v = [x for x in v.split(",") if x.strip()]
    if not isinstance(v, list):
        v = [v]
    return [str(x.get("id", x) if isinstance(x, dict) else x).strip()[:80] for x in v]


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _text(v: Any) -> str | None:
    s = str(v).strip() if v not in (None, "") else ""
    return s[:2000] or None


DEAL_COLUMNS = {"deal_type": ("deal_type_raw", _opt), "billing_model": ("billing_model_raw", _opt), "forecast_category": ("forecast_category_raw", _opt),
                "start_date": ("start_date", _date), "duration_months": ("duration_months", _num), "fte": ("fte", _num),
                "monthly_value": ("monthly_value", _num), "skills": ("skills", _opts)}
ORG_COLUMNS = {"client_status": ("client_status_raw", _opt), "contract_end": ("contract_end", _date), "fte_now": ("fte_now", _num)}


def apply_deal_fields(conn: Conn, d: dict[str, Any], fmap: dict[str, dict[str, Any]] | None = None) -> None:
    fmap = field_map(conn, "deal") if fmap is None else fmap
    if not fmap:
        return
    sets, args = [], []
    for role, (col, parse) in DEAL_COLUMNS.items():
        if role in fmap:
            present, v = _raw(d, fmap[role]["field_key"])
            if present:
                sets.append(f"{col}=%s")
                args.append(parse(v))
    qual, any_q = {}, False
    for role, k in QUAL.items():
        if role in fmap:
            present, v = _raw(d, fmap[role]["field_key"])
            any_q |= present
            if _text(v):
                qual[k] = _text(v)
    if any_q:
        sets.append("qualification=%s")
        args.append(jb(qual) if qual else None)
    if sets:
        conn.execute(f"UPDATE crm_deals SET {', '.join(sets)} WHERE crm_id=%s", (*args, d["id"]))


def apply_org_fields(conn: Conn, o: dict[str, Any]) -> None:
    fmap = field_map(conn, "organization")
    sets, args = [], []
    for role, (col, parse) in ORG_COLUMNS.items():
        if role in fmap:
            present, v = _raw(o, fmap[role]["field_key"])
            if present:
                sets.append(f"{col}=%s")
                args.append(parse(v))
    if sets:
        conn.execute(f"UPDATE crm_organizations SET {', '.join(sets)} WHERE crm_id=%s", (*args, o["id"]))


# ------------------------------------------------------------------ deal products: who is billed, how much, how often, from when
PER_MONTH = {"monthly": 1.0, "weekly": 52 / 12, "quarterly": 1 / 3, "semi-annually": 1 / 6, "semi_annually": 1 / 6, "annually": 1 / 12}
MONTHS_PER_CYCLE = {"monthly": 1.0, "weekly": 12 / 52, "quarterly": 3.0, "semi-annually": 6.0, "semi_annually": 6.0, "annually": 12.0}


HOURS_PER_FTE_MONTH = 160.0
MAX_PEOPLE_PER_LINE = 31.0           # a monthly quantity above this is hours, not people: no single line staffs that many
_HOURLY = re.compile(r"\b(hours?|hrs?|godz\w*|man-?hours?|roboczogodz\w*)\b|/\s*h\b|per\s+hour", re.I)


def _line_fte(qty: float, freq: str, name: str) -> float:
    """People on one recurring line. Quantity is people, unless the line is priced by the hour (its name says so, or the monthly
    quantity is too large to be people): then hours a month / 160."""
    per_month = qty * PER_MONTH[freq]
    if _HOURLY.search(name or "") or per_month > MAX_PEOPLE_PER_LINE:
        return per_month / HOURS_PER_FTE_MONTH
    return qty


def summarize_products(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One deal's products -> {monthly, total, start, months, fte, n}. A recurring line bills price x quantity each cycle, for its number
    of cycles from its start date; a one-time line adds to the total only."""
    if not items:
        return None
    monthly = total = fte = 0.0
    starts, months = [], []
    for p in items:
        qty = _num(p.get("quantity")) or 0.0
        price = _num(p.get("item_price")) or 0.0
        disc = _num(p.get("discount")) or 0.0
        line = qty * price
        if p.get("discount_type") == "amount":
            line -= disc
        elif disc:
            line *= 1 - disc / 100
        freq = str(p.get("billing_frequency") or "one-time").lower()
        cycles = _num(p.get("billing_frequency_cycles"))
        if freq in PER_MONTH:
            monthly += line * PER_MONTH[freq]
            fte += _line_fte(qty, freq, str(p.get("name") or ""))
            if cycles:
                months.append(cycles * MONTHS_PER_CYCLE[freq])
                total += line * cycles
            s = _date(p.get("billing_start_date"))
            if s:
                starts.append(s)
        else:
            total += line
    return {"monthly": round(monthly, 2), "total": round(total, 2), "start": min(starts).isoformat() if starts else None,
            "months": max(months) if months else None, "fte": round(fte, 2) or None, "n": len(items)}


def sync_deal_products(conn: Conn, reader: Any, deal_ids: list[int]) -> int:
    """API v2 lists products of up to 100 deals per call. Deals with no products are left as they are (products stays NULL)."""
    n = 0
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i:i + 100]
        items: dict[int, list[dict[str, Any]]] = {}
        cursor = None
        while True:
            params: dict[str, Any] = {"deal_ids": ",".join(str(x) for x in chunk), "limit": 500, **({"cursor": cursor} if cursor else {})}
            body = reader._get("/deals/products", params)
            for p in body.get("data") or []:
                if p.get("deal_id") is not None:
                    items.setdefault(int(p["deal_id"]), []).append(p)
            cursor = (body.get("additional_data") or {}).get("next_cursor")
            if not cursor:
                break
        for deal_id in chunk:
            summary = summarize_products(items.get(deal_id, []))
            conn.execute("UPDATE crm_deals SET products=%s WHERE crm_id=%s", (jb(summary) if summary else None, deal_id))
            n += 1 if summary else 0
    return n


def sync_users(conn: Conn, reader: Any) -> int:
    """Names of Pipedrive users (the data-quality view groups by owner). Name and active flag only."""
    body = reader._get_url(f"{_v1(reader)}/users", {})
    n = 0
    for u in body.get("data") or []:
        if u.get("id") is None:
            continue
        conn.execute("INSERT INTO crm_users(crm_id, name, active, synced_at) VALUES (%s,%s,%s,now()) ON CONFLICT (crm_id) DO UPDATE SET name=EXCLUDED.name, "
                     "active=EXCLUDED.active, synced_at=now()", (u["id"], str(u.get("name") or f"User {u['id']}")[:200], bool(u.get("active_flag", True))))
        n += 1
    return n


def sync_pipelines(conn: Conn, reader: Any) -> int:
    n = 0
    for p in reader.paged("/pipelines"):
        if p.get("id") is None:
            continue
        conn.execute("INSERT INTO crm_pipelines(crm_id, name, synced_at) VALUES (%s,%s,now()) ON CONFLICT (crm_id) DO UPDATE SET name=EXCLUDED.name, synced_at=now()",
                     (p["id"], str(p.get("name") or f"Pipeline {p['id']}")[:200]))
        n += 1
    return n
