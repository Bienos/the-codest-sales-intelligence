"""Regulatory deadline calendar: hard EU dates that force banks, payment firms and insurers to spend on IT, pinned to our target accounts.

The calendar is data in the repository (``rules/regulatory_calendar.yaml``), researched by a person: nothing here invents or infers a date.

Who a deadline applies to is decided from two columns of ``organizations``:

* ``country_code``: the deadline's countries (ISO codes, or a group such as ``eu`` or ``non_euro_eu`` defined in the file);
* ``segment`` (the lead list's sector, compared without case): the kind of firm. ``Banking`` is a bank, ``Payments`` a payment or
  e-money firm, ``Insurance`` an insurer; the lead list's ``list_icp_segment`` "Large Bank" also counts as a bank. Other segments
  (Fintech, Financial Software, Asset Management...) get no deadline: the list does not say which of them hold a banking, payment or
  e-money licence.
* the name: central banks and statutory health, pension and accident insurers (``not_covered`` in the file) and public bodies are left out,
  even when the lead list keeps them as targets: these rules are about commercial banks, payment firms and life insurers.

Only target accounts (``target_market.target_sql``) are pinned and counted, within the person's row scope for the pages.

``pin()`` is the daily scheduler lane ``deadlines_pinned``. For every ``adopted`` deadline still ahead (at least ``MIN_DAYS_LEFT`` days) and
within ``PIN_MONTHS`` months, it records one ``deadline_approaching`` event per target account it applies to, keyed
``deadline:<deadline id>:<company id>``. Provisional and possible deadlines are shown on the calendar but never pinned. Re-running updates
the same rows (days left, wording); it never duplicates or deletes. An event whose deadline is about to pass, or that no longer applies
(the account left the target list, the entry changed), is marked expired and kept.

Each new or changed event goes through the normal path (``ingest.process.finish_event``: checked, market-tagged, run against the signal
rules), carries its own fit reason and Polish summary (so the AI fit job never spends on it), and cites the calendar's source link as a
structured claim. No signal rule fires on a deadline alone: the original tracker's REG-01 says a deadline by itself is market context, a
buying signal needs the company's own programme as well. Today shows at most ``api.today.DEADLINE_CAP`` deadline items a day.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .db import Conn, jb, q1, qa
from .target_market import target_sql

PATH = Path(__file__).resolve().parents[2] / "rules" / "regulatory_calendar.yaml"
EVENT_TYPE = "deadline_approaching"
MODEL = "regulatory-calendar/1"
CERTAINTIES = ("adopted", "provisional", "possible")
PIN_MONTHS = 18            # adopted deadlines within this many months are pinned to accounts
MIN_DAYS_LEFT = 2          # a deadline is unpinned the day before it: a daily lane then never leaves it showing as news once it has passed
RUN_LIMIT = 800            # new or changed events written per run (each goes through the event checks); the rest follow at the next tick
SAMPLE = 6                 # account names shown per deadline on the calendar
SHORT_MAX = 32             # characters in a deadline's short label (the Calendar page's month grid)
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_ISO2 = re.compile(r"^[A-Z]{2}$")

MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
MONTHS_PL = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia")
# country names for the plain "why" line ("bank in Poland" / "bank w Polsce")
COUNTRY_EN = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "the Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "GB": "the United Kingdom", "CH": "Switzerland", "NO": "Norway",
    "IS": "Iceland", "LI": "Liechtenstein",
}
COUNTRY_PL_IN = {
    "AT": "w Austrii", "BE": "w Belgii", "BG": "w Bułgarii", "HR": "w Chorwacji", "CY": "na Cyprze", "CZ": "w Czechach", "DK": "w Danii",
    "EE": "w Estonii", "FI": "w Finlandii", "FR": "we Francji", "DE": "w Niemczech", "GR": "w Grecji", "HU": "na Węgrzech", "IE": "w Irlandii",
    "IT": "we Włoszech", "LV": "na Łotwie", "LT": "na Litwie", "LU": "w Luksemburgu", "MT": "na Malcie", "NL": "w Holandii", "PL": "w Polsce",
    "PT": "w Portugalii", "RO": "w Rumunii", "SK": "na Słowacji", "SI": "w Słowenii", "ES": "w Hiszpanii", "SE": "w Szwecji",
    "GB": "w Wielkiej Brytanii", "CH": "w Szwajcarii", "NO": "w Norwegii", "IS": "na Islandii", "LI": "w Liechtensteinie",
}


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    label_pl: str
    plural: str
    plural_pl: str
    segments: frozenset[str]


@dataclass(frozen=True)
class Deadline:
    id: str
    title: str
    title_pl: str
    regulation: str
    date: date
    certainty: str
    countries: frozenset[str]
    kinds: tuple[str, ...]
    applies_to: str
    applies_to_pl: str
    must: str
    must_pl: str
    sources: tuple[str, ...]
    approximate: bool = False
    note: str = ""
    note_pl: str = ""
    ask_for: str = ""
    ask_for_pl: str = ""
    groups: tuple[str, ...] = field(default_factory=tuple)      # the country groups it was written with ("eu"), for the page
    short: str = ""                                              # the month grid's label ("IPR: send + VoP"); see short_label()
    short_pl: str = ""

    def days_left(self, today: date) -> int:
        return (self.date - today).days

    def pinnable(self, today: date) -> bool:
        """Turned into account events: adopted, still at least MIN_DAYS_LEFT days ahead, and within PIN_MONTHS months."""
        return self.certainty == "adopted" and not self.approximate and self.days_left(today) >= MIN_DAYS_LEFT and self.date <= add_months(today, PIN_MONTHS)


@dataclass(frozen=True)
class Calendar:
    deadlines: tuple[Deadline, ...]
    kinds: dict[str, Kind]
    not_covered: tuple[tuple[re.Pattern[str], str, str], ...] = ()      # (name pattern, reason, Polish reason)

    def left_out(self, name: str | None) -> str | None:
        """Why the rules are not about this company (a central bank, a statutory insurer, a public body), or None."""
        from .market import is_public_body
        for rx, reason, _ in self.not_covered:
            if rx.search(name or ""):
                return reason
        return "public body" if is_public_body(name) else None

    def get(self, did: str) -> Deadline | None:
        return next((d for d in self.deadlines if d.id == did), None)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def parse(raw: dict[str, Any]) -> Calendar:
    """The calendar from the file's contents, checked. A broken entry is an error naming it, never silently skipped."""
    groups = {str(k): [str(c).upper() for c in v] for k, v in (raw.get("groups") or {}).items()}
    kinds = {}
    for key, k in (raw.get("kinds") or {}).items():
        kinds[str(key)] = Kind(str(key), _text(k["label"]), _text(k.get("label_pl") or k["label"]), _text(k.get("plural") or k["label"]),
                               _text(k.get("plural_pl") or k.get("label_pl") or k["label"]), frozenset(_text(s).lower() for s in k.get("segments") or []))
    out: list[Deadline] = []
    seen: set[str] = set()
    for e in raw.get("deadlines") or []:
        did = str(e.get("id") or "")
        if not _ID.match(did) or did in seen:
            raise ValueError(f"regulatory calendar: bad or repeated id {did!r}")
        seen.add(did)
        when = e.get("date")
        if isinstance(when, str):
            when = date.fromisoformat(when)
        if not isinstance(when, date):
            raise ValueError(f"regulatory calendar: {did} has no ISO date")
        certainty = str(e.get("certainty") or "")
        if certainty not in CERTAINTIES:
            raise ValueError(f"regulatory calendar: {did} certainty must be one of {CERTAINTIES}")
        sources = tuple(str(s) for s in e.get("sources") or [])
        if any(not re.match(r"^https://\S+$", s) for s in sources) or (certainty == "adopted" and not sources):
            raise ValueError(f"regulatory calendar: {did} needs https source links")
        names = e.get("countries")
        names = [names] if isinstance(names, str) else list(names or [])
        countries: set[str] = set()
        used_groups: list[str] = []
        for n in names:
            if str(n) in groups:
                countries.update(groups[str(n)])
                used_groups.append(str(n))
            elif _ISO2.match(str(n).upper()):
                countries.add(str(n).upper())
            else:
                raise ValueError(f"regulatory calendar: {did} has an unknown country or group {n!r}")
        dk = tuple(str(k) for k in e.get("kinds") or [])
        if not countries or not dk or any(k not in kinds for k in dk):
            raise ValueError(f"regulatory calendar: {did} needs countries and known kinds of firm")
        for f in ("title", "regulation", "applies_to", "must"):
            if not _text(e.get(f)):
                raise ValueError(f"regulatory calendar: {did} has no {f}")
        if any(len(_text(e.get(f))) > SHORT_MAX for f in ("short", "short_pl")):
            raise ValueError(f"regulatory calendar: {did} short label is longer than {SHORT_MAX} characters")
        out.append(Deadline(
            id=did, title=_text(e["title"]), title_pl=_text(e.get("title_pl") or e["title"]), regulation=_text(e["regulation"]), date=when,
            certainty=certainty, countries=frozenset(countries), kinds=dk, applies_to=_text(e["applies_to"]),
            applies_to_pl=_text(e.get("applies_to_pl") or e["applies_to"]), must=_text(e["must"]).rstrip("."), must_pl=_text(e.get("must_pl") or e["must"]).rstrip("."),
            sources=sources, approximate=bool(e.get("approximate")), note=_text(e.get("note")), note_pl=_text(e.get("note_pl") or e.get("note")),
            ask_for=_text(e.get("ask_for")), ask_for_pl=_text(e.get("ask_for_pl") or e.get("ask_for")), groups=tuple(used_groups),
            short=_text(e.get("short")), short_pl=_text(e.get("short_pl") or e.get("short"))))
    out.sort(key=lambda d: (d.date, d.id))
    skip = tuple((re.compile(str(x["names"]), re.I), _text(x["reason"]), _text(x.get("reason_pl") or x["reason"])) for x in raw.get("not_covered") or [])
    return Calendar(tuple(out), kinds, skip)


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> Calendar:
    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def load(path: Path = PATH) -> Calendar:
    """The calendar file, read again whenever it changes."""
    return _load(str(path), path.stat().st_mtime)


# ------------------------------------------------------------------ who it applies to

def kind_of(cal: Calendar, segment: str | None, icp: str | None = None) -> str | None:
    """The kind of firm, from the lead list's segment (compared without case); "Large Bank" on the list is a bank."""
    seg = _text(segment).lower()
    for k in cal.kinds.values():
        if seg and seg in k.segments:
            return k.key
    if _text(icp).lower() == "large bank" and "bank" in cal.kinds:
        return "bank"
    return None


def applies(d: Deadline, country: str | None, kind: str | None) -> bool:
    return bool(kind) and kind in d.kinds and _text(country).upper() in d.countries


def why(cal: Calendar, kind: str, country: str) -> tuple[str, str]:
    """Why a deadline applies, in a few words: ("bank in Poland", "bank w Polsce")."""
    k = cal.kinds[kind]
    c = _text(country).upper()
    return f"{k.label} in {COUNTRY_EN.get(c, c)}", f"{k.label_pl} {COUNTRY_PL_IN.get(c, 'w kraju ' + c)}"


def day_en(d: date) -> str:
    return f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"


def day_pl(d: date) -> str:
    return f"{d.day} {MONTHS_PL[d.month - 1]} {d.year}"


def accounts(conn: Conn, cal: Calendar, where: str = "TRUE", args: list[Any] | tuple[Any, ...] = (), *, keep_left_out: bool = False) -> list[dict[str, Any]]:
    """Target accounts with a kind of firm the calendar knows (``where`` narrows them, e.g. to a person's row scope), best first. Companies
    the rules are not about are dropped, or kept with their ``left_out`` reason when ``keep_left_out``."""
    segments = sorted({s for k in cal.kinds.values() for s in k.segments})
    rows = qa(conn, f"""
        SELECT o.id::text AS id, o.display_name AS name, upper(trim(o.country_code)) AS country, o.segment, o.list_icp_segment,
               o.list_priority, o.list_score, o.logo_url
        FROM organizations o
        WHERE {target_sql('o')} AND ({where}) AND o.country_code IS NOT NULL
          AND (lower(trim(coalesce(o.segment, ''))) = ANY(%s) OR o.list_icp_segment = 'Large Bank')
        ORDER BY CASE o.list_priority WHEN 'P1' THEN 0 WHEN 'P2' THEN 1 WHEN 'P3' THEN 2 ELSE 3 END, o.list_score DESC NULLS LAST, o.display_name, o.id""",
              [*args, segments])      # noqa: S608 - fixed SQL fragments from internal callers
    out = []
    for r in rows:
        k = kind_of(cal, r["segment"], r["list_icp_segment"])
        why_not = cal.left_out(r["name"]) if k else None
        if k and (keep_left_out or not why_not):
            out.append({**r, "kind": k, "left_out": why_not})
    return out


# ------------------------------------------------------------------ the daily lane

def _fit(d: Deadline, why_en: str) -> dict[str, Any]:
    return {"level": "possible", "service": "team_extension", "basis": "calendar",
            "reason": f"A law gives this {why_en} a fixed date: they must {d.must} by {day_en(d.date)}. "
                      "We do not know yet whether their own team or a supplier already covers the work."}


def _payload(cal: Calendar, d: Deadline, o: dict[str, Any], today: date) -> tuple[str, dict[str, Any], str]:
    why_en, why_pl = why(cal, o["kind"], o["country"])
    summary = f"{o['name']} must {d.must} by {day_en(d.date)}, under the {d.regulation}."
    attrs = {
        "deadline_id": d.id, "deadline_title": d.title, "regulation": d.regulation, "deadline_date": d.date.isoformat(),
        "date_approximate": d.approximate, "certainty": d.certainty, "days_left": d.days_left(today), "source_url": d.sources[0],
        "sources": list(d.sources), "why": why_en, "why_pl": why_pl, "kind": o["kind"], "country": o["country"], "ask_for": d.ask_for, "must": d.must,
        "summary_pl": f"{o['name']}: do {day_pl(d.date)} trzeba {d.must_pl}. Podstawa: {d.regulation}.", "summary_pl_checked": True,
        "fit": _fit(d, why_en),
    }
    material = {k: v for k, v in attrs.items() if k != "days_left"}
    fp = hashlib.sha256(json.dumps([summary, material], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    attrs["calendar_fp"] = fp
    return summary, attrs, fp


def _claim(conn: Conn, d: Deadline) -> str:
    """One structured claim per version of a deadline (its date, certainty, sources and wording), shared by every account it is pinned to."""
    from .events.store import add_claim
    fp = hashlib.sha256(json.dumps([d.date.isoformat(), d.certainty, d.sources, d.must, d.applies_to], ensure_ascii=False).encode()).hexdigest()[:16]
    row = q1(conn, "SELECT id FROM claims WHERE predicate='regulatory_deadline' AND object_json->>'deadline_id'=%s AND object_json->>'fp'=%s "
                   "ORDER BY observed_at LIMIT 1", (d.id, fp))
    if row:
        return str(row["id"])
    return add_claim(conn, subject=d.title, predicate="regulatory_deadline",
                     obj={"deadline_id": d.id, "fp": fp, "date": d.date.isoformat(), "certainty": d.certainty, "regulation": d.regulation,
                          "url": d.sources[0], "official_url": d.sources[0], "sources": list(d.sources)},
                     doc_id=None, anchor=f"{d.applies_to} must {d.must} by {day_en(d.date)}.", kind="structured", model_version=MODEL)


def pin(conn: Conn, now: datetime | None = None, *, limit: int = RUN_LIMIT, cal: Calendar | None = None) -> dict[str, Any]:
    """The daily lane: pin each upcoming adopted deadline to the target accounts it applies to. Idempotent; never deletes."""
    from .ingest.process import finish_event
    now = now or datetime.now(timezone.utc)
    cal = cal or load()
    today = now.date()
    due = [d for d in cal.deadlines if d.pinnable(today)]
    orgs = accounts(conn, cal)
    existing = {r["event_key"]: r for r in qa(conn, "SELECT id::text AS id, event_key, lifecycle_status, attrs->>'calendar_fp' AS fp FROM events "
                                                    "WHERE event_type=%s AND event_key LIKE 'deadline:%%'", (EVENT_TYPE,))}
    wanted: set[str] = set()
    pinned_orgs: set[str] = set()
    pinned_deadlines: set[str] = set()
    written = 0
    more = False
    for d in due:
        claim_id: str | None = None
        unchanged: list[str] = []
        for o in orgs:
            if not applies(d, o["country"], o["kind"]):
                continue
            key = f"deadline:{d.id}:{o['id']}"
            wanted.add(key)
            pinned_orgs.add(o["id"])
            pinned_deadlines.add(d.id)
            summary, attrs, fp = _payload(cal, d, o, today)
            ex = existing.get(key)
            if ex and ex["fp"] == fp and ex["lifecycle_status"] == "active":
                unchanged.append(ex["id"])
                continue
            if written >= limit:
                more = True
                continue
            claim_id = claim_id or _claim(conn, d)
            event_at = datetime.combine(d.date, time(0, 0), tzinfo=timezone.utc)
            row = conn.execute(
                "INSERT INTO events(event_key, event_type, subject_org_id, subject_name_raw, event_at, event_at_known, pub_at, summary, attrs, "
                "extraction_model_version, independent_origin_group, verification_status) VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,'unverified') "
                "ON CONFLICT (event_key) DO UPDATE SET subject_name_raw=EXCLUDED.subject_name_raw, event_at=EXCLUDED.event_at, summary=EXCLUDED.summary, "
                "attrs=(events.attrs - 'ended') || EXCLUDED.attrs, lifecycle_status='active', last_checked_at=now() RETURNING id",
                (key, EVENT_TYPE, o["id"], o["name"], event_at, now, summary, jb(attrs), MODEL, f"calendar:{d.id}")).fetchone()
            eid = str(row["id"])  # type: ignore[index]
            conn.execute("INSERT INTO event_claims(event_id, claim_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (eid, claim_id))
            finish_event(conn, eid, now=now)          # the normal path: checks, market tag, signal rules, programme links
            written += 1
        if unchanged:
            conn.execute("UPDATE events SET attrs = attrs || jsonb_build_object('days_left', %s::int), last_checked_at=now() WHERE id = ANY(%s::uuid[])",
                         (d.days_left(today), unchanged))
    # pinned before but not now: the deadline is about to pass, or no longer applies. Kept, marked expired with the reason.
    ended = 0
    for key, r in existing.items():
        if key in wanted or r["lifecycle_status"] != "active":
            continue
        d = cal.get(key.split(":")[1])
        reason = "deadline_passed" if d is None or d.days_left(today) < MIN_DAYS_LEFT else "no_longer_applies"
        conn.execute("UPDATE events SET lifecycle_status='expired', attrs = attrs || jsonb_build_object('ended', %s::text), last_checked_at=now() WHERE id=%s",
                     (reason, r["id"]))
        ended += 1
    return {"accounts_pinned": len(pinned_orgs), "deadlines_pinned": len(pinned_deadlines), "events_written": written, "events_ended": ended,
            "more_to_pin": more}


# ------------------------------------------------------------------ what the pages read

def short_label(d: Deadline) -> tuple[str, str]:
    """The month grid's label: the file's own ``short``, else the regulation's abbreviation and the title ("IPR: Instant euro payments...")."""
    if d.short:
        return d.short, d.short_pl or d.short
    abbr = re.findall(r"\(([^()]{2,12})\)", d.regulation)
    return (f"{abbr[-1]}: {d.title}", f"{abbr[-1]}: {d.title_pl}") if abbr else (d.title, d.title_pl)


def _deadline_json(d: Deadline, today: date) -> dict[str, Any]:
    left = d.days_left(today)
    short, short_pl = short_label(d)
    return {
        "id": d.id, "title": d.title, "title_pl": d.title_pl, "short": short, "short_pl": short_pl, "regulation": d.regulation,
        "date": d.date.isoformat(), "year": d.date.year,
        "approximate": d.approximate, "certainty": d.certainty, "past": left < 0, "days_left": left, "pinned": d.pinnable(today),
        "countries": sorted(d.countries), "country_groups": list(d.groups), "kinds": list(d.kinds), "applies_to": d.applies_to,
        "applies_to_pl": d.applies_to_pl, "must": d.must, "must_pl": d.must_pl,
        "say": f"{d.applies_to} must {d.must}.", "say_pl": f"{d.applies_to_pl}: trzeba {d.must_pl}.",
        "ask_for": d.ask_for or None, "ask_for_pl": d.ask_for_pl or None, "note": d.note or None, "note_pl": d.note_pl or None,
        "sources": list(d.sources),
    }


def calendar(conn: Conn, where: str = "TRUE", args: list[Any] | tuple[Any, ...] = (), *, now: datetime | None = None,
             cal: Calendar | None = None) -> dict[str, Any]:
    """Every deadline (all certainties, past ones flagged), each with how many of our target accounts it applies to and a few of them.
    ``where``/``args`` is the person's row scope over ``o.id``."""
    now = now or datetime.now(timezone.utc)
    cal = cal or load()
    today = now.date()
    every = accounts(conn, cal, where, args, keep_left_out=True)
    orgs = [o for o in every if not o["left_out"]]
    items = []
    affected: set[str] = set()
    for d in cal.deadlines:
        hit = [o for o in orgs if applies(d, o["country"], o["kind"])]
        if not d.days_left(today) < 0:
            affected.update(o["id"] for o in hit)
        by_kind = {k: sum(o["kind"] == k for o in hit) for k in d.kinds}
        items.append({**_deadline_json(d, today), "accounts": {
            "count": len(hit), "by_kind": by_kind,
            "sample": [{"id": o["id"], "name": o["name"], "country": o["country"], "kind": o["kind"], "logo_url": o["logo_url"]} for o in hit[:SAMPLE]]}})
    kinds = {k.key: {"label": k.label, "label_pl": k.label_pl, "plural": k.plural, "plural_pl": k.plural_pl} for k in cal.kinds.values()}
    return {"today": today.isoformat(), "pin_months": PIN_MONTHS, "items": items, "kinds": kinds,
            "counts": {"deadlines": len(items), "upcoming": sum(not i["past"] for i in items), "pinned": sum(i["pinned"] for i in items),
                       "accounts": len(affected),
                       "left_out": sum(1 for o in every if o["left_out"] and any(applies(d, o["country"], o["kind"]) for d in cal.deadlines))}}


def for_account(conn: Conn, org: dict[str, Any], *, now: datetime | None = None, cal: Calendar | None = None) -> dict[str, Any]:
    """The deadlines that apply to one company (by its country and kind of firm), soonest first, with whether each is pinned to it."""
    now = now or datetime.now(timezone.utc)
    cal = cal or load()
    today = now.date()
    kind = kind_of(cal, org.get("segment"), org.get("list_icp_segment"))
    country = _text(org.get("country_code")).upper() or None
    left_out = cal.left_out(org.get("display_name"))
    if left_out:
        return {"kind": kind, "country": country, "left_out": left_out, "items": []}
    pinned = {r["did"] for r in qa(conn, "SELECT attrs->>'deadline_id' AS did FROM events WHERE subject_org_id=%s AND event_type=%s AND lifecycle_status='active'",
                                   (org["id"], EVENT_TYPE))}
    items = []
    for d in cal.deadlines:
        if not applies(d, country, kind):
            continue
        why_en, why_pl = why(cal, kind, country)  # type: ignore[arg-type]
        items.append({**_deadline_json(d, today), "why": why_en, "why_pl": why_pl, "on_account": d.id in pinned})
    items.sort(key=lambda i: (i["past"], i["date"]))
    return {"kind": kind, "country": country, "left_out": None, "items": items}


def today_events(conn: Conn, org_ids: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    """For Today: per company, its soonest pinned deadline still ahead (with how many others it has)."""
    if not org_ids:
        return []
    now = now or datetime.now(timezone.utc)
    return qa(conn, """
        SELECT * FROM (
          SELECT e.subject_org_id::text AS oid, e.event_key, e.summary, e.event_at, e.first_observed_at, e.attrs,
                 count(*) OVER (PARTITION BY e.subject_org_id) AS n,
                 row_number() OVER (PARTITION BY e.subject_org_id ORDER BY e.event_at, e.event_key) AS rn
          FROM events e
          WHERE e.event_type=%s AND e.subject_org_id = ANY(%s::uuid[]) AND e.lifecycle_status='active' AND e.verification_status='verified'
            AND NOT e.is_historical_replay AND e.event_at > %s AND e.attrs->>'certainty'='adopted') x
        WHERE rn = 1 ORDER BY event_at, oid""", (EVENT_TYPE, org_ids, now))
