"""Company page (owner feedback, 25 Nov 2026): "why talk to them now" is at most three reasons, strongest first, each with a date and a link,
plus one next step; and opportunities never repeat. One programme reached through several routes, a second initiative of the same kind,
and a lead-list signal logged several times each show once. Names are synthetic."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api import account_page as page
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.db import Database
from test_r28_profiles import SECRET, org, settings
from test_r4_crm_notify import mk_user

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TED = "https://ted.europa.eu/en/notice/-/detail/795292-2026"
DTVP = "https://dtvp.de/ausschreibungen/cms-migration/"


# ------------------------------------------------------------------ pure rules
def test_routes_and_same_kind_initiatives_fold_into_one_programme():
    hyps = [{"id": "h1", "initiative_id": "i1", "initiative_title": "Payment rail integration (analyst working title)", "route_type": "direct"},
            {"id": "h2", "initiative_id": "i1", "initiative_title": "Payment rail integration (analyst working title)", "route_type": "vendor"},
            {"id": "h3", "initiative_id": "i2", "initiative_title": "Payment rail integration (analyst working title)", "route_type": "direct"},
            {"id": "h4", "initiative_id": "i3", "initiative_title": "Atlas", "route_type": "direct"}]
    groups = page.group_opportunities(hyps)
    assert [[h["id"] for h in g] for g in groups] == [["h1", "h2", "h3"], ["h4"]]       # best first, the leader keeps its place
    assert page.plain_title("Payment rail integration (analyst working title)") == "Payment rail integration"


def test_lead_list_signal_logged_three_times_is_one_signal():
    rows = [{"event": "Prequalification closed on 23 Nov.", "date": "2026-10-22", "category": "CMS procurement closed", "source_url": DTVP},
            {"event": "Independent listing confirms the deadline.", "date": "2026-10-22", "category": "CMS procurement closed", "source_url": DTVP.rstrip("/")},
            {"event": "Dental software tender open to 14 Dec.", "date": "2026-11-16", "category": "Dental software tender", "source_url": TED},
            {"event": "Same dental tender, product-led.", "date": "2026-11-16", "category": "Dental tender (product)", "source_url": TED},
            {"event": "CMS and dental, summary.", "date": "2026-10-22", "category": "Both", "source_url": "https://www.dtvp.de/ausschreibungen/cms-migration"}]
    out = page.dedupe_list_signals(rows)
    assert [(x["category"], x["repeats"]) for x in out] == [("CMS procurement closed", 3), ("Dental software tender", 2)]


def test_why_now_orders_by_strength_keeps_three_with_dates_and_links_and_never_repeats_a_page():
    lead = {"event_id": "e1", "rule_id": "SRC-06", "demand_tier": "D1", "summary": "Asks for contract Java engineers", "happened_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
            "source_url": "https://bank.example/jobs/1", "commercial_questions": ["Which team needs them, and from when?"]}
    signals = [{"event_id": "e1", "story_key": "s1", "status": "active", "demand_tier": "D1", "summary": "Asks for contract Java engineers", "evidence": [{"url": "https://bank.example/jobs/1"}]},
               {"event_id": "e2", "story_key": "s2", "status": "active", "demand_tier": "D3", "summary": "Cloud migration under way", "event_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "evidence": [{"url": "https://news.example/cloud"}]},
               {"event_id": "e3", "story_key": "s3", "status": "historical", "demand_tier": "D1", "summary": "Old tender", "evidence": [{"url": "https://old.example"}]}]
    list_signals = [{"category": "Hiring Java contractors", "event": "Same job ad", "date": "2026-11-01", "source_url": "https://bank.example/jobs/1/", "confidence": "High"},
                    {"category": "Ancient news", "date": "2024-01-10", "source_url": "https://old.example/2024", "confidence": "High"}]
    recent = [{"date": "2026-11", "headline": "Results: IT spend up", "source_url": "https://news.example/results"}]
    tenders = [{"title": "Software development services", "deadline_at": datetime(2026, 12, 14, tzinfo=timezone.utc), "published_at": datetime(2026, 11, 16, tzinfo=timezone.utc),
                "official_url": TED},
               {"title": "Closed tender", "deadline_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "official_url": "https://ted.europa.eu/closed"}]
    w = page.why_now(lead_signal=lead, signals=signals, list_signals=list_signals, recent=recent, tenders=tenders, list_profile={}, opportunities=[], now=NOW)
    rs = w["reasons"]
    assert len(rs) == 3
    assert [r["kind"] for r in rs] == ["signal", "tender", "signal"]          # outside demand (D1), then the open tender, then a checked programme
    assert all(r["date"] and r["url"] for r in rs)
    assert [r["strength"] for r in rs] == sorted([r["strength"] for r in rs], reverse=True)
    assert not any("old.example" in r["url"] or "closed" in r["url"] for r in rs)      # historical, closed and year-old items are not reasons for *now*
    assert w["next_step"] == {"kind": "tender", "date": "2026-12-14", "url": TED, "text": None}     # a deadline beats everything else


def test_next_step_prefers_the_lead_list_then_the_signal_question_then_a_seller_action():
    lead = {"event_id": "e1", "demand_tier": "D2", "summary": "x", "source_url": "https://a.example", "commercial_questions": ["Is the test window dated?"]}
    common = {"lead_signal": lead, "signals": [], "list_signals": [], "recent": [], "tenders": [], "now": NOW}
    opp = [{"hypothesis_id": "h1", "route": {"counterparty": "Bank"}, "next_action": {"action_type": "disqualify_review", "objective": "internal"}},
           {"hypothesis_id": "h2", "route": {"counterparty": "Nimbus"}, "next_action": {"action_type": "investigate_vendor_route", "objective": "Ask Nimbus"}}]
    assert page.why_now(list_profile={"Next Action": "Call the CIO about the CMS shortlist."}, opportunities=opp, **common)["next_step"] == {"kind": "lead_list", "text": "Call the CIO about the CMS shortlist."}
    assert page.why_now(list_profile={}, opportunities=opp, **common)["next_step"] == {"kind": "ask", "text": "Is the test window dated?"}
    step = page.why_now(list_profile={}, opportunities=opp, **{**common, "lead_signal": None})["next_step"]
    assert step["kind"] == "opportunity" and step["action_type"] == "investigate_vendor_route" and step["hypothesis_id"] == "h2"     # internal review steps are skipped
    assert page.why_now(list_profile={}, opportunities=[], **{**common, "lead_signal": None}) == {"reasons": [], "next_step": None}


# ------------------------------------------------------------------ the endpoint
@pytest.fixture()
def client(conn, db_url):
    mk_user(conn, "bdm@x", "bdm", 555)
    conn.commit()
    db = Database(db_url, max_size=2)
    yield TestClient(create_app(settings(), db=db)), {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
    db.close()


def _initiative(conn, oid, title):
    return str(conn.execute("insert into initiatives(owner_org_id, title, programme_domain, stage) values (%s,%s,'payments_rail','implementation') returning id",
                            (oid, title)).fetchone()["id"])


def _hyp(conn, oid, ini, route, counterparty, score):
    conn.execute("insert into opportunity_hypotheses(initiative_id, route_type, counterparty_name, organization_id, score_total, state, priority) "
                 "values (%s,%s,%s,%s,%s,'actionable_hypothesis','medium')", (ini, route, counterparty, oid, score))


def test_account_endpoint_shows_each_programme_once_and_each_list_signal_once(conn, client):
    c, h = client
    oid = org(conn, "Fjordvik Bank", "fjordvik.example")
    a = _initiative(conn, oid, "Payment rail integration (analyst working title)")
    b = _initiative(conn, oid, "Payment rail integration (analyst working title)")       # a second event opened a second initiative of the same kind
    other = _initiative(conn, oid, "Atlas core replacement")
    _hyp(conn, oid, a, "direct", "Fjordvik Bank", 60)
    _hyp(conn, oid, a, "vendor", "Nimbus Core", 55)
    _hyp(conn, oid, b, "direct", "Fjordvik Bank", 40)
    _hyp(conn, oid, other, "direct", "Fjordvik Bank", 50)
    def link(row):      # one lead-list row each, as the importer stores them
        return conn.execute("insert into account_source_links(source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at, organization_id) "
                            "values ('xlsx','f','s','Lead list',%s,%s,'h','Fjordvik Bank',now(),%s) returning id", (f"r{row}", row, oid)).fetchone()["id"]
    for i, (event, url) in enumerate([("Tender open to 14 Dec", TED), ("Same tender, second note", TED), ("Same tender, third note", TED)]):
        conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'buying_signal_log',%s, now() - make_interval(mins => %s))",
                     (oid, link(i + 2), json.dumps({"Verified event": event, "Event date": "2026-11-16", "Signal type": "Software tender", "Source URL": url, "Evidence confidence": "High"}), i))
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'master_account',%s, now())",
                 (oid, link(1), json.dumps({"Next Action": "Ask who is shortlisted for the build."})))
    conn.commit()

    d = c.get(f"/api/accounts/{oid}", headers=h).json()
    titles = [o["initiative"]["plain_title"] for o in d["opportunities"]]
    assert titles == ["Payment rail integration", "Atlas core replacement"]              # three records, one card; best score leads
    pay = d["opportunities"][0]
    assert pay["sources"] == 3 and pay["programmes"] == 2
    assert {(r["type"], r["counterparty"]) for r in pay["routes"]} == {("direct", "Fjordvik Bank"), ("vendor", "Nimbus Core")}   # each way in once
    assert len(d["list_signals"]) == 1 and d["list_signals"][0]["repeats"] == 3 and d["list_signals"][0]["event"] == "Tender open to 14 Dec"
    why = d["why_now"]
    assert len(why["reasons"]) == 1 and why["reasons"][0]["url"] == TED and why["reasons"][0]["date"] == "2026-11-16"
    assert why["next_step"] == {"kind": "lead_list", "text": "Ask who is shortlisted for the build."}
