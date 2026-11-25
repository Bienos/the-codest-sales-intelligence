"""SYNTHETIC demo data at realistic scale: ~20 invented financial companies across the UK, DACH, the Nordics and Poland, each with verified buying
signals, contacts, a Pipedrive mirror, Lemlist activity and Apollo candidates. For UI work, walkthroughs and tests ONLY.

Every name is invented, every web address uses the reserved .example / example.invalid domains, and every email uses example.invalid.
Refuses to run unless RI_ENV=development. Run after `ri.cli migrate` and `ri.cli seed`:  PYTHONPATH=src python scripts/seed_demo_rich.py
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ri.config import Settings  # noqa: E402
from ri.db import connect  # noqa: E402
from ri.ingest.process import finish_event  # noqa: E402
from ri.opportunity import engine  # noqa: E402
from ri.scoring import recompute_scores  # noqa: E402
from ri.signals.baseline import import_baseline  # noqa: E402
from ri.signals.persist import sync_rule_versions  # noqa: E402
from ri.sources.registry import seed_registry  # noqa: E402

if os.environ.get("RI_ENV", "development") != "development":
    sys.exit("refusing to seed demo data outside development")

rnd = random.Random(7)
now = datetime.now(timezone.utc)

# name, country, segment, employees, pipedrive owner (555 = demo BDM, 666 = another seller, None = unowned), open deal
COMPANIES = [
    ("Northbridge Bank", "GB", "Banking", 42000, 555, False), ("Kestrel Payments", "GB", "Payments", 1800, None, False),
    ("Harbourline Insurance", "GB", "Insurance", 9500, 666, False), ("Fenwick Lending", "GB", "Lending", 1200, None, True),
    ("Alpenbank Privat", "CH", "Banking", 3200, None, False), ("Rheinwerk Finanz", "DE", "Financial services", 5400, 555, False),
    ("Isarpay", "DE", "Payments", 900, None, False), ("Donau Versicherungen", "AT", "Insurance", 7600, None, False),
    ("Fjordline Bank", "NO", "Banking", 4100, None, False), ("Skarven Pay", "SE", "Payments", 1400, 555, False),
    ("Nordvik Wealth", "SE", "Wealth management", 2100, None, False), ("Lindqvist Leasing", "DK", "Leasing", 650, None, False),
    ("Wisła Bank Cyfrowy", "PL", "Banking", 6800, None, False), ("Kraków Fintech Hub", "PL", "Fintech", 700, None, False),
    ("Bałtyk Ubezpieczenia", "PL", "Insurance", 3900, 666, False), ("Helsinki Capital Markets", "FI", "Capital markets", 1600, None, False),
    ("Amstel Clearing", "NL", "Payments", 2500, None, False), ("Lisboa Neobank", "PT", "Banking", 1100, None, False),
]

SIGNALS = {
    "funding": ("funding_announced", {"funding_stage": "series_b", "funding_use_stated": True},
                "{n} raises a Series B round to expand its engineering team and payments platform", "S4"),
    "hiring": ("engineering_job_opened", {"open_status": "open", "title": "Senior Backend Engineer, Payments Platform", "employment_type": "permanent", "tech_keywords": ["java", "kafka"]},
               "{n} is hiring a Senior Backend Engineer for its payments platform", "S2"),
    "ai_hiring": ("engineering_job_opened", {"open_status": "open", "title": "Machine Learning Engineer, LLM platform", "tech_keywords": ["llm", "python"]},
                  "{n} is hiring a Machine Learning Engineer to put its LLM assistant into production", "S2"),
    "cto": ("technology_leader_appointed", {"leader_role": "cto", "leader_start_date": (now - timedelta(days=45)).date().isoformat()},
            "{n} appoints a new Chief Technology Officer to lead its platform modernisation", "S3"),
    "payments": ("programme_announced", {"programme_domain": "payments_rail", "named_systems": ["SEPA Instant", "ISO 20022"], "programme_status": "implementation"},
                 "{n} starts a programme to move its payments to SEPA Instant and ISO 20022", "S3"),
    "core": ("vendor_selected", {"programme_domain": "core_banking", "named_systems": ["Example Core"], "has_dated_milestones": True, "counterparty_selected": True,
                                 "programme_status": "implementation"}, "{n} selects a new core banking platform with a migration planned in waves", "S3"),
    "launch": ("market_launch", {"market_launch_dated": True}, "{n} announces its launch in Germany with a date set for the spring", "S4"),
    "product": ("product_launch", {"new_product_line": "cards"}, "{n} launches a new card product for small businesses", "S4"),
    "cloud": ("programme_announced", {"programme_domain": "cloud_data", "named_systems": ["Example Cloud", "Kafka"], "has_dated_milestones": True, "programme_status": "implementation"},
              "{n} plans to move its data platform to the cloud by the end of next year", "S3"),
}
PLAN = [["funding", "hiring"], ["payments"], ["cto"], ["hiring"], ["core"], ["cloud", "cto"], ["funding"], ["product"], ["payments", "hiring"], ["launch"],
        ["ai_hiring"], ["funding"], ["core", "hiring"], ["ai_hiring", "funding"], ["cto"], ["cloud"], ["payments"], ["launch", "product"]]
FIRST = ["Anna", "James", "Sofia", "Lukas", "Maja", "Oliver", "Emma", "Jonas", "Piotr", "Elin", "Henrik", "Laura", "Tomasz", "Ingrid", "Matteo", "Clara", "Erik", "Zofia"]
LAST = ["Clarke", "Weber", "Nilsson", "Kowalski", "Berg", "Hughes", "Fischer", "Lind", "Nowak", "Haugen", "Brennan", "Keller", "Dahl", "Wójcik", "Moreau", "Sousa"]
TITLES = ["Chief Technology Officer", "VP Engineering", "Head of Payments", "Head of Platform", "Chief Information Officer", "Head of Data and AI", "Engineering Manager", "Head of Product"]

s = Settings.from_env()
conn = connect(s.database_url)
import_baseline(conn); sync_rule_versions(conn); seed_registry(conn)
for email, name, role, team, pd_id in (("bdm@demo.invalid", "Demo BDM", "bdm", "t1", 555), ("bdm2@demo.invalid", "Other BDM", "bdm", "t2", 666),
                                       ("manager@demo.invalid", "Demo Manager", "sales_manager", "t1", 700), ("admin@demo.invalid", "Demo Admin", "admin", "t1", None),
                                       ("analyst@demo.invalid", "Demo Analyst", "research_analyst", "t1", None)):
    conn.execute("INSERT INTO users(email, display_name, role, team, pipedrive_user_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (email, name, role, team, pd_id))

n_ev = [0]
orgs: list[tuple[str, str, str]] = []
for i, (name, cc, seg, emp, owner, open_deal) in enumerate(COMPANIES):
    slug = name.lower().replace(" ", "-").replace("ł", "l").replace("ś", "s").replace("ó", "o").replace("ż", "z").replace("ć", "c").replace("ą", "a").replace("ę", "e")
    domain = f"{slug}.example"
    oid = str(conn.execute("INSERT INTO organizations(display_name, country_code, employees_estimate, employees_basis, segment, in_master_list, canonical_domain, entity_type, "
                           "company_ownership, company_profile_source_url) VALUES (%s,%s,%s,'sheet_recorded',%s,%s,%s,'company','private',%s) RETURNING id",
                           (name, cc, emp, seg, i % 3 != 2, domain, f"https://example.invalid/profiles/{slug}")).fetchone()["id"])
    conn.execute("INSERT INTO organization_aliases(organization_id, alias, alias_norm) VALUES (%s,%s,%s)", (oid, name, name.lower()))
    orgs.append((oid, name, domain))
    if owner is not None or i % 4 == 1:
        crm_id = 9100 + i
        conn.execute("INSERT INTO crm_organizations(crm_id, organization_id, name, owner_id, open_deals_count, link_status) VALUES (%s,%s,%s,%s,%s,'linked')",
                     (crm_id, oid, name, owner, 1 if open_deal else 0))
        if open_deal:
            conn.execute("INSERT INTO crm_deals(crm_id, crm_org_id, title, owner_id, status) VALUES (%s,%s,%s,%s,'open')", (9500 + i, crm_id, f"{name} platform team", owner))
    if i % 3 == 0:     # a row from the workbooks, so the sheets view and the shortlist have something to show
        link = conn.execute("INSERT INTO account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at) "
                            "VALUES (%s,'xlsx','demo-workbook','demo-sheet','PRIORITY OUTREACH',%s,%s,%s,%s,now()) RETURNING id",
                            (oid, f"demo-row-{i}", 10 + i, f"demo-hash-{i}", name)).fetchone()["id"]
        conn.execute("INSERT INTO legacy_account_research(organization_id, source_link_id, kind, original, observed_at) VALUES (%s,%s,'priority_outreach',%s,now())",
                      (oid, link, json.dumps({"Company": name, "Strongest buying signal": f"{name} is modernising its platform (sheet note, not yet verified)",
                                              "Primary source URL": f"https://example.invalid/news/{slug}"})))
    # signals: each event has a quoted claim from a (synthetic) source page
    for k, key in enumerate(PLAN[i]):
        etype, attrs, summary, tier = SIGNALS[key]
        n_ev[0] += 1
        when = now - timedelta(days=rnd.randint(2, 40) + k * 3)
        text = summary.format(n=name)
        doc = conn.execute("INSERT INTO source_documents(source_id, external_id, version_key, canonical_url, title, published_at, content_text, excerpt) "
                           "VALUES ('verify-original-page',%s,'1',%s,%s,%s,%s,%s) RETURNING id",
                           (f"demo-doc-{n_ev[0]}", f"https://example.invalid/news/{slug}/{key}", text, when, f"{text}. More detail on the page.", text)).fetchone()["id"]
        ev = conn.execute("INSERT INTO events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                          "VALUES (%s,%s,%s,%s,%s,true,%s,%s,'[]','synthetic-demo','verified',%s) RETURNING id",
                          (f"rich{n_ev[0]}", etype, oid, when, when, text, json.dumps({"source_tier": tier, **attrs}), f"rich-g{n_ev[0]}")).fetchone()["id"]
        c = conn.execute("INSERT INTO claims(subject,predicate,object_json,evidence_anchor,anchor_verified,claim_kind,status,source_document_id) "
                         "VALUES (%s,'announced',%s,%s,true,'quote','supported',%s) RETURNING id",
                         (name, json.dumps({"url": f"https://example.invalid/news/{slug}/{key}"}), text, doc)).fetchone()["id"]
        conn.execute("INSERT INTO event_claims(event_id, claim_id) VALUES (%s,%s)", (ev, c))
        conn.execute("INSERT INTO event_documents(event_id, document_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (ev, doc))
        finish_event(conn, str(ev), now=now)
    # people: two to four per company, most with verified emails
    for j in range(2 + i % 3):
        first, last = FIRST[(i + j * 5) % len(FIRST)], LAST[(i * 3 + j) % len(LAST)]
        status = "Verified" if (i + j) % 4 != 3 else "Needs Verification"
        pid = str(conn.execute("INSERT INTO people(full_name, work_email, email_verification_status, suppressed, suppression_reason) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                               (f"{first} {last}", f"{first.lower()}.{last.lower()}@{slug}.example.invalid", status, (i * j) % 11 == 7,
                                "unsubscribed through Lemlist" if (i * j) % 11 == 7 else None)).fetchone()["id"])
        conn.execute("INSERT INTO employment(person_id, organization_id, title, buyer_lane, verification_status, source_link_id) VALUES (%s,%s,%s,'Technology','unverified',%s)",
                     (pid, oid, TITLES[(i + j) % len(TITLES)], link if i % 3 == 0 and j == 0 else None))
        if j == 0 or i % 2 == 1:      # most contacts came from the workbooks
            conn.execute("INSERT INTO person_sources(person_id, system) VALUES (%s,'sheets') ON CONFLICT DO NOTHING", (pid,))
        if j == 1 and i % 2 == 0:       # found through Apollo
            conn.execute("INSERT INTO contact_candidates(organization_id, provider, external_id, name, title, person_id, status, found_by) VALUES (%s,'apollo',%s,%s,%s,%s,'revealed','bdm@demo.invalid')",
                         (oid, f"apollo-{i}-{j}", f"{first} {last}", TITLES[(i + j) % len(TITLES)], pid))
            conn.execute("INSERT INTO person_sources(person_id, system, external_id) VALUES (%s,'apollo',%s) ON CONFLICT DO NOTHING", (pid, f"apollo-{i}-{j}"))
        crm = conn.execute("SELECT crm_id, owner_id FROM crm_organizations WHERE organization_id=%s", (oid,)).fetchone()
        if crm and j == 0:              # in Pipedrive
            conn.execute("INSERT INTO crm_persons(crm_id, person_id, crm_org_id, name, owner_id) VALUES (%s,%s,%s,%s,%s)", (7000 + i * 10 + j, pid, crm["crm_id"], f"{first} {last}", crm["owner_id"]))
            conn.execute("INSERT INTO person_sources(person_id, system, external_id) VALUES (%s,'pipedrive',%s) ON CONFLICT DO NOTHING", (pid, str(7000 + i * 10 + j)))
        if i in (2, 7, 11) and j == 0:  # earlier Lemlist outreach with a reply
            for t, d in (("emailsSent", 20), ("emailsOpened", 19), ("emailsReplied", 17)):
                conn.execute("INSERT INTO lemlist_activities(id, type, email, campaign_id, campaign_name, person_id, happened_at) VALUES (%s,%s,%s,'cmp_demo','Q3 platform outreach',%s,%s)",
                             (f"act-{i}-{t}", t, f"{first.lower()}.{last.lower()}@{slug}.example.invalid", pid, now - timedelta(days=d)))
            conn.execute("INSERT INTO person_sources(person_id, system, external_id) VALUES (%s,'lemlist','cmp_demo') ON CONFLICT DO NOTHING", (pid,))

for i in conn.execute("SELECT id FROM initiatives").fetchall():
    engine.build_for_initiative(conn, str(i["id"]))
for h in conn.execute("SELECT id FROM opportunity_hypotheses").fetchall():
    engine.evaluate(conn, str(h["id"]), now=now, requester_crm_user=555)
recompute_scores(conn, now)
for sid in ("procurement-ted", "regulator-fca", "attention-sifted", "attention-eustartups", "jobs-jobtech-se"):
    conn.execute("UPDATE sources SET status='active' WHERE source_id=%s", (sid,))
    conn.execute("UPDATE source_health SET state='healthy', last_success_at=now() - interval '2 hours' WHERE source_id=%s", (sid,))
conn.commit()
print("rich demo seeded:", {k: conn.execute(f"SELECT count(*) n FROM {k}").fetchone()["n"] for k in ("organizations", "events", "signals", "people", "opportunity_hypotheses", "account_scores")})
