"""Client book (every client we deliver for: work, value, end, contact, hiring, signals, flags) and campaign results (every Lemlist campaign
side by side, by step and sender, with the deals that followed). Names are invented."""
from datetime import timedelta

from ri import campaign_results as cr
from ri import clients
from ri.config import Settings
from test_r64_forecast import api, seed_stages  # noqa: F401  (api is a fixture)
from test_r70_services_forecast import NOW, _delivery

S = Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7)


def account(conn, name):
    return str(conn.execute("insert into organizations(display_name, canonical_domain, entity_type) values (%s,%s,'company') returning id",
                            (name, f"{name.lower()}.example")).fetchone()["id"])


# ------------------------------------------------------------------ client book
def test_client_book_groups_work_by_client_with_the_forecasts_numbers(conn):
    seed_stages(conn)
    _delivery(conn)
    b = clients.book(conn, S, NOW)
    by = {c["name"]: c for c in b["clients"]}
    assert set(by) == {"Bank", "Shop"}
    bank, shop = by["Bank"], by["Shop"]
    assert bank["monthly"] == 20000 and [w["crm_id"] for w in bank["work"]] == [101]                 # same monthly value as the forecast
    assert [r["crm_id"] for r in bank["open_roles"]] == [103] and [h["crm_id"] for h in bank["on_hold"]] == [104]
    assert bank["ends_on"] == "2027-01-31" and 0 < bank["days_left"] <= 90
    assert bank["flags"] == ["ending_soon", "on_hold", "not_linked"]                                    # activities not read yet: no contact flag
    assert shop["monthly"] == 10000 and "end_assumed" in shop["flags"] and "no_value" in shop["flags"]
    assert b["clients"][0]["name"] == "Bank" and round(bank["share"], 2) == 0.67                         # largest first, share of the book
    assert b["monthly_total"] == 30000 and b["flags"]["ending_soon"] == 1 and b["activities_synced"] is False


def test_client_book_adds_contact_hiring_and_signals_for_linked_accounts(conn):
    seed_stages(conn)
    _delivery(conn)
    acc = account(conn, "Bank")
    conn.execute("update crm_organizations set organization_id=%s where crm_id=1", (acc,))
    conn.execute("insert into sync_state(system, entity, watermark) values ('pipedrive','activities','x')")
    conn.execute("insert into crm_activities(crm_id, crm_org_id, type, subject, done, done_at) values (1, 1, 'meeting', 'QBR', true, %s)", (NOW - timedelta(days=4),))
    conn.execute("insert into crm_activities(crm_id, crm_deal_id, type, subject, done, due_at) values (2, 101, 'call', 'Plan Q1', false, %s)", (NOW + timedelta(days=3),))
    conn.execute("insert into job_postings(provider, board, job_id, organization_id, employer_name, title, open_status, first_seen_at, last_seen_at) "
                 "values ('greenhouse','bank','1',%s,'Bank','Senior Java Developer','open',now(),now()), ('greenhouse','bank','2',%s,'Bank','Old role','closed',now(),now())", (acc, acc))
    b = clients.book(conn, S, NOW)
    bank = next(c for c in b["clients"] if c["name"] == "Bank")
    shop = next(c for c in b["clients"] if c["name"] == "Shop")
    assert bank["account_id"] == acc and bank["days_since_contact"] == 4 and bank["next_step"].startswith((NOW + timedelta(days=3)).date().isoformat())
    assert bank["hiring"] == {"n": 1, "titles": ["Senior Java Developer"]}
    assert "no_contact" not in bank["flags"] and "not_linked" not in bank["flags"]
    assert "no_contact" in shop["flags"]                                                                # activities read, nothing logged for Shop


# ------------------------------------------------------------------ campaign results
def ev(conn, typ, email, camp="c1", name="UK | Procurement", step=1, sender="ola@thecodest.co", at=None, person=None):
    conn.execute("insert into lemlist_activities(id, type, email, campaign_id, campaign_name, person_id, happened_at, sequence_step, send_user_email, send_user_name) "
                 "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (f"{typ}-{email}-{camp}-{step}", typ, email, camp, name, person, at or NOW - timedelta(days=3), step, sender, sender.split("@")[0] if sender else None))


def test_campaigns_side_by_side_count_people_not_events_and_flag_bounces(conn):
    for i in range(40):
        ev(conn, "emailsSent", f"p{i}@a.example")
    ev(conn, "emailsSent", "p0@a.example", step=2)                       # a second email to the same lead
    ev(conn, "emailsReplied", "p1@a.example")
    ev(conn, "emailsReplied", "p1@a.example", step=2)                    # two replies, one person
    ev(conn, "emailsOpened", "p2@a.example")                              # opens are left out
    ev(conn, "emailsBounced", "p3@a.example")
    for i in range(10):
        ev(conn, "emailsSent", f"q{i}@b.example", camp="c2", name="Nordics | Procurement [DRAFT]", sender="jan@thecodest.co")
    for i in range(3):
        ev(conn, "emailsBounced", f"q{i}@b.example", camp="c2", name="Nordics | Procurement [DRAFT]", sender="jan@thecodest.co")
    r = cr.results(conn, now=NOW)
    c1, c2 = r["campaigns"]
    assert (c1["name"], c1["leads"], c1["sends"], c1["replied"], c1["bounced"]) == ("UK | Procurement", 40, 41, 1, 1)
    assert c1["bounce"] == "warn" and c1["reply_rate"] == 1 / 40                                       # 1 of 40 bounced: 2.5%
    assert c2["draft"] and c2["bounce"] == "bad" and c2["leads"] == 10                                  # 30%
    assert [s["step"] for s in r["steps"]] == [1, 2] and r["steps"][1]["sends"] == 1
    assert r["total"]["leads"] == 50 and "senders" not in r
    s = cr.results(conn, now=NOW, by_sender=True)["senders"]
    assert [(x["email"], x["leads"]) for x in s] == [("ola@thecodest.co", 40), ("jan@thecodest.co", 10)]


def test_deals_created_at_a_contacted_company_after_its_first_email_count(conn):
    acc = account(conn, "Acme")
    pid = str(conn.execute("insert into people(full_name, work_email) values ('Ann Smith','ann@acme.example') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, acc))
    conn.execute("insert into crm_organizations(crm_id, name, organization_id) values (7,'Acme',%s)", (acc,))
    first = NOW - timedelta(days=10)
    ev(conn, "emailsSent", "ann@acme.example", at=first, person=pid)
    conn.execute("insert into crm_deals(crm_id, title, crm_org_id, pipeline_id, stage_id, status, add_time) values (1,'Acme platform',7,2,7,'open',%s), "
                 "(2,'Acme old',7,2,7,'lost',%s)", (first + timedelta(days=2), first - timedelta(days=200)))
    c = cr.results(conn, now=NOW)["campaigns"][0]
    assert (c["companies"], c["deals"], c["won"]) == (1, 1, 0)                                          # the older deal came before the email


def test_api_permissions(api):
    c, h = api
    assert c.get("/api/clients", headers=h("bdm")).status_code == 403                                  # team-wide money: managers only
    assert c.get("/api/clients", headers=h("mgr")).status_code == 200
    r = c.get("/api/campaign-results", headers=h("bdm"))
    assert r.status_code == 200 and r.json()["can"]["senders"] is False and "senders" not in r.json()
    assert c.get("/api/campaign-results", headers=h("mgr")).json()["can"]["senders"] is True
    assert c.get("/api/campaign-results?days=2", headers=h("mgr")).status_code == 422
