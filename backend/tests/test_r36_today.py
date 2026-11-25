"""Today (GET /api/today/brief): who to contact today and why, most urgent first, with what is new since the last visit."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user
from test_r29_buying_signals import company, event, person

from ri.api import today as td
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.outbound.signal_campaigns import ANGLES
from ri.scoring import recompute_scores
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources.registry import seed_registry

SECRET = "t" * 40
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def _day(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _fmt(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def list_signal(conn, oid, sid, text, *, days_ago=3, strength="High", status="Review", opener=None, who=None, url="https://news.example/item", notes="",
                checked_days_ago=1):
    """One row of the lead list's research log, as the workbook import stores it."""
    link = conn.execute("insert into account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, "
                        "imported_hash, raw_company_name, observed_at) values (%s,'xlsx','f','s','BUYING SIGNAL LOG',%s,1,'h','x',now()) returning id",
                        (oid, sid)).fetchone()["id"]
    original = {"Signal ID": sid, "Status": status, "Event date": _day(-days_ago) if days_ago is not None else "Unknown", "Verified event": text,
                "Signal Strength": strength, "Signal type": "Platform programme", "Source URL": url, "Source Notes": notes,
                "Recommended Action": opener or "Your programme is moving fast. Would outside engineers help?", "Relevant Buyer Persona": who or "CTO",
                "Last Verified": _day(-checked_days_ago)}
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'buying_signal_log',%s,now())",
                 (oid, link, json.dumps(original)))


@pytest.fixture()
def world(conn, db_url):
    import_baseline(conn)
    sync_rule_versions(conn)
    seed_registry(conn)
    mk_user(conn, "bdm@x", "bdm", 555)
    mk_user(conn, "analyst@x", "research_analyst")
    mk_user(conn, "mkt@x", "marketing")
    o = {
        "tender": company(conn, "Tender Bank", segment="Banking", priority="P2", score=60),
        "p1": company(conn, "Prime Insurer", country="DE", segment="Insurance", priority="P1", score=90),
        "p2": company(conn, "Second Bank", segment="Banking", priority="P2", score=70),
        "old": company(conn, "Old News Bank", segment="Banking", priority="P1", score=95),
        "closed": company(conn, "Closed Tender Bank", segment="Banking", priority="P1", score=80),
        "outside": company(conn, "Outside Fintech", eligibility="Out of scope", priority="P1"),
        "theirs": company(conn, "Owned Elsewhere", owner=999, priority="P1"),
        "quiet": company(conn, "Quiet Bank", segment="Banking", priority="P1", score=99),
        "checked": company(conn, "Kestrel Pay", priority="P3", score=40),
    }
    soon = TODAY + timedelta(days=5)
    list_signal(conn, o["tender"], "S1", f"Tender Bank opened RFx 101 for engineering suppliers; submissions close {_fmt(soon)} 12:00.",
                status="Open notice — eligibility review", days_ago=6, opener="Your RFx 101 closes soon. Can The Codest apply?", who="IT Procurement")
    # an older row about the same notice, checked before the deadline was revised: not a second, earlier deadline
    list_signal(conn, o["tender"], "S0", f"Tender Bank RFx 101 for engineering suppliers, closing {_fmt(TODAY + timedelta(days=2))}.",
                status="Open notice — eligibility review", days_ago=6, checked_days_ago=6)
    list_signal(conn, o["p1"], "S2", "Prime Insurer is hiring ten engineers for its new claims platform.", days_ago=2, who="Head of Claims IT")
    list_signal(conn, o["p1"], "S3", "Prime Insurer opened a second office.", days_ago=4, strength="Medium",
                opener="Map the claims programme owner; verify the delivery model.")
    list_signal(conn, o["p2"], "S4", "Second Bank starts a payments modernisation programme.", days_ago=1, strength="Medium")
    list_signal(conn, o["old"], "S5", "Old News Bank announced a cloud move.", days_ago=40)
    list_signal(conn, o["closed"], "S6", "Closed Tender Bank tender closed last week.", status="Past / closed", days_ago=3)
    list_signal(conn, o["outside"], "S7", "Outside Fintech raised money.", days_ago=1)
    list_signal(conn, o["theirs"], "S8", "Owned Elsewhere is hiring.", days_ago=1)
    event(conn, o["checked"], "Kestrel Pay", "funding", days=3)
    person(conn, o["p1"], "Ada Clarke", "ada@prime.example")
    person(conn, o["p1"], "Cat Lind")
    recompute_scores(conn, NOW)
    conn.commit()
    db = Database(db_url, max_size=3)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(app), "h": hdr, "o": o}
    db.close()


def _brief(world, who="bdm"):
    r = world["c"].get("/api/today/brief", headers=world["h"](who))
    assert r.status_code == 200, r.text
    return r.json()


def _names(items):
    return [(i.get("organization") or {}).get("name") for i in items]


# ---------------------------------------------------------------- reading a deadline out of research notes

def test_deadline_is_read_from_the_clause_that_gives_it():
    today = date(2026, 11, 25)
    assert td.deadline_in("mBank opened RFx 004447; submissions close 30 Nov 2026 12:00.", today) == date(2026, 11, 30)
    # the questions round, a separate notice and an older superseded date are not the deadline
    assert td.deadline_in("initial offers due Nov 30 at 12:00 Paris time, questions Nov 26 at 12:00. Separate SAS migration RFI due Dec 18.",
                          today) == date(2026, 11, 30)
    assert td.deadline_in("current portal deadline 1 Dec 2026, while some secondary listings still show prior 23 Nov noon deadline", today) == date(2026, 12, 1)
    assert td.deadline_in("reported amended deadline Dec 1 15:00. Original notice Sep 30.", today) == date(2026, 12, 1)
    assert td.deadline_in("participation requests due 21 Dec 2026 10:00 CET.", today) == date(2026, 12, 21)
    assert td.deadline_in("The procedure closed on 13 November.", today) is None           # past
    assert td.deadline_in("The bank will migrate by 30 June 2027.", today) is None         # a date, but not a deadline
    assert td.deadline_in("", today) is None


# ---------------------------------------------------------------- the list

def test_do_today_is_ranked_one_per_company_with_a_reason_and_a_next_step(world):
    b = _brief(world)
    names = _names(b["actions"])
    # the tender closing in 5 days first, then the strong fresh signal at a top-priority company, then the rest
    assert names[0] == "Tender Bank"
    assert names[1] == "Prime Insurer"
    assert set(names) == {"Tender Bank", "Prime Insurer", "Second Bank", "Kestrel Pay"}
    # closed tenders, news older than three weeks, companies outside the target market and other sellers' accounts stay out
    assert not set(names) & {"Old News Bank", "Closed Tender Bank", "Outside Fintech", "Owned Elsewhere"}
    assert len(names) == len(set(names))                     # one item per company

    tender = b["actions"][0]
    assert tender["key"].endswith(":S1") and tender["kind"] == "tender" and tender["days_left"] == 5 and tender["deadline"] == _day(5)
    assert tender["opener"].startswith("Your RFx 101") and tender["opener_kind"] == "line" and tender["who"] == "IT Procurement"
    assert tender["source"] == {"name": "news.example", "url": "https://news.example/item"}
    prime = b["actions"][1]
    assert prime["kind"] == "signal" and prime["strength"] == 3 and prime["happened"] == _day(-2)
    assert prime["text"].startswith("Prime Insurer is hiring") and prime["who"] == "Head of Claims IT"
    assert prime["organization"]["priority"] == "P1"
    assert (prime["organization"]["people"], prime["organization"]["people_with_email"]) == (2, 1)
    checked = next(a for a in b["actions"] if a["organization"]["name"] == "Kestrel Pay")
    assert checked["rule_id"] == "COM-01" and checked["opener"] == ANGLES["COM-01"] and checked["source"]["url"] == "https://kestrelpay.example/funding"


def test_new_since_the_last_visit_and_the_glance_numbers(world, conn):
    b = _brief(world)
    assert b["last_visit"] is None                          # first visit: the last 7 days
    assert abs(datetime.fromisoformat(b["since"]) - (NOW - timedelta(days=7))) < timedelta(minutes=5)
    # news already in "Do today" is not repeated; the second, weaker Prime Insurer item is left out with its company
    assert all(n["organization"]["name"] not in _names(b["actions"]) for n in b["new"] if n.get("organization"))
    assert b["glance"]["targets"] == 7 and b["glance"]["with_news"] == 4
    assert b["glance"]["people_with_email"] == 1 and b["glance"]["accounts_with_email"] == 1

    # a visit three days ago is where "new" starts next time; a visit earlier today does not move it
    uid = conn.execute("select id from users where email='bdm@x'").fetchone()["id"]
    conn.execute("delete from product_events where user_id=%s", (uid,))
    conn.execute("insert into product_events(user_id, event, at) values (%s,'today_viewed',%s)", (uid, NOW - timedelta(days=3)))
    conn.commit()
    b = _brief(world)
    assert abs(datetime.fromisoformat(b["last_visit"]) - (NOW - timedelta(days=3))) < timedelta(seconds=5)
    assert abs(datetime.fromisoformat(b["since"]) - (NOW - timedelta(days=3))) < timedelta(seconds=5)
    b = _brief(world)
    assert abs(datetime.fromisoformat(b["since"]) - (NOW - timedelta(days=3))) < timedelta(seconds=5)
    assert conn.execute("select count(*) n from product_events where user_id=%s and event='today_viewed'", (uid,)).fetchone()["n"] == 3


def test_done_and_not_now_take_an_item_off_the_list_and_undo_brings_it_back(world, conn):
    c, h = world["c"], world["h"]
    prime = next(a for a in _brief(world)["actions"] if a["organization"]["name"] == "Prime Insurer")
    r = c.post("/api/today/brief/items", headers=h("bdm"), json={"key": prime["key"], "outcome": "done", "organization_id": prime["organization"]["id"]})
    assert r.status_code == 200
    b = _brief(world)
    # the item and the company's other news stay off the list
    assert "Prime Insurer" not in _names(b["actions"]) + _names(b["queue"])
    c.post("/api/today/brief/items", headers=h("bdm"), json={"key": prime["key"], "outcome": "undo"})
    assert "Prime Insurer" in _names(_brief(world)["actions"])
    # opening the link is recorded but keeps the item; "not now" puts it aside
    tender = _brief(world)["actions"][0]
    c.post("/api/today/brief/items", headers=h("bdm"), json={"key": tender["key"], "outcome": "opened"})
    assert _brief(world)["actions"][0]["key"] == tender["key"]
    c.post("/api/today/brief/items", headers=h("bdm"), json={"key": tender["key"], "outcome": "later"})
    assert tender["key"] not in [a["key"] for a in _brief(world)["actions"]]
    events = conn.execute("select detail->>'outcome' o from product_events where event='today_item' order by id").fetchall()
    assert [e["o"] for e in events] == ["done", "undo", "opened", "later"]
    # each person has their own list: the analyst still sees the tender
    assert tender["key"] in [a["key"] for a in _brief(world, "analyst")["actions"]]
    # bad input and accounts the person may not see are refused
    assert c.post("/api/today/brief/items", headers=h("bdm"), json={"key": "x y", "outcome": "done"}).status_code == 422
    assert c.post("/api/today/brief/items", headers=h("bdm"), json={"key": "list:a:b", "outcome": "maybe"}).status_code == 422
    assert c.post("/api/today/brief/items", headers=h("bdm"),
                  json={"key": "list:a:b", "outcome": "done", "organization_id": world["o"]["theirs"]}).status_code == 404


def test_replies_come_first_and_need_the_contacts_permission(world, conn):
    pid = conn.execute("select p.id from people p where p.full_name='Ada Clarke'").fetchone()["id"]
    conn.execute("insert into lemlist_activities(id, type, email, campaign_id, campaign_name, person_id, happened_at) "
                 "values ('a1','emailsReplied','ada@prime.example','c1','Claims platforms',%s,%s)", (pid, NOW - timedelta(days=1)))
    conn.commit()
    b = _brief(world)
    first = b["actions"][0]
    assert first["kind"] == "reply" and first["person"]["name"] == "Ada Clarke" and first["title"] == "Claims platforms"
    assert first["organization"]["name"] == "Prime Insurer"
    # without the contacts permission there are no names, no replies and no people counts
    a = _brief(world, "analyst")
    assert all(x["kind"] != "reply" for x in a["actions"])
    assert a["glance"]["people_with_email"] is None and a["actions"][0]["organization"]["people"] is None
    # people without today.read get nothing
    assert world["c"].get("/api/today/brief", headers=world["h"]("mkt")).status_code == 403


def test_at_most_three_tenders_in_the_first_seven_and_the_rest_wait_in_the_queue(world, monkeypatch):
    def tenders(conn, p):
        items = [{"id": f"t{i}", "title": f"Tender {i}", "buyer": f"Buyer {i}", "deadline": NOW + timedelta(days=i + 1), "days_left": i + 1,
                  "url": f"https://tenders.example/{i}", "source": "EU tenders", "published": NOW - timedelta(days=1), "summary": None, "account": None}
                 for i in range(5)]
        return items, 5
    monkeypatch.setattr(td, "_tenders", tenders)
    b = _brief(world)
    kinds = [a["kind"] for a in b["actions"]]
    assert kinds.count("tender") == 3 and len(b["actions"]) <= td.DO_LIMIT
    assert [a.get("buyer") for a in b["actions"] if a["kind"] == "tender"] == ["Buyer 0", "Buyer 1", "Buyer 2"]
    # the later tenders (and the lead list's own tender, closing in 5 days) wait in the queue, in deadline order
    queued = [a.get("buyer") or a["organization"]["name"] for a in b["queue"] if a["kind"] == "tender"]
    assert queued[:2] == ["Buyer 3", "Buyer 4"] and "Tender Bank" in queued
    assert b["glance"]["open_tenders"] == 5


def test_nothing_urgent_points_to_the_top_of_the_lead_list(world, conn):
    conn.execute("delete from legacy_account_research")
    conn.execute("delete from signals")
    conn.commit()
    b = _brief(world)
    assert b["actions"] == [] and b["queue"] == []
    assert [x["organization"]["name"] for x in b["next_up"]][:2] == ["Quiet Bank", "Old News Bank"]
