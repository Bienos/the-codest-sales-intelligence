"""More ways to watch competitors: sitemaps, own feeds, GDELT, Clutch widget, job systems and role mix, KRS and Companies House, Polish and
UK tenders, AWS partner finder and vendor partner pages. Every source: the first run is a baseline; changes after it wait for review."""
import json
from datetime import datetime, timedelta, timezone

from ri import competitor_sources as cs
from ri import competitor_watch as cw
from ri import competitors as ct
from ri.db import jb

NOW = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)


def pages(d):
    calls = []

    def fetch(url):
        calls.append(url)
        for k, v in d.items():
            if url == k or (k.endswith("*") and url.startswith(k[:-1])):
                return v(url) if callable(v) else v
        raise RuntimeError("404")
    fetch.calls = calls
    return fetch


def firm(conn, tier=1):
    return next(c for c in cw.tracked(conn) if c["tier"] == tier)


def track(conn, key, **ids):
    conn.execute("UPDATE competitors SET profile = jsonb_set(profile, '{track}', %s) WHERE key=%s", (jb(ids), key))


def cands(conn, key, actor=None):
    return conn.execute("SELECT * FROM competitor_moves WHERE status='candidate' AND competitor_key=%s" + (" AND added_by=%s" if actor else "") + " ORDER BY created_at",
                        (key, actor) if actor else (key,)).fetchall()


def smap(*paths, host="https://acme.example"):
    return "<urlset>" + "".join(f"<url><loc>{host}{p}</loc></url>" for p in paths) + "</urlset>"


def test_sitemap_baseline_then_new_pages_grouped_by_kind(conn):
    ct.load_seed(conn)
    f = firm(conn)
    site = f["url"].rstrip("/")
    track(conn, f["key"], sitemap=site + "/sitemap.xml")
    index = f"<sitemapindex><sitemap><loc>{site}/page-sitemap.xml</loc></sitemap><sitemap><loc>{site}/blog-sitemap.xml</loc></sitemap></sitemapindex>"
    one = smap("/", "/services/web-development/", "/case-studies/acme-shop/", host=site)
    comps = cw.tracked(conn)
    cs.sitemaps(conn, comps, pages({site + "/sitemap.xml": index, site + "/page-sitemap.xml": one}), NOW)
    assert not cands(conn, f["key"])                                      # first run: baseline only
    two = smap("/", "/services/web-development/", "/case-studies/acme-shop/", "/industries/banking/", "/services/dora-compliance/",
               "/case-studies/big-bank/", "/pricing/", "/blog/new-post/", host=site)
    fetch = pages({site + "/sitemap.xml": index, site + "/page-sitemap.xml": two})
    out = cs.sitemaps(conn, cw.tracked(conn), fetch, NOW + timedelta(days=8))
    assert site + "/blog-sitemap.xml" not in fetch.calls                  # blog sitemaps are skipped
    got = {m["move_type"]: m for m in cands(conn, f["key"], "collector:sitemap")}
    assert set(got) == {"vertical", "client", "pricing"} and out["sitemap_candidates"] == 3
    assert "banking" in got["vertical"]["headline"] and "dora compliance" in got["vertical"]["headline"] and got["vertical"]["impact"] == 2
    assert got["vertical"]["grade"] == "official" and got["client"]["source_url"] == site + "/case-studies/big-bank/"


def test_sitemap_found_through_robots(conn):
    fetch = pages({"https://acme.example/robots.txt": "User-agent: *\nSitemap: https://acme.example/wp-sitemap.xml\n"})
    assert cs.find_sitemap(fetch, "https://acme.example/en/") == "https://acme.example/wp-sitemap.xml"


RSS = """<rss><channel><title>Blog</title>{}</channel></rss>"""
ITEM = "<item><title>{t}</title><link>{l}</link><pubDate>Tue, 24 Nov 2026 10:00:00 GMT</pubDate><description><![CDATA[<p>{d}</p>]]></description></item>"


def test_own_feed_finds_new_posts_after_the_baseline(conn):
    ct.load_seed(conn)
    f = firm(conn)
    home = f["url"]
    feed = home.rstrip("/") + "/feed/"
    first = RSS.format(ITEM.format(t="Old post", l=feed + "old", d="x"))
    cs.feeds(conn, cw.tracked(conn), pages({home: f'<link rel="alternate" type="application/rss+xml" href="{feed}">', feed: first}), NOW)
    assert not cands(conn, f["key"])
    second = RSS.format(ITEM.format(t=f"{f['name']} opens an office in Stockholm", l=feed + "stockholm", d="Nordic expansion") + ITEM.format(t="Old post", l=feed + "old", d="x"))
    cs.feeds(conn, cw.tracked(conn), pages({feed: second}), NOW + timedelta(days=8))
    c = cands(conn, f["key"], "collector:feed")
    assert len(c) == 1 and c[0]["move_type"] == "market" and str(c[0]["happened_on"]) == "2026-11-24" and c[0]["grade"] == "official"


def test_gdelt_batches_firms_keeps_only_titles_that_name_them_and_respects_the_rate_limit(conn):
    ct.load_seed(conn)
    comps = cw.tracked(conn)
    t1 = [c for c in comps if c["tier"] == 1 and cs.news_name(c)]
    a = t1[0]
    naps = []

    def gd(url):
        return json.dumps({"articles": [{"url": "https://press.example/a", "title": f"{a['name']} acquires a payments boutique", "seendate": "20261128T101500Z", "domain": "press.example"},
                                        {"url": "https://press.example/b", "title": "Unrelated story about software", "seendate": "20261128T101500Z"}]})
    out = cs.gdelt(conn, comps, pages({"https://api.gdeltproject.org/*": gd}), NOW, sleep=naps.append)
    assert out["gdelt_queries"] >= 2 and len(naps) == out["gdelt_queries"] - 1 and all(n >= 5 for n in naps)
    c = cands(conn, a["key"], "collector:gdelt")
    assert len(c) == 1 and c[0]["move_type"] == "ma_buy" and str(c[0]["happened_on"]) == "2026-11-28"
    assert cs.news_name({"name": "Sii Poland", "aliases": []}) == "Sii Poland" and cs.news_name({"name": "N-iX", "aliases": []}) is None


def test_gdelt_stops_for_the_day_when_refused(conn):
    ct.load_seed(conn)
    naps = []
    out = cs.gdelt(conn, cw.tracked(conn), pages({}), NOW, sleep=naps.append)
    assert out["gdelt_queries"] == 1 and out["gdelt_failed"] == 1 and not naps


WIDGET = "<html><style>.a{}</style><body>Acme S.A. Clutch Review Widget <span>4.7</span> <i>4.700000</i> <b>61 reviews</b> <q>“They delivered our banking app on time and on budget.”</q></body></html>"


def test_clutch_widget_updates_the_profile_and_flags_new_reviews(conn):
    ct.load_seed(conn)
    f = firm(conn)
    track(conn, f["key"], clutchUid=123)
    conn.execute("UPDATE competitors SET profile = jsonb_set(profile, '{clutch}', %s) WHERE key=%s",
                 (jb({"rating": 4.9, "reviews": 50, "rate": "$50–99/hr", "source": "https://clutch.co/profile/acme", "asOf": "2026-11", "grade": "reported"}), f["key"]))
    assert cs.parse_clutch(WIDGET) == {"rating": 4.7, "reviews": 61, "quotes": ["They delivered our banking app on time and on budget."]}
    cs.clutch(conn, cw.tracked(conn), pages({"https://widget.clutch.co/*": WIDGET}), NOW)
    p = conn.execute("SELECT profile->'clutch' c FROM competitors WHERE key=%s", (f["key"],)).fetchone()["c"]
    assert p["reviews"] == 61 and p["rating"] == 4.7 and p["rate"] == "$50–99/hr" and p["asOf"] == "2026-12-01"
    heads = sorted(m["headline"] for m in cands(conn, f["key"], "collector:clutch"))
    assert heads == ["11 new Clutch reviews (50 to 61, rating 4.7)", "Clutch rating falls from 4.9 to 4.7"]
    ct.load_seed(conn)                                                   # a deploy re-runs the seed: the newer count stays
    assert conn.execute("SELECT profile->'clutch'->>'reviews' r FROM competitors WHERE key=%s", (f["key"],)).fetchone()["r"] == "61"


def test_job_systems_give_the_role_mix_and_sales_hiring_in_our_markets(conn):
    ct.load_seed(conn)
    f = firm(conn)
    track(conn, f["key"], ats={"kind": "workable", "slug": "acme"}, nfj=f["name"])
    wk1 = json.dumps({"jobs": [{"title": "Senior Python Developer", "city": "Krakow", "country": "Poland"}]})
    wk2 = json.dumps({"jobs": [{"title": "Senior Python Developer", "city": "Krakow", "country": "Poland"},
                               {"title": "Account Executive, Financial Services", "city": "London", "country": "United Kingdom"},
                               {"title": "Machine Learning Engineer", "city": "", "country": "Poland"}]})
    nfj = json.dumps({"postings": [{"name": f["name"], "title": "AI Engineer (LLM)", "location": {"places": [{"city": "Remote"}]}},
                                   {"name": "Someone Else", "title": "Sales Manager", "location": {"places": [{"city": "London"}]}}]})
    post = lambda url, body: nfj                                             # noqa: E731
    cs.careers(conn, cw.tracked(conn), pages({"https://apply.workable.com/api/v1/widget/accounts/acme": wk1}), post, NOW)
    assert not cands(conn, f["key"])                                         # baseline
    mix = conn.execute("SELECT profile->'roleMix' m FROM competitors WHERE key=%s", (f["key"],)).fetchone()["m"]
    assert mix["total"] == 2 and mix["ai"] == 1 and mix["grade"] == "official"
    cs.careers(conn, cw.tracked(conn), pages({"https://apply.workable.com/api/v1/widget/accounts/acme": wk2}), post, NOW + timedelta(days=8))
    c = cands(conn, f["key"], "collector:careers")
    assert [m["headline"] for m in c] == ["Hiring sales in UK: 1 open sales role"] and c[0]["impact"] == 2 and "Account Executive" in c[0]["detail"]
    assert cs.classify_roles([{"title": "Business Development Manager DACH", "location": "Berlin"}])["salesMarkets"] == {"DACH": 1}


def test_other_job_systems_parse():
    gh = json.dumps({"jobs": [{"title": "Delivery Manager", "location": {"name": "Warsaw"}}]})
    jobs, url = cs.ats_jobs("greenhouse", "acme", pages({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": gh}))
    assert jobs == [{"title": "Delivery Manager", "location": "Warsaw"}] and url == "https://boards.greenhouse.io/acme"
    lv = json.dumps([{"text": "Data Engineer", "categories": {"location": "Remote"}}])
    assert cs.ats_jobs("lever", "acme", pages({"https://api.lever.co/v0/postings/acme?mode=json": lv}))[0][0]["title"] == "Data Engineer"
    tt = "<rss><channel><title>Acme jobs</title><item><title>QA Engineer</title></item></channel></rss>"
    assert [j["title"] for j in cs.ats_jobs("teamtailor", "acme", pages({"https://acme.teamtailor.com/jobs.rss": tt}))[0]] == ["QA Engineer"]


KRS = {"odpis": {"naglowekA": {"numerOstatniegoWpisu": 41, "dataOstatniegoWpisu": "12.11.2026"},
                 "dane": {"dzial1": {"danePodmiotu": {"nazwa": "ACME SPÓŁKA AKCYJNA"}},
                          "dzial2": {"reprezentacja": {"sklad": [{"nazwisko": {"nazwiskoICzlon": "KOWALSKI"}, "imiona": {"imie": "JAN"}, "funkcjaWOrganie": "PREZES ZARZĄDU"}]}}}}}


def test_krs_board_change_and_companies_house_officers(conn):
    ct.load_seed(conn)
    f = firm(conn)
    track(conn, f["key"], krs=["435101"], ukCompany="05722669")
    assert cs.parse_krs(KRS)["board"] == ["Jan Kowalski (prezes zarządu)"]
    ch1 = '<div class="appointment-1"><h2><span id="officer-name-1"><a href="#">SMITH, Anna</a></span></h2><dd id="officer-role-1">Director</dd></div>'
    ch2 = ch1 + '<div class="appointment-2"><h2><span id="officer-name-2"><a href="#">BROWN, Tom</a></span></h2><dd id="officer-role-2">Director</dd></div>' + \
        '<div class="appointment-3"><h2><span id="officer-name-3"><a href="#">OLD, Person</a></span></h2><dd>Resigned on 1 May 2020</dd></div>'
    krs_url = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/0000435101?rejestr=P&format=json"
    ch_url = "https://find-and-update.company-information.service.gov.uk/company/05722669/officers"
    cs.registers(conn, cw.tracked(conn), pages({krs_url: json.dumps(KRS), ch_url: ch1}), NOW)
    assert not cands(conn, f["key"])
    k2 = json.loads(json.dumps(KRS))
    k2["odpis"]["dane"]["dzial2"]["reprezentacja"]["sklad"][0]["nazwisko"]["nazwiskoICzlon"] = "NOWAK"
    out = cs.registers(conn, cw.tracked(conn), pages({krs_url: json.dumps(k2), ch_url: ch2}), NOW + timedelta(days=31))
    heads = sorted(m["headline"] for m in cands(conn, f["key"]))
    assert out["krs_candidates"] == 1 and out["ch_candidates"] == 1
    assert heads == ["Board change in the Polish register: joins: Jan Nowak (prezes zarządu); leaves: Jan Kowalski (prezes zarządu)",
                     "UK company officers change: appointed: Brown, Tom (director)"]


def test_polish_and_uk_tender_wins_become_candidate_client_wins(conn):
    ct.load_seed(conn)
    f = firm(conn)
    legal = f["name"].upper() + " SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ"
    bzp = json.dumps([{"bzpNumber": "2026/BZP 1", "objectId": "abc", "organizationName": "Urząd Miasta", "orderObject": "Rozwój systemu",
                       "cpvCode": "72000000-5 (Usługi IT)", "publicationDate": "2026-11-30T08:00:00Z", "contractors": [{"contractorName": legal}]},
                      {"bzpNumber": "2026/BZP 2", "objectId": "def", "organizationName": "X", "orderObject": "Y", "cpvCode": "72000000-5",
                       "publicationDate": "2026-11-30T08:00:00Z", "contractors": [{"contractorName": "NOBODY WE TRACK SP. Z O.O."}]}])
    cf = json.dumps({"releases": [{"id": "uuid-1-922252", "date": "2026-11-30T10:00:00Z", "buyer": {"name": "NHS Digital"},
                                   "tender": {"title": "Platform engineering", "classification": {"id": "72200000"}},
                                   "awards": [{"suppliers": [{"name": f"{f['name']} Ltd"}]}]}], "links": {}})
    fetch = pages({"https://ezamowienia.gov.pl/mo-board/*": bzp, "https://www.contractsfinder.service.gov.uk/*": cf,
                   "https://www.find-tender.service.gov.uk/*": json.dumps({"releasePackages": [], "links": {}})})
    out = cs.tenders(conn, cw.tracked(conn), fetch, NOW, sleep=lambda s: None)
    assert out["awards_stored"] == 2
    made = cw.awards_since(conn, cw.tracked(conn), NOW)
    c = cands(conn, f["key"], "collector:awards")
    assert made == 2 and {m["source_url"] for m in c} == {"https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/abc",
                                                          "https://www.contractsfinder.service.gov.uk/Notice/uuid-1"}
    assert all(m["impact"] == 2 for m in c)                                  # our markets, tier 1


def test_aws_tier_and_vendor_partner_pages(conn):
    ct.load_seed(conn)
    f = firm(conn)
    host = f["url"]

    def aws(tier, comp):
        return json.dumps({"message": {"results": [{"_source": {"name": f["name"], "website": host, "current_program_status": tier, "aws_certifications_count": 40,
                                                                 "competencies_count": comp, "references_casestudy_count": 3}}]}})
    out = cs.aws(conn, cw.tracked(conn), pages({"https://api.finder.partners.aws.a2z.com/*": aws("Select", 0)}), NOW, sleep=lambda s: None)
    assert out["aws_checked"] == cs.PER_RUN["aws"]
    assert conn.execute("SELECT profile->'aws'->>'tier' t FROM competitors WHERE key=%s", (f["key"],)).fetchone()["t"] == "Select"
    cs.aws(conn, cw.tracked(conn), pages({"https://api.finder.partners.aws.a2z.com/*": aws("Advanced", 1)}), NOW + timedelta(days=31), sleep=lambda s: None)
    assert sorted(m["headline"] for m in cands(conn, f["key"], "collector:aws")) == ["AWS partner tier changes: Select to Advanced", "New AWS competency: 0 to 1"]
    page1 = "<html><li>Accenture</li></html>"
    page2 = f"<html><li>Accenture</li><li>{f['name']}</li></html>"
    cs.partner_pages(conn, cw.tracked(conn), pages({u: page1 for _, u in cs.PARTNER_PAGES}), NOW)
    cs.partner_pages(conn, cw.tracked(conn), pages({u: page2 for _, u in cs.PARTNER_PAGES}), NOW + timedelta(days=31))
    heads = sorted(m["headline"] for m in cands(conn, f["key"], "collector:partners"))
    assert heads == ["Listed as a 10x Banking partner", "Listed as a Thought Machine partner"]


def test_the_daily_job_runs_every_source_and_one_broken_source_never_stops_the_rest(conn, monkeypatch):
    ct.load_seed(conn)
    monkeypatch.setattr(cs, "sitemaps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = cw.run(conn, fetch=pages({}), now=NOW, post=lambda u, b: "{}", sleep=lambda s: None)
    assert any("boom" in e for e in out["errors"]) and "feeds_checked" in out and "tenders" not in out and "bzp_notices" in out and "krs_checked" in out
    names = {r["check_name"] for r in conn.execute("SELECT DISTINCT check_name FROM competitor_watch").fetchall()}
    assert {"feed", "gdelt", "tenders", "partners"} <= names
