"""What fits them best (company) and the sales brief (person): ranking from stored evidence, every line traced to a stored source, reasons to
stop or wait lowering the score, scope and permissions as on the company and person pages, and no email address ever in a response."""
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri import solutions as S
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, q1
from ri.signals.baseline import import_baseline
from ri.sources.registry import seed_registry
from test_r4_crm_notify import mk_user

SECRET = "f" * 40
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
KINDS = {"signal", "news", "job", "tender", "profile", "lead_list", "records"}


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def org(conn, name, *, segment="Payments", country="GB", eligibility="Eligible", owner=None):
    oid = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, employees_estimate, in_master_list, list_eligibility) "
                           "values (%s,%s,%s,%s,1800,true,%s) returning id", (name, name.lower().replace(" ", "") + ".example", country, segment, eligibility)).fetchone()["id"])
    if owner is not None:
        crm = 500 + conn.execute("select count(*) n from crm_organizations").fetchone()["n"]
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (%s,%s,%s,%s,'linked')", (crm, oid, name, owner))
    return oid


def person(conn, oid, name, title, *, email=None, detail=None):
    pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values (%s,%s,%s) returning id",
                           (name, email, "Verified" if email else "unknown")).fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,%s)", (pid, oid, title))
    if detail is not None:
        conn.execute("insert into person_sources(person_id, system, external_id, detail) values (%s,'sheets',%s,%s)", (pid, f"S-{pid[:6]}", json.dumps(detail)))
    return pid


_ROW = [0]


def list_row(conn, oid, kind, original):
    _ROW[0] += 1
    n = _ROW[0]
    link = conn.execute("insert into account_source_links(source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at, organization_id) "
                        "values ('sheets','F1','S1',%s,%s,%s,'h',%s,now(),%s) returning id", (kind, f"r{n}", n, original.get("Company", "x"), oid)).fetchone()["id"]
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,%s,%s,now())", (oid, link, kind, json.dumps(original)))


def signal(conn, oid, rule, summary, url, *, days=10, strength=2, tier="D2"):
    when = NOW - timedelta(days=days)
    doc = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, published_at, content_text) "
                       "values ('verify-original-page',%s,'1',%s,%s,%s,%s) returning id", (f"d-{url}", url, summary, when, summary)).fetchone()["id"]
    ev = conn.execute("insert into events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,extraction_model_version,"
                      "verification_status,independent_origin_group) values (%s,'programme_announced',%s,%s,%s,true,%s,'{}','[]','test','verified',%s) returning id",
                      (f"e-{url}", oid, when, when, summary, f"g-{url}")).fetchone()["id"]
    claim = conn.execute("insert into claims(subject,predicate,object_json,evidence_anchor,anchor_verified,claim_kind,status,source_document_id) "
                         "values ('x','announced',%s,%s,true,'quote','supported',%s) returning id", (json.dumps({"url": url}), summary, doc)).fetchone()["id"]
    conn.execute("insert into event_claims(event_id, claim_id) values (%s,%s)", (ev, claim))
    conn.execute("insert into signals(event_id, rule_id, rule_version, organization_id, strength, demand_tier, verification_status, effective_from, evidence_claim_ids, reason_code) "
                 "values (%s,%s,'1.0.0',%s,%s,%s,'verified',%s,%s,'test')", (ev, rule, oid, strength, tier, when, [claim]))
    return str(ev)


def negative(conn, oid, rule, ev):
    conn.execute("insert into negative_findings(rule_id, rule_version, scope_type, scope_id, kind, effect, event_id) values (%s,'1.0.0','account',%s,'soft_risk','downgrade',%s)",
                 (rule, oid, ev))


def tender(conn, oid, title, url, *, work_type, deadline_days=12, status="open"):
    conn.execute("insert into procurement_notices(source_id, notice_id, buyer_name, buyer_org_id, title, status, published_at, deadline_at, official_url, summary) "
                 "values ('procurement-ted',%s,'b',%s,%s,%s,%s,%s,%s,%s)", (url, oid, title, status, NOW - timedelta(days=5), NOW + timedelta(days=deadline_days), url,
                                                                 json.dumps({"title_en": title, "work_type": work_type, "fit": {"level": "strong", "reason": "x"}})))


def job(conn, oid, title, url, *, kind="permanent", days=6):
    conn.execute("insert into job_postings(provider, board, job_id, organization_id, title, employment_type, posted_at, url, tech_keywords) values ('greenhouse','b',%s,%s,%s,%s,%s,%s,%s)",
                 (url, oid, title, kind, NOW - timedelta(days=days), url, ["python"]))


def company_profile(conn, oid, content):
    conn.execute("insert into account_profiles(organization_id, requested_by, status, content, finished_at) values (%s,'t','done',%s,now())", (oid, json.dumps(content)))


def person_profile(conn, pid, oid, content):
    conn.execute("insert into person_profiles(person_id, organization_id, requested_by, status, content, finished_at) values (%s,%s,'t','done',%s,now())", (pid, oid, json.dumps(content)))


URLS = {"ai_sig": "https://news.example/kestrel-ai-roles", "ml_job": "https://jobs.example/kestrel/ml-engineer", "exa_news": "https://news.example/kestrel-assistant",
        "exa_help": "https://kestrel.example/ai", "list_sig": "https://press.example/kestrel-ai-launch", "tender": "https://ted.example/notice/1",
        "qa_tender": "https://ted.example/notice/2", "hub": "https://news.example/frozen-hub", "talk": "https://conf.example/ada-talk"}


@pytest.fixture()
def world(conn, db_url):
    import_baseline(conn); seed_registry(conn)
    mk_user(conn, "bdm@x", "bdm", 555); mk_user(conn, "admin@x", "admin"); mk_user(conn, "comp@x", "compliance"); mk_user(conn, "analyst@x", "research_analyst")
    o = {"kestrel": org(conn, "Kestrel Pay"), "fjord": org(conn, "Fjord Bank", segment="Banking", country="NO"), "checks": org(conn, "Checkwell Insurance", segment="Insurance"),
         "quiet": org(conn, "Quiet Bank", segment="Banking"), "frozen": org(conn, "Frozen Bank", segment="Banking"), "theirs": org(conn, "Owned Elsewhere", owner=999),
         "outside": org(conn, "Outside Retail", segment="Retail", eligibility="Out of scope")}
    # Kestrel: everything points at AI and data
    signal(conn, o["kestrel"], "CAP-05", "Kestrel Pay is hiring machine learning engineers for its fraud models", URLS["ai_sig"], days=8, strength=2, tier="D2")
    job(conn, o["kestrel"], "Senior Machine Learning Engineer", URLS["ml_job"])
    job(conn, o["kestrel"], "Data Engineer (Python)", URLS["ml_job"] + "-2")
    company_profile(conn, o["kestrel"], {
        "about": {"text": "Kestrel Pay runs payments.", "sources": []},
        "how_we_help": [{"angle": "AI engineers for the fraud models", "why": "They are hiring for it.", "service": "ai_data",
                         "sources": [{"source": "W1", "source_url": URLS["exa_help"], "published": day(20)}]}],
        "recent": [{"date": day(15), "headline": "Kestrel Pay launches an AI assistant for merchants", "detail": "..", "source_url": URLS["exa_news"], "quote": "q"}],
        "icp_points": [{"label": "Tech stack", "value": "Python, AWS", "fit": "good", "source_url": URLS["exa_help"]}],
        "vendors": [{"name": "Accenture", "role": "core systems", "source_url": URLS["exa_help"]}], "open_questions": ["Who owns the fraud roadmap?"]})
    list_row(conn, o["kestrel"], "master_account", {"Company": "Kestrel Pay", "The Codest Service Fit": "AI", "Procurement Route": "Unknown",
                                                    "Research Notes": "write to ops@kestrel.example"})
    for _ in range(2):                                  # the lead list logs one story twice: it counts once
        list_row(conn, o["kestrel"], "buying_signal_log", {"Company": "Kestrel Pay", "Verified event": "Kestrel Pay launched an AI fraud product for merchants.",
                                                           "Event date": day(12), "Signal type": "AI product launch", "Service Fit": "AI",
                                                           "Source URL": URLS["list_sig"], "Evidence confidence": "High"})
    list_row(conn, o["kestrel"], "buying_signal_log", {"Company": "Kestrel Pay", "Verified event": "An old story about a data warehouse.", "Event date": day(500),
                                                       "Signal type": "Data", "Service Fit": "AI", "Source URL": "https://old.example/x", "Evidence confidence": "High"})
    # Fjord: an open software tender and a "Development" lead list fit
    tender(conn, o["fjord"], "Rebuild of the mobile banking app", URLS["tender"], work_type="software_development")
    list_row(conn, o["fjord"], "master_account", {"Company": "Fjord Bank", "The Codest Service Fit": "Development"})
    # Checkwell: a testing tender
    tender(conn, o["checks"], "Test automation for the claims platform", URLS["qa_tender"], work_type="testing_qa")
    # Frozen: hiring signal but its own nearshore hub
    ev = signal(conn, o["frozen"], "CAP-01", "Frozen Bank opens 12 Java roles for its payments team", "https://news.example/frozen-roles", days=5)
    hub = signal(conn, o["frozen"], "COM-08", "Frozen Bank opens its own engineering hub in Kraków", URLS["hub"], days=4, strength=1)
    negative(conn, o["frozen"], "NEG-04", hub)
    signal(conn, o["theirs"], "PRG-10", "Owned Elsewhere moves its AI pilot to production", "https://news.example/theirs", days=3)
    list_row(conn, o["outside"], "master_account", {"Company": "Outside Retail", "The Codest Service Fit": "Development", "Eligibility": "Out of scope"})
    p = {"ada": person(conn, o["kestrel"], "Ada Clarke", "Chief Technology Officer", email="ada@kestrel.example",
                       detail={"seniority": "Executive", "persona": "C-Level Technology", "committee_role": "Economic buyer", "priority": "P1",
                               "signal": "Kestrel Pay told analysts it will double its AI team (mail ada@kestrel.example for details)."}),
         "pia": person(conn, o["kestrel"], "Pia Lund", "IT Procurement Manager", detail={"persona": "Procurement & Vendor Management"}),
         "quinn": person(conn, o["quiet"], "Quinn Hale", "Head of QA"),
         "tom": person(conn, o["theirs"], "Tom Ward", "CTO", email="tom@owned.example")}
    person_profile(conn, p["ada"], o["kestrel"], {
        "identity": {"confident": True, "note": ""},
        "current_role": {"title": "CTO", "remit": "Leads a team of 120 engineers across payments and data. Reach her at ada.private@gmail.com.", "since": str(TODAY.year - 2),
                         "source_url": "https://www.linkedin.com/in/ada-clarke"},
        "focus_areas": [{"label": "fraud detection", "source": "W1"}, {"label": "real-time payments", "source": "W1"}],
        "recent_activity": [{"date": day(30), "kind": "talk", "title": "Taking fraud AI to production", "detail": "..", "source_url": URLS["talk"], "quote": "q"}],
        "previous_roles": [{"title": "VP Engineering", "company": "Northpay", "from": "2018", "to": "2022"}], "talking_points": []})
    conn.commit()
    db = Database(db_url, max_size=3)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72), db=db))
    yield {"c": c, "h": hdr, "o": o, "p": p}
    db.close()


# ------------------------------------------------------------------ ranking
def test_best_fit_ranks_services_from_the_evidence(conn, world):
    o = world["o"]
    out = S.best_fit_many(conn, list(o.values()) + ["not-a-uuid"])
    assert out[o["kestrel"]]["service"] == "ai_data" and out[o["kestrel"]]["level"] == "strong" and out[o["kestrel"]]["label"] == "AI and data"
    assert out[o["fjord"]]["service"] == "delivery_team" and out[o["fjord"]]["why"].startswith("Open tender: Rebuild of the mobile banking app")
    assert out[o["checks"]]["service"] == "qa"
    assert o["quiet"] not in out                                  # nothing we hold supports a fit: no entry
    for v in out.values():
        assert set(v) >= {"service", "label", "score", "why"} and 0 <= v["score"] <= 100 and len(v["why"]) <= 140
    assert S.best_fit_many(conn, []) == {}


def test_company_fit_traces_every_line_to_a_stored_source(conn, world):
    fit = S.solution_fit(conn, world["o"]["kestrel"])
    assert fit["best"] == "ai_data" and fit["services"][0]["service"] == "ai_data"
    assert [s["score"] for s in fit["services"]] == sorted((s["score"] for s in fit["services"]), reverse=True)
    stored = set(URLS.values()) | {URLS["ml_job"] + "-2"}
    ai = fit["services"][0]
    kinds = {w["kind"] for w in ai["why"]}
    assert {"signal", "job", "profile", "lead_list", "news"} <= kinds <= KINDS
    for s in fit["services"]:
        for w in s["why"]:
            assert w["source_url"] is None or w["source_url"] in stored, w
            assert w["date"] is None or re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", w["date"])
    # the lead list's repeated story counts once, and a story older than a year does not count
    assert sum(1 for w in ai["why"] if w["source_url"] == URLS["list_sig"]) == 1
    assert not any("old story" in w["text"] for s in fit["services"] for w in s["why"])
    assert fit["why_now"] and "machine learning" in fit["why_now"].lower()
    assert any("Accenture" in w for w in fit["watch_outs"])       # a large integrator is already there
    assert any("How they buy" in u for u in fit["unknowns"])      # the lead list says "Unknown"
    assert EMAIL.search(json.dumps(fit)) is None                  # the lead list's note held an address: it never leaves


def test_reasons_to_wait_lower_the_score_and_are_said(conn, world):
    o = world["o"]
    fit = S.solution_fit(conn, o["frozen"])
    te = next(s for s in fit["services"] if s["service"] == "team_extension")
    items, off, watch = S.evidence(S._gather(conn, [o["frozen"]])[o["frozen"]], TODAY)
    raw = next(r for r in S.score(items, {}) if r["service"] == "team_extension")
    assert off["team_extension"] == 20 and te["score"] == max(0, raw["score"] - 20)
    assert any("nearshore" in w for w in fit["watch_outs"])
    out = S.solution_fit(conn, o["outside"])
    assert any("out of scope" in w.lower() for w in out["watch_outs"])
    assert any("financial services" in w for w in out["watch_outs"])


def test_no_evidence_means_no_fit_and_says_what_is_missing(conn, world):
    fit = S.solution_fit(conn, world["o"]["quiet"])
    assert fit["services"] == [] and fit["best"] is None and fit["why_now"] is None
    assert any("not researched them with Exa" in u for u in fit["unknowns"])


# ------------------------------------------------------------------ the person brief
def test_person_brief_is_grounded_and_role_aware(conn, world):
    b = S.person_brief(conn, world["p"]["ada"])
    assert b["lens"]["committee"] == "Decision maker" and b["lens"]["role"] == "Chief Technology Officer" and len(b["lens"]["cares_about"]) >= 3
    assert b["best_fit"]["service"] == "ai_data" and b["best_fit"]["label"] == "AI and data" and b["best_fit"]["why"]
    assert 2 <= len(b["openers"]) <= 3
    assert b["openers"][0]["source_url"] == URLS["talk"] and b["openers"][0]["date"] == day(30)    # her own talk leads
    for op in b["openers"]:
        assert op["text"] and op["because"]
        assert op["source_url"] is None or op["source_url"] in set(URLS.values()) | {URLS["ml_job"] + "-2"}
    assert 3 <= len(b["questions"]) <= 5
    facts = {f["label"]: f for f in b["facts"]}
    assert facts["Email"]["value"] == "Email ✓" and facts["Remit"]["source"] == "Exa research"
    assert facts["Team size hint"]["value"] == "team of 120" and "2 years" in facts["In this role since"]["value"]
    assert facts["Part in the buying decision"]["value"] == "Economic buyer" and facts["Part in the buying decision"]["source"] == "Lead list"
    assert isinstance(b["gaps"], list) and b["avoid"]
    text = json.dumps(b)
    assert EMAIL.search(text) is None and "ada@kestrel.example" not in text and "gmail" not in text


def test_procurement_person_gets_a_procurement_lens(conn, world):
    b = S.person_brief(conn, world["p"]["pia"])
    assert b["lens"]["committee"] == "Procurement"
    assert any("supplier" in q.lower() for q in b["questions"])
    assert {f["label"]: f["value"] for f in b["facts"]}["Email"] == "No email"
    assert any("Exa" in g for g in b["gaps"]) and any("email address" in g.lower() for g in b["gaps"])


def test_role_only_suggestion_is_labelled_as_such(conn, world):
    b = S.person_brief(conn, world["p"]["quinn"])           # Head of QA at a company with no evidence
    assert b["best_fit"]["service"] == "qa" and "suggestion" in b["best_fit"]["why"].lower()
    assert b["openers"] == []                                 # nothing dated to open with: nothing invented


def test_unsure_identity_keeps_exa_out_of_the_brief(conn, world):
    conn.execute("update person_profiles set content = jsonb_set(content, '{identity,confident}', 'false') where person_id=%s", (world["p"]["ada"],))
    conn.commit()
    b = S.person_brief(conn, world["p"]["ada"])
    assert not any(o["source_url"] == URLS["talk"] for o in b["openers"])
    assert "Remit" not in {f["label"] for f in b["facts"]}
    assert any("not sure" in g for g in b["gaps"])


# ------------------------------------------------------------------ the API: scope, permissions, audit, no addresses
def test_endpoints_follow_account_scope_and_permissions(conn, world):
    c, h, o, p = world["c"], world["h"], world["o"], world["p"]
    r = c.get(f"/api/accounts/{o['kestrel']}/solutions", headers=h("bdm"))
    assert r.status_code == 200 and r.json()["best"] == "ai_data"
    assert c.get(f"/api/accounts/{o['theirs']}/solutions", headers=h("bdm")).status_code == 404       # another rep's account
    assert c.get(f"/api/accounts/{o['theirs']}/solutions", headers=h("admin")).status_code == 200
    assert c.get(f"/api/accounts/{o['kestrel']}/solutions", headers=h("comp")).status_code == 403
    assert c.get(f"/api/accounts/not-a-uuid/solutions", headers=h("bdm")).status_code == 404
    assert c.get(f"/api/accounts/{o['kestrel']}/solutions").status_code == 401
    r = c.get(f"/api/people/{p['ada']}/brief", headers=h("bdm"))
    assert r.status_code == 200 and set(r.json()) >= {"lens", "best_fit", "openers", "questions", "facts", "gaps", "avoid"}
    assert EMAIL.search(r.text) is None
    assert c.get(f"/api/people/{p['tom']}/brief", headers=h("bdm")).status_code == 404               # works at another rep's account
    assert c.get(f"/api/people/{p['ada']}/brief", headers=h("analyst")).status_code == 403           # no contacts.read
    assert c.get(f"/api/people/00000000-0000-0000-0000-000000000000/brief", headers=h("bdm")).status_code == 404
    a = q1(conn, "select count(*) n from audit_events where operation='pii_read' and record_id=%s and detail->>'view'='brief'", (p["ada"],))
    assert a["n"] == 1


def test_best_fit_many_is_set_based(conn, world):
    """List pages ask for hundreds of accounts: the number of queries must not grow with them."""
    o = world["o"]
    ids = [org(conn, f"Bulk {i}") for i in range(60)]
    conn.commit()
    seen = []
    real = conn.execute

    def counting(sql, *a, **k):
        seen.append(sql)
        return real(sql, *a, **k)
    conn.execute = counting                                   # type: ignore[method-assign]
    try:
        S.best_fit_many(conn, [o["kestrel"]])
        small = len(seen)
        seen.clear()
        S.best_fit_many(conn, ids + list(o.values()))
        assert len(seen) == small
    finally:
        conn.execute = real                                   # type: ignore[method-assign]


def test_service_words_are_read_in_order():
    assert S.services_in("Development") == {"delivery_team": 1.0}
    assert S.services_in("AI") == {"ai_data": 1.0}
    f = S.services_in("API integration / data engineering / QA")
    assert f["delivery_team"] == 1.0 and f["ai_data"] == .75 and f["qa"] == .75
    assert S.services_in("") == {}
    assert isinstance(date.today(), date)
