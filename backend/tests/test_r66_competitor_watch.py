"""Weekly competitor collection: awards, open roles, homepage headlines and news become reviewable moves; nothing reaches the page unconfirmed."""
from datetime import datetime, timedelta, timezone

from ri import competitor_watch as cw
from ri import competitors as ct
from ri.crm import pipedrive as pd

NOW = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)


def sitemap(*paths):
    return "<urlset>" + "".join(f"<url><loc>https://justjoin.it/job-offer/{p}</loc></url>" for p in paths) + "</urlset>"


def fake_fetch(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        if url in pages:
            return pages[url]
        raise RuntimeError("404")
    fetch.calls = calls
    return fetch


def candidates(conn, key=None):
    return conn.execute("select * from competitor_moves where status='candidate'" + (" and competitor_key=%s" if key else "") + " order by created_at",
                        (key,) if key else None).fetchall()


def test_parsing_helpers():
    assert cw.headline_of("<html><head><title>T</title></head><body><h1>  Engineering <b>for</b> banks </h1><h1>x</h1></body></html>") == "Engineering for banks"
    assert cw.headline_of('<head><meta property="og:title" content="OG line"><title>Title</title></head>') == "OG line"
    counts = cw.count_offers(["https://justjoin.it/job-offer/sii-java-dev-krakow-1", "https://justjoin.it/job-offer/stx-next-senior-data-2",
                              "https://justjoin.it/job-offer/siili-react-3", "https://justjoin.it/job-offer/sii-qa-4"], {"sii": "sii", "stx-next": "stx-next"})
    assert counts == {"sii": 2, "stx-next": 1}
    assert cw.guess_type("Neontri acquires a payments boutique") == "ma_buy" and cw.guess_type("A webinar") == "content"


def t1(conn):
    """A tier 1 firm, whichever the seed makes it: tests must not depend on one firm staying in the top seven."""
    return next(c for c in ct.tracker(conn)["competitors"] if c["tier"] == 1)


def test_homepage_change_waits_for_review_then_updates_the_profile(conn):
    ct.load_seed(conn)
    firm = t1(conn)
    key, url = firm["key"], firm["url"]
    pages = {url: "<h1>Core banking engineering, done right</h1>", cw.JOBS_SITEMAP.format(0): sitemap()}
    out = cw.run(conn, fetch=fake_fetch(pages), now=NOW)
    assert out["headlines_changed"] >= 1
    c = candidates(conn, key)
    assert len(c) == 1 and c[0]["move_type"] == "positioning" and c[0]["payload"]["hero"] == "Core banking engineering, done right"
    assert all(m["headline"] != c[0]["headline"] for x in ct.tracker(conn)["competitors"] for m in x["moves"])      # not shown before review
    # the next day nothing is due; a week later the same headline is not suggested again
    f = fake_fetch(pages)
    cw.run(conn, fetch=f, now=NOW + timedelta(days=1))
    assert url not in f.calls                             # checked yesterday: not due (the batch moves on to other firms)
    cw.run(conn, fetch=fake_fetch(pages), now=NOW + timedelta(days=8))
    assert len(candidates(conn, key)) == 1
    ct.review_move(conn, str(c[0]["id"]), status="confirmed", actor="mkt@x", so_what="They now lead with core banking.")
    hero = conn.execute("select profile->'hero' h from competitors where key=%s", (key,)).fetchone()["h"]
    assert hero["value"] == "Core banking engineering, done right" and hero["grade"] == "official" and hero["asOf"] == "2026-12-01"


def test_open_roles_are_counted_and_survive_a_reseed(conn):
    ct.load_seed(conn)
    pages = {cw.JOBS_SITEMAP.format(0): sitemap(*[f"stx-next-role-{i}" for i in range(30)], "sii-java-1")}
    cw.run(conn, fetch=fake_fetch(pages), now=NOW)
    jobs = conn.execute("select profile->'jobs' j from competitors where key='stx-next'").fetchone()["j"]
    assert jobs["n"] == 30 and jobs["asOf"] == "2026-12-01" and jobs["grade"] == "reported"
    assert any(m["move_type"] == "hiring" for m in candidates(conn, "stx-next"))          # 4 to 30 in a week: worth a look
    ct.load_seed(conn)                                                                     # a deploy re-runs the seed
    assert conn.execute("select profile->'jobs'->>'n' n from competitors where key='stx-next'").fetchone()["n"] == "30"


def test_new_awards_and_news_become_candidates(conn):
    ct.load_seed(conn)
    firm = t1(conn)
    key, name = firm["key"], firm["name"]
    conn.execute("insert into awarded_contracts(notice_id, buyer_name, buyer_country, winners, awarded_on, end_basis, relevant, official_url, title) "
                 "values ('n9','Bank Gospodarstwa','PL',%s,'2026-11-20','assumed',true,'https://ted.europa.eu/n9','Payment system')", ("{" + name + " sp. z o.o.}",))
    pages_seen = []

    def search(purpose, body):
        pages_seen.append(purpose)
        if purpose.endswith(":" + key):
            return [{"url": firm["url"].rstrip("/") + "/news/new-office", "title": f"{name} opens an office in Stockholm", "text": f"{name} opens a Nordic office.", "published": "2026-11-25"},
                    {"url": "https://example.com/other", "title": "Unrelated fintech news", "text": "Nothing about them.", "published": "2026-11-25"}]
        return []
    out = cw.run(conn, fetch=fake_fetch({cw.JOBS_SITEMAP.format(0): sitemap()}), search=search, now=NOW)
    assert out["award_candidates"] == 1 and out["news_searches"] == cw.SEARCHES_PER_RUN
    moves = {m["move_type"]: m for m in candidates(conn, key)}
    assert moves["client"]["source_url"] == "https://ted.europa.eu/n9" and moves["client"]["impact"] == 2       # our market, tier 1
    assert moves["market"]["grade"] == "official" and "Stockholm" in moves["market"]["headline"]
    assert not [m for m in candidates(conn) if m["source_url"] == "https://example.com/other"]                  # does not name the firm
    cw.run(conn, fetch=fake_fetch({}), search=search, now=NOW + timedelta(days=8))
    assert len([m for m in candidates(conn, key) if m["move_type"] == "client"]) == 1                     # never suggested twice


def test_the_lane_queues_one_job_a_day(conn):
    assert cw.schedule(conn, NOW) == 0                    # nothing tracked yet
    ct.load_seed(conn)
    assert cw.schedule(conn, NOW) == 1 and cw.schedule(conn, NOW + timedelta(hours=1)) == 0 and cw.schedule(conn, NOW + timedelta(days=1)) == 1
    assert "competitor_watch" in {lane.key for lane in __import__("ri.scheduler", fromlist=["LANES"]).LANES}


def test_pipedrive_v2_custom_fields_are_read(conn, monkeypatch):
    ct.load_seed(conn)
    monkeypatch.setenv("RI_PIPEDRIVE_COMPETITOR_FIELD", "f00d")
    pd.upsert_deal(conn, {"id": 5, "title": "x", "status": "won", "custom_fields": {"f00d": "Neontri"}})
    assert conn.execute("select role from deal_competitors where crm_deal_id=5").fetchone()["role"] == "won_against"
