"""Vendor intelligence: who a prospect buys technology and engineering from, and what is happening with those relationships.
Exa and the model are FAKES (no network). What matters: a supplier is shown only when a returned page names BOTH the company and the supplier and the
quote is really on that page; old evidence never makes a supplier "current"; our own records work with no search at all; the angles are derived by code from
verified items; the pages behind a supplier go to the normal page check; the Exa budget stops the run cleanly."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ri import budget, usage
from ri.db import q1, qa
from ri.events.store import store_event
from ri.research import profile_jobs
from ri.research import vendor_intel as vi
from ri.research.exa_search import ExaSearcher
from ri.sources.base import BudgetExceeded
from test_r28_profiles import caps, env, fake_post, org, settings                # noqa: F401  (env is a shared fixture)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
REC = {"name": "Kestrel Pay Ltd", "website": "kestrel.example", "country": "GB"}

PAGES = {
    "W1": {"url": "https://mambu.example/customers/kestrel-pay", "title": "Kestrel Pay builds instant payouts on Mambu", "published": None, "purpose": "case_study",
           "text": "Kestrel Pay, a UK payments company, runs its core ledger on Mambu. Delivery was led by Endava, whose engineers built the payouts integrations for Kestrel Pay."},
    "W2": {"url": "https://news.example/kestrel-sepa", "title": "Kestrel Pay launches instant payouts", "published": "2026-09-14", "purpose": "vendor_news",
           "text": "Kestrel Pay has launched instant payouts across the eurozone and chose Mambu as its core platform partner for the programme."},
    "W3": {"url": "https://endava.example/work/kestrel-pay", "title": "Endava and Kestrel Pay", "published": "2024-02-01", "purpose": "integrator_story",
           "text": "Endava has provided a dedicated engineering team to Kestrel Pay since 2024, under a three-year agreement signed in February 2024."},
    "W4": {"url": "https://news.example/kestrel-delay", "title": "Kestrel Pay payouts programme slips", "published": "2026-10-20", "purpose": "change",
           "text": "Kestrel Pay's instant payouts programme has been delayed by a quarter after the migration to Mambu ran over, the company said."},
    "W5": {"url": "https://kestrel.example/engineering", "title": "Our engineering partners", "published": None, "purpose": "own_pages",
           "text": "Our engineering is supported by Endava and we run on Amazon Web Services."},
    "W6": {"url": "https://mambu.example/customers/harbour-bank", "title": "Harbour Bank on Mambu", "published": "2026-06-01", "purpose": "case_study",
           "text": "Harbour Bank moved its deposits to Mambu and cut onboarding time in half."},
    "W7": {"url": "https://news.example/mambu-acquired", "title": "Mambu to be acquired", "published": "2026-10-30", "purpose": "health:Mambu",
           "text": "Mambu has agreed to be acquired by a private equity group, the companies said on 30 October."},
}


def ev(src, kind, by, quote, date=""):
    return {"source": src, "kind": kind, "by": by, "date": date, "quote": quote}


GOOD = {"vendors": [
    {"name": "Mambu", "category": "core_platform", "relationship": "recent_selection", "role": "Core ledger for instant payouts", "term": "",
     "evidence": [ev("W2", "news_article", "press", "chose Mambu as its core platform partner", "2026-09-14"),
                  ev("W1", "case_study", "vendor", "runs its core ledger on Mambu"),
                  ev("W6", "case_study", "vendor", "Harbour Bank moved its deposits to Mambu")]},        # another customer: dropped
    {"name": "Endava", "category": "engineering_partner", "relationship": "current", "role": "Dedicated engineering team", "term": "a three-year agreement signed in February 2024",
     "evidence": [ev("W3", "integrator_story", "integrator", "Endava has provided a dedicated engineering team to Kestrel Pay since 2024", "2024-02"),
                  ev("W5", "company_page", "company", "Our engineering is supported by Endava")]},
    {"name": "Globex Cloud", "category": "cloud", "relationship": "current", "role": "Invented", "term": "",
     "evidence": [ev("W5", "company_page", "company", "we run on Globex Cloud")]},                          # not on the page: dropped
    {"name": "Amazon Web Services", "category": "cloud", "relationship": "current", "role": "Hosting", "term": "a five-year enterprise discount programme",
     "evidence": [ev("W5", "company_page", "company", "we run on Amazon Web Services")]}],                  # term not on the page: removed, supplier kept
    "happenings": [
        {"vendor": "Mambu", "kind": "delay", "date": "2026-10-20", "headline": "Instant payouts programme delayed by a quarter", "detail": "The Mambu migration ran over.", "source": "W4",
         "quote": "has been delayed by a quarter after the migration to Mambu ran over"},
        {"vendor": "Mambu", "kind": "vendor_acquired", "date": "2026-10-30", "headline": "Mambu to be acquired by a private equity group", "detail": "", "source": "W7",
         "quote": "Mambu has agreed to be acquired by a private equity group"},
        {"vendor": "Endava", "kind": "dispute", "date": "2026-10-01", "headline": "Dispute with Endava", "detail": "", "source": "W4", "quote": "a dispute over invoices that the page never mentions"}],
    "open_questions": ["Who runs delivery of the payouts programme?"]}


# ------------------------------------------------------------------ names
def test_names_are_matched_whole_word_ignoring_case_accents_and_legal_suffixes():
    assert vi.canon("Thought Machine Ltd")[:2] == ("Thought Machine", "core_platform")
    assert vi.canon("Zuhlke Engineering AG")[1] is None and vi.canon("Zuhlke")[:2] == ("Zühlke", "system_integrator")
    assert vi.canon("SS&C Technologies Inc")[0] == "SS&C" and vi.canon("Some New Vendor GmbH")[:2] == ("Some New Vendor GmbH", None)
    assert vi.name_key("Nexi S.p.A.") == vi.name_key("NEXI") and vi.name_key("Nets A/S") == "nets"
    assert vi.on_page(["N-iX"], "We chose N iX for the build") and vi.on_page(["Zühlke"], "built with ZUHLKE engineers")
    assert not vi.on_page(["FIS"], "the efficiency of it") and not vi.on_page(["Sii"], "the siiblings and the asii family") and not vi.on_page(["Q2"], "in Q22")


def test_the_lexicon_is_consistent():
    lex = vi.lexicon()
    cats = set(lex["categories"])
    assert cats == set(vi.CATEGORIES)
    keys: dict[str, str] = {}
    for e in lex["index"].values():
        assert e["category"] in cats
    # one spelling, one entry: no alias is claimed by two different suppliers
    import yaml
    raw = yaml.safe_load(vi.LEXICON_PATH.read_text(encoding="utf-8"))["vendors"]
    for v in raw:
        for n in [v["name"], *(v.get("aliases") or [])]:
            k = vi.name_key(n)
            assert keys.setdefault(k, v["name"]) == v["name"], f"{n} is claimed by {keys[k]} and {v['name']}"


# ------------------------------------------------------------------ the search plan
def test_the_plan_searches_by_who_is_writing_and_the_second_round_watches_the_right_suppliers():
    p = dict(vi.plan({**REC, "name": "Kestrel Pay"}, NOW))
    assert list(p) == ["case_study", "vendor_news", "integrator_story", "change", "own_pages"]
    assert "startPublishedDate" not in p["case_study"] and "startPublishedDate" in p["vendor_news"] and p["own_pages"]["includeDomains"] == ["kestrel.example"]
    assert p["vendor_news"]["category"] == "news" and p["change"]["category"] == "news" and "category" not in p["case_study"]
    assert [k for k, _ in vi.plan({"name": "Kestrel Pay"}, NOW)] == ["case_study", "vendor_news", "integrator_story", "change"]       # no website: no own-pages search
    vendors = [{"name": "Hays", "category": "staffing_msp", "relationship": "current", "strength": "reported"},
               {"name": "Amazon Web Services", "category": "cloud", "relationship": "current", "strength": "official"},
               {"name": "Mambu", "category": "core_platform", "relationship": "current", "strength": "reported"},
               {"name": "Endava", "category": "engineering_partner", "relationship": "unclear", "strength": "vendor_claim"},
               {"name": "Accenture", "category": "system_integrator", "relationship": "past", "strength": "official"},
               {"name": "Capgemini", "category": "system_integrator", "relationship": "current", "strength": "reported"}]
    watch = vi.pick_watch(vendors)
    assert [w["name"] for w in watch] == ["Endava", "Capgemini", "Mambu"]            # engineering partner, integrator, platform; never an agency, a past supplier or a hyperscaler first
    f = dict(vi.followup_plan({"name": "Kestrel Pay"}, watch, NOW))
    assert set(f) == {"watch:Endava", "health:Endava", "watch:Capgemini", "health:Capgemini", "watch:Mambu", "health:Mambu"}
    assert f["watch:Mambu"]["query"] == "Mambu Kestrel Pay"
    aws = dict(vi.followup_plan({"name": "Kestrel Pay"}, [{"name": "Amazon Web Services", "category": "cloud"}], NOW))
    assert set(aws) == {"watch:Amazon Web Services"}                                   # no "is the hyperscaler in trouble" search


# ------------------------------------------------------------------ verification: only what a page supports survives
def test_only_statements_tied_to_a_returned_page_that_names_both_company_and_supplier_survive():
    vendors, happenings, questions, dropped = vi.verify_answer(GOOD, PAGES, REC)
    by = {v["name"]: v for v in vendors}
    assert set(by) == {"Mambu", "Endava", "Amazon Web Services"}
    assert [e["source"] for e in by["Mambu"]["evidence"]] == ["W2", "W1"] and by["Mambu"]["category"] == "core_platform"       # W6 is about Harbour Bank
    assert by["Endava"]["term"] == "a three-year agreement signed in February 2024" and by["Amazon Web Services"]["term"] is None
    assert by["Mambu"]["evidence"][0]["date"] == "2026-09-14" and by["Endava"]["evidence"][0]["date"] == "2024-02"
    assert by["Endava"]["evidence"][1]["source_url"] == "https://kestrel.example/engineering"             # the company's own site counts as about the company with no name on it
    assert [h["kind"] for h in happenings] == ["delay", "vendor_acquired"]                                # the quote of the third is not on its page
    assert happenings[1]["source_url"] == "https://news.example/mambu-acquired"                           # news about the supplier itself needs no mention of the company
    assert questions == ["Who runs delivery of the payouts programme?"]
    assert dropped == 3                                                                                  # W6, the Globex Cloud evidence, and the unquoted dispute; a removed term is not a dropped supplier


def test_a_page_about_the_supplier_alone_cannot_prove_a_relationship_or_a_delay():
    raw = {"vendors": [{"name": "Mambu", "category": "core_platform", "relationship": "current", "role": "Ledger", "term": "",
                        "evidence": [ev("W7", "news_article", "press", "Mambu has agreed to be acquired by a private equity group")]}],
           "happenings": [{"vendor": "Mambu", "kind": "delay", "date": "2026-10-30", "headline": "Mambu delays", "detail": "", "source": "W7", "quote": "Mambu has agreed to be acquired by a private equity group"}],
           "open_questions": []}
    vendors, happenings, _, dropped = vi.verify_answer(raw, PAGES, REC)
    assert vendors == [] and happenings == [] and dropped == 2
    # an invented page id, a missing quote and a quote that is not there are all dropped; a vendor-side kind needs the supplier named on the page
    raw2 = {"vendors": [{"name": "Endava", "category": "engineering_partner", "relationship": "current", "role": "", "term": "",
                         "evidence": [ev("W99", "case_study", "vendor", "Endava team"), ev("W3", "case_study", "vendor", ""), ev("W3", "case_study", "vendor", "Endava runs the whole bank")]}],
            "happenings": [{"vendor": "Endava", "kind": "vendor_cuts", "date": "2026-10-30", "headline": "Endava cuts jobs", "detail": "", "source": "W7", "quote": "agreed to be acquired by a private equity group"}],
            "open_questions": []}
    vendors, happenings, _, dropped = vi.verify_answer(raw2, PAGES, REC)
    assert vendors == [] and happenings == [] and dropped == 4


# ------------------------------------------------------------------ merging and the "current" rule
def cand(name, rel, date, *, by="press", kind="news_article", category=None, **kw):
    return {"name": name, "category": category, "relationship": rel, "role": None, "term": None,
            "evidence": [{"source": "W1", "kind": kind, "by": by, "date": date, "quote": "q", "source_url": f"https://{by}.example/{name.replace(' ', '')}{date}", "source_title": "t"}], **kw}


def test_a_supplier_is_never_current_on_old_or_undated_evidence_and_the_newest_evidence_decides():
    out = {v["name"]: v for v in vi.merge_vendors([
        cand("Old Vendor", "current", "2022-03", category="core_platform"),
        cand("Undated Vendor", "current", None, by="vendor", kind="case_study"),
        cand("Own Page Vendor", "current", None, by="company", kind="company_page"),
        cand("Fresh Vendor", "recent_selection", "2026-08"),
        cand("Older Fresh", "recent_selection", "2025-06"),
        cand("Thought Machine Ltd", "current", "2025-01", category="other"), cand("thought machine", "ending", "2026-10-01"),
        cand("Ended Vendor", "past", "2026-05")], NOW)}
    assert out["Old Vendor"]["relationship"] == "unclear" and "2022-03" in out["Old Vendor"]["relationship_note"]
    assert out["Undated Vendor"]["relationship"] == "unclear" and "undated" in out["Undated Vendor"]["relationship_note"]
    assert out["Own Page Vendor"]["relationship"] == "current" and out["Own Page Vendor"]["strength"] == "official"
    assert out["Fresh Vendor"]["relationship"] == "recent_selection" and out["Older Fresh"]["relationship"] == "current"      # selected 17 months ago is no longer "recent"
    assert out["Thought Machine"]["relationship"] == "ending" and len(out["Thought Machine"]["evidence"]) == 2 and out["Thought Machine"]["category"] == "core_platform"   # one entry, the lexicon's category
    assert out["Ended Vendor"]["relationship"] == "past"
    assert list(out)[0] == "Thought Machine"                                         # live relationships first: ending, then recent selections
    # strength says who wrote the evidence: the company or a registry is official, press is reported, only the vendor's own pages is a claim
    assert out["Undated Vendor"]["strength"] == "vendor_claim" and out["Fresh Vendor"]["strength"] == "reported"
    two = vi.merge_vendors([cand("Same", "current", "2026-01", by="vendor", kind="case_study"), cand("Same", "current", "2026-02", by="integrator", kind="integrator_story")], NOW)
    assert two[0]["strength"] == "reported"                                          # two independent sites, still not the company's own word


# ------------------------------------------------------------------ angles: derived by code from verified items
def test_angles_come_from_verified_items_and_say_which_tracker_rule_they_relate_to():
    vendors, happenings, q, _ = vi.verify_answer(GOOD, PAGES, REC)
    c = vi.assemble(REC, {"suppliers": [], "awards": [], "events": [], "stack": [{"keyword": "AWS", "ads": 3}, {"keyword": "Kubernetes", "ads": 2}], "any": False}, vendors, happenings, q, NOW, had_pages=True)
    by = {}
    for a in c["angles"]:
        by.setdefault(a["code"], a)
    assert set(by) == {"incumbent_engineering_partner", "vendor_implementation", "delivery_trouble", "forced_change"}
    assert by["vendor_implementation"]["level"] == "strong" and by["vendor_implementation"]["maps_to"] == "SRC-05" and by["vendor_implementation"]["vendors"] == ["Mambu"]
    assert by["delivery_trouble"]["level"] == "strong" and by["delivery_trouble"]["maps_to"] == "PRG-16" and "do not quote it" in by["delivery_trouble"]["text"]
    assert by["forced_change"]["vendors"] == ["Mambu"] and by["incumbent_engineering_partner"]["vendors"] == ["Endava"]
    assert [a["level"] for a in c["angles"]] == sorted((a["level"] for a in c["angles"]), key=["strong", "possible", "caution"].index)
    assert {s["keyword"]: s["vendor"] for s in c["stack"]} == {"AWS": "Amazon Web Services", "Kubernetes": None}
    mam = next(v for v in c["vendors"] if v["name"] == "Mambu")
    assert {h["kind"] for h in mam["happenings"]} == {"delay", "vendor_acquired"}


def test_suppliers_and_happenings_that_share_a_reason_make_one_card_not_a_repeat():
    mk = lambda n, cat: {"name": n, "category": cat, "relationship": "recent_selection", "last_evidence": "2026-09-01", "happenings": [], "contract_end": None}    # noqa: E731
    delays = [{"vendor": "Mambu", "kind": "delay", "date": "2026-10-20", "headline": "Payouts delayed by a quarter", "source_url": "u"},
              {"vendor": None, "kind": "delay", "date": "2026-11-07", "headline": "Kestrel Pay's programme is delayed", "source_url": "u"},
              {"vendor": None, "kind": "dispute", "date": "2025-01-01", "headline": "Old dispute", "source_url": "u"}]
    out = vi.derive_angles([mk("Mambu", "core_platform"), mk("Backbase", "digital_channels")], delays, NOW, had_pages=False)
    assert [a["code"] for a in out] == ["vendor_implementation", "delivery_trouble"]
    impl, trouble = out
    assert impl["vendors"] == ["Mambu", "Backbase"] and "Mambu and Backbase" in impl["text"]
    assert trouble["text"].startswith("Kestrel Pay's programme is delayed.") and trouble["vendors"] == ["Mambu"] and len(trouble["basis"]) == 2     # the newest headline is shown; both are kept as basis


def test_old_or_undated_happenings_make_no_angle_and_an_empty_picture_says_so():
    old = {"vendor": None, "kind": "delay", "date": "2025-01-10", "headline": "Old delay", "source_url": "u"}
    undated = {"vendor": None, "kind": "dispute", "date": None, "headline": "Undated dispute", "source_url": "u"}
    assert vi.derive_angles([], [old, undated], NOW, had_pages=False) == []
    only = vi.derive_angles([], [], NOW, had_pages=True)
    assert [a["code"] for a in only] == ["no_partner_seen"] and only[0]["vendors"] == []
    caution = vi.derive_angles([], [{"vendor": None, "kind": "panel_closed", "date": "2026-09-01", "headline": "Supplier list cut to 12 firms", "source_url": "u"},
                                    {"vendor": None, "kind": "replaced", "date": "2026-10-01", "headline": "Contract ended", "source_url": "u"}], NOW, had_pages=False)
    assert [(a["code"], a["level"], a["maps_to"]) for a in caution] == [("supplier_change", "strong", "SRC-03"), ("supplier_list_cut", "caution", "NEG-05")]
    assert caution[0]["text"].startswith("Contract ended. A change of supplier")


def test_a_contract_award_makes_a_renewal_angle_and_says_whether_the_end_is_stated_or_assumed():
    mk = lambda end, basis: {"name": "Accenture", "category": "system_integrator", "category_label": "x", "relationship": "current", "relationship_note": None, "role": None,    # noqa: E731
                             "term": None, "strength": "official", "last_evidence": "2023-12-01", "evidence": [], "contract_end": end, "contract_end_basis": basis, "happenings": []}
    stated = vi.derive_angles([mk("2027-03-31", "stated")], [], NOW, had_pages=False)
    assumed = vi.derive_angles([mk("2027-09-30", "assumed")], [], NOW, had_pages=False)
    far = vi.derive_angles([mk("2029-01-01", "stated")], [], NOW, had_pages=False)
    renew = lambda a: [x for x in a if x["code"] == "contract_window"]       # noqa: E731
    assert renew(stated)[0]["level"] == "strong" and "stated contract length" in renew(stated)[0]["text"] and renew(stated)[0]["maps_to"] == "SRC-03"
    assert renew(assumed)[0]["level"] == "possible" and "assuming a 36-month term" in renew(assumed)[0]["text"]
    assert renew(far) == []
    assert [a["code"] for a in stated if a["code"] != "contract_window"] == ["partner_route"]      # an integrator on record is also the way in


# ------------------------------------------------------------------ our own records (no search, no model)
def seed_records(conn, oid):
    """A supplier named in a verified finding, an IT contract award, a verified vendor-selected event naming a supplier, a delay, and open job ads."""
    conn.execute("insert into supplier_edges(supplier_name, buyer_org_id, supplier_role, scope_text, relation, status, valid_from) values ('Capgemini', %s, 'si', 'Programme delivery', 'contracted', 'announced', %s)",
                 (oid, NOW - timedelta(days=500)))
    conn.execute("insert into awarded_contracts(notice_id, buyer_name, buyer_org_id, title, winners, awarded_on, duration_months, expected_end, end_basis, relevant, official_url) "
                 "values ('N1','Kestrel Pay Ltd',%s,'Application support and development',%s,'2024-04-10',36,'2027-04-10','stated',true,'https://ted.example/N1')", (oid, ["Accenture"]))
    conn.execute("insert into awarded_contracts(notice_id, buyer_name, buyer_org_id, title, winners, awarded_on, expected_end, end_basis, relevant) "
                 "values ('N2','Kestrel Pay Ltd',%s,'Cleaning services',%s,'2024-04-10','2027-04-10','assumed',false)", (oid, ["Clean Co"]))      # not IT: ignored
    for etype, name, summary, days in (("vendor_selected", "Backbase", "Kestrel Pay selected Backbase for its digital channels", 60), ("programme_delayed", None, "Kestrel Pay's payouts programme is delayed", 20)):
        eid, _ = store_event(conn, event_type=etype, subject_name="Kestrel Pay Ltd", subject_org_id=oid, summary=summary, attrs={}, key_parts=(summary,), event_at=NOW - timedelta(days=days),
                             pub_at=NOW - timedelta(days=days), claim_ids=[], document_ids=[], other_entities=[{"name": name, "role": "vendor"}] if name else [])
        conn.execute("update events set verification_status='verified' where id=%s", (eid,))
    for i, kw in enumerate((["Java", "AWS"], ["Java", "Kubernetes"], ["AWS"])):
        conn.execute("insert into job_postings(provider, board, job_id, organization_id, employer_name, title, tech_keywords, open_status) values ('lever','kestrel',%s,%s,'Kestrel Pay','Engineer',%s,'open')", (f"j{i}", oid, kw))
    conn.commit()


def test_our_own_records_give_a_supplier_picture_with_no_search_at_all(conn, env):
    oid = env["o"]["open"]
    assert vi.from_records(conn, oid, NOW)["vendors"] == []
    seed_records(conn, oid)
    c = vi.from_records(conn, oid, NOW)
    by = {v["name"]: v for v in c["vendors"]}
    assert set(by) == {"Capgemini", "Accenture", "Backbase"} and c["web"] is False
    assert by["Capgemini"]["category"] == "system_integrator" and by["Capgemini"]["relationship"] == "current" and by["Capgemini"]["evidence"][0]["by"] == "record"
    assert by["Accenture"]["relationship"] == "current" and by["Accenture"]["strength"] == "official" and by["Accenture"]["contract_end"] == "2027-04-10"
    assert by["Accenture"]["evidence"][0]["source_url"] == "https://ted.example/N1" and "stated contract length" in by["Accenture"]["term"]
    assert by["Backbase"]["relationship"] == "recent_selection" and by["Backbase"]["category"] == "digital_channels"
    assert [h["kind"] for h in c["happenings"]] == ["delay", "selected"] and c["happenings"][0]["vendor"] is None
    assert {a["code"] for a in c["angles"]} >= {"partner_route", "contract_window", "vendor_implementation", "delivery_trouble"}
    assert [(s["keyword"], s["ads"]) for s in c["stack"]][:2] == [("AWS", 2), ("Java", 2)] and next(s for s in c["stack"] if s["keyword"] == "AWS")["vendor"] == "Amazon Web Services"
    json.dumps(c)                                                                                       # everything stored is plain JSON


# ------------------------------------------------------------------ the research run
def fake_pages():
    """Exa answers keyed by a phrase in the query. Pages are numbered in the order they are found: the five searches give W1 to W5, the second round
    gives W6 (about Mambu and another customer) and W7 (news about Mambu alone). The second-round keys come first, so they win over the broad ones."""
    page = lambda k, **kw: {**{"url": PAGES[k]["url"], "title": PAGES[k]["title"], "text": PAGES[k]["text"]}, **({"publishedDate": PAGES[k]["published"] + "T08:00:00.000Z"} if PAGES[k]["published"] else {}), **kw}    # noqa: E731
    return {"Mambu news: acquired": [page("W7")], "Endava news: acquired": [], "Mambu Kestrel Pay": [page("W6")], "Endava Kestrel Pay": [],
            "customer case study": [page("W1")], "selects, chooses or partners": [page("W2")], "client story": [page("W3")],
            "replaces a supplier": [page("W4")], "works with": [page("W5")]}


def two_phase_model(first, second, seen=None):
    """One reply for the first reading, another for the second (the pages of the second round are added)."""
    def factory():
        n = {"i": 0}

        async def call(system, user):
            n["i"] += 1
            if seen is not None:
                seen.append(user)
            reply = first if n["i"] == 1 else second
            if isinstance(reply, Exception):
                raise reply
            return json.dumps(reply), 1500, 400, "fake-model"
        return call
    return factory


def new_row(conn, oid, who="bdm@x"):
    rid = str(conn.execute("insert into account_vendor_intel(organization_id, requested_by) values (%s,%s) returning id", (oid, who)).fetchone()["id"])
    conn.commit()
    return rid


FIRST = {"vendors": [{**GOOD["vendors"][0], "evidence": GOOD["vendors"][0]["evidence"][:2]}, *GOOD["vendors"][1:]],          # the first reading has seen W1 to W5 only:
         "happenings": GOOD["happenings"][:1], "open_questions": GOOD["open_questions"]}                                   # not the other customer's page, nor the news about Mambu itself


def test_research_reads_five_searches_then_watches_the_top_suppliers_and_reads_again(conn, env):
    caps(conn)
    oid = env["o"]["open"]
    rid = new_row(conn, oid)
    calls, prompts = [], []
    out = profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(fake_pages(), calls), call_factory=two_phase_model(FIRST, GOOD, prompts))
    row = q1(conn, "select * from account_vendor_intel where id=%s", (rid,))
    assert out["status"] == "done" and row["status"] == "done"
    # five searches, then the second round: Endava (engineering partner first), Mambu (platform) and Amazon Web Services (hosting; no "is it in trouble" search)
    assert [c["query"].split(" ")[0] for c in calls[:5]] == ["Kestrel", "Kestrel", "consultancy", "Kestrel", "technology"] and len(calls) == 10 and row["searches"] == 10
    assert [c["query"] for c in calls[5:]] == ["Endava Kestrel Pay Ltd", "Endava news: acquired, layoffs, product end of life, outage, customers leaving", "Mambu Kestrel Pay Ltd",
                                                 "Mambu news: acquired, layoffs, product end of life, outage, customers leaving", "Amazon Web Services Kestrel Pay Ltd"]
    assert calls[0].get("startPublishedDate") is None and calls[1]["category"] == "news" and all(c["contents"]["text"]["maxCharacters"] <= 1800 for c in calls)
    assert len(prompts) == 2 and 'found_by="health:Mambu"' not in prompts[0] and 'found_by="health:Mambu"' in prompts[1]      # the second reading has the extra page
    assert "Suppliers we already know" in prompts[0]
    content = row["content"]
    assert content["partial"] is False and content["watched"] == ["Endava", "Mambu", "Amazon Web Services"] and {v["name"] for v in content["vendors"]} == {"Mambu", "Endava", "Amazon Web Services"}
    assert {h["kind"] for h in content["happenings"]} == {"delay", "vendor_acquired"}
    assert [s["id"] for s in row["sources"]] == ["W1", "W2", "W3", "W4", "W5", "W6", "W7"] and "text" not in row["sources"][0]      # page text is not stored
    assert row["tokens_in"] == 3000 and row["tokens_out"] == 800 and row["dropped"] == 3
    used = qa(conn, "select operation, cost_basis from provider_usage where provider='exa'")
    assert len(used) == 10 and all(u["operation"] == "vendor_intel" for u in used)
    assert float(q1(conn, "select reserved from provider_budgets where provider='exa'")["reserved"]) == 0


def test_a_failed_second_reading_keeps_the_first_answer_and_says_so(conn, env):
    caps(conn)
    rid = new_row(conn, env["o"]["open"])
    profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(fake_pages(), []), call_factory=two_phase_model(FIRST, ValueError("model JSON still invalid")))
    row = q1(conn, "select status, content, tokens_in from account_vendor_intel where id=%s", (rid,))
    assert row["status"] == "done" and row["content"]["partial"] is True and "could not be finished" in row["content"]["notes"][0] and row["tokens_in"] == 1500
    assert {h["kind"] for h in row["content"]["happenings"]} == {"delay"}                 # the news about Mambu itself was only in the second round


def test_budget_running_out_part_way_marks_the_answer_partial_and_skips_the_second_round(conn, env):
    caps(conn, exa=0.04)          # room for two searches
    rid = new_row(conn, env["o"]["open"])
    calls = []
    out = profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(fake_pages(), calls), call_factory=two_phase_model(FIRST, GOOD))
    row = q1(conn, "select status, content from account_vendor_intel where id=%s", (rid,))
    assert len(calls) == 2 and out["status"] == "done" and row["content"]["partial"] is True and "budget" in row["content"]["notes"][0] and row["content"]["watched"] == []


def test_with_no_budget_and_no_records_the_run_fails_plainly_but_our_records_still_answer(conn, env):
    oid = env["o"]["open"]
    caps(conn)
    conn.execute("update provider_budgets set spent=cap where provider='exa'")
    rid = new_row(conn, oid)
    calls = []
    out = profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(fake_pages(), calls), call_factory=two_phase_model(FIRST, GOOD))
    row = q1(conn, "select status, error from account_vendor_intel where id=%s", (rid,))
    assert out["reason"] == "budget" and calls == [] and row["status"] == "failed" and "Exa budget" in row["error"]
    seed_records(conn, oid)                                                                           # now there are records: the run answers from them, marked partial
    rid2 = new_row(conn, oid)
    out2 = profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid2}}, post_factory=fake_post(fake_pages(), calls), call_factory=two_phase_model(FIRST, GOOD))
    row2 = q1(conn, "select status, content, model from account_vendor_intel where id=%s", (rid2,))
    assert out2["status"] == "done" and calls == [] and row2["model"] is None and row2["content"]["partial"] is True
    assert {v["name"] for v in row2["content"]["vendors"]} == {"Capgemini", "Accenture", "Backbase"} and "only what our own records hold" in " ".join(row2["content"]["notes"])


def test_pages_with_nothing_for_this_company_say_so_instead_of_inventing_suppliers(conn, env):
    caps(conn)
    rid = new_row(conn, env["o"]["open"])
    profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post({}, []), call_factory=two_phase_model(FIRST, GOOD))
    row = q1(conn, "select status, content, model from account_vendor_intel where id=%s", (rid,))
    assert row["status"] == "done" and row["model"] is None and row["content"]["vendors"] == [] and "ask about current suppliers" in row["content"]["empty"].lower()


# ------------------------------------------------------------------ pages go on to the normal page check
def test_the_pages_behind_suppliers_and_happenings_are_queued_as_page_checks_and_nothing_else(conn, env):
    caps(conn)
    oid = env["o"]["open"]
    conn.execute("insert into verification_requests(url, origin, requested_by) values ('https://news.example/kestrel-delay','manual','mgr@x')")      # already queued by someone: skipped
    rid = new_row(conn, oid, "bdm@x")
    profile_jobs.vendor_intel(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(fake_pages(), []), call_factory=two_phase_model(FIRST, GOOD))
    row = q1(conn, "select pages_queued, content from account_vendor_intel where id=%s", (rid,))
    queued = qa(conn, "select url, origin, plan_id, organization_id::text as org, requested_by, status, job_id from verification_requests where plan_id='vendor-intel' order by url")
    urls = [q["url"] for q in queued]
    # W7 is news about the supplier alone (never queued); W4 was queued by someone else; W6 was dropped; W3 is a 2024 case study (older than two years: not worth a
    # page check); the rest tie a live supplier to the company
    assert row["pages_queued"] == len(queued) and "https://news.example/mambu-acquired" not in urls and "https://news.example/kestrel-delay" not in urls
    assert "https://mambu.example/customers/harbour-bank" not in urls and set(urls) == {"https://news.example/kestrel-sepa", "https://mambu.example/customers/kestrel-pay", "https://kestrel.example/engineering"}
    assert all(q["origin"] == "exa_discovery" and q["org"] == oid and q["requested_by"] == "bdm@x" and q["status"] == "queued" and q["job_id"] for q in queued)
    assert qa(conn, "select kind from jobs where kind='verify_url'") and q1(conn, "select count(*) n from jobs where kind='verify_url'")["n"] == len(queued)
    # records are never queued again, and old evidence is not worth a page check
    assert vi.page_check_urls({"happenings": [{"source_url": "https://a.example/x", "headline": "h", "found_by": "records"}], "vendors": [
        {"name": "V", "relationship": "current", "evidence": [{"source_url": "https://a.example/old", "date": "2021-01", "by": "press", "found_by": "case_study"},
                                                                  {"source_url": "https://www.linkedin.com/company/v", "date": "2026-06", "by": "press", "found_by": "case_study"}]}]}, NOW) == []


# ------------------------------------------------------------------ the API
def test_the_vendors_api_shows_our_records_at_once_starts_research_and_keeps_scope_and_permissions(conn, env):
    c, cr, h, oid = env["c"], env["cr"], env["h"], env["o"]["open"]
    seed_records(conn, oid)
    r = c.get(f"/api/accounts/{oid}/vendors", headers=h("bdm"))
    assert r.status_code == 200
    j = r.json()
    assert j["profile"] is None and j["running"] is False and j["can_run"] is True and j["setup"]["ready"] is False
    assert {v["name"] for v in j["records"]["vendors"]} == {"Capgemini", "Accenture", "Backbase"} and j["records"]["web"] is False
    # not set up: the button is refused with the reason, and nothing is queued
    assert c.post(f"/api/accounts/{oid}/vendors", headers=h("bdm")).status_code == 409
    assert c.post(f"/api/accounts/{oid}/vendors", headers=h("comp")).status_code == 403 and c.get(f"/api/accounts/{oid}/vendors", headers=h("comp")).status_code == 403     # compliance has no research access
    started = cr.post(f"/api/accounts/{oid}/vendors", headers=h("bdm"))
    assert started.status_code == 200 and started.json()["status"] == "queued"
    assert q1(conn, "select kind, max_attempts from jobs where kind='vendor_intel'")["max_attempts"] == 1
    assert q1(conn, "select count(*) n from audit_events where operation='exa_vendor_research' and actor='bdm@x'")["n"] == 1
    assert cr.post(f"/api/accounts/{oid}/vendors", headers=h("bdm")).status_code == 409                # already running
    assert cr.get(f"/api/accounts/{oid}/vendors", headers=h("bdm")).json()["running"] is True
    # a finished run is returned with how many pages went on to be checked, and the cool-down stops a repeat
    rid = q1(conn, "select id from account_vendor_intel where organization_id=%s", (oid,))["id"]
    conn.execute("update account_vendor_intel set status='done', finished_at=now(), content=%s, pages_queued=3 where id=%s", (json.dumps({"vendors": [], "angles": []}), rid))
    conn.commit()
    done = cr.get(f"/api/accounts/{oid}/vendors", headers=h("bdm")).json()
    assert done["profile"]["pages_queued"] == 3 and done["running"] is False
    assert cr.post(f"/api/accounts/{oid}/vendors", headers=h("bdm")).status_code == 409
    # another seller's account is invisible to a BDM who does not own it: 404, not 403
    theirs = env["o"]["theirs"]
    assert c.get(f"/api/accounts/{theirs}/vendors", headers=h("bdm")).status_code == 404
    assert c.get(f"/api/accounts/{oid}/vendors").status_code in (401, 403)        # no sign-in
