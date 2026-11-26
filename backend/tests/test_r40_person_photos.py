"""LinkedIn profile pictures found by Exa research: only real profile photos, only on the person they belong to, shown by the API,
and the app falls back to initials when there is none. Exa and the model are FAKES (no network). All names are invented."""
from ri.db import q1
from ri.enrichment.exa_people import parse_result, profile_photo, set_photo
from ri.research import profile_jobs
from ri.research.exa_search import normalise_result
from test_r28_profiles import _linkedin_pages, caps, env, fake_model, fake_post, person  # noqa: F401  (env is a fixture)

PHOTO = "https://media.licdn.com/dms/image/v2/D4E03AQabc/profile-displayphoto-shrink_200_200/B4EZxyz/0/1700000000000?e=1900000000&v=beta&t=abc"
PHOTO_BEN = "https://media.licdn.com/dms/image/v2/C5603AQben/profile-displayphoto-shrink_800_800/0/1600000000000?e=1900000000&v=beta&t=def"
PHOTO_DAN = "https://media-exp1.licdn.com/dms/image/C5603AQdan/profile-displayphoto-shrink_200_200/0/1500000000000?e=1900000000&t=ghi"


def test_only_real_profile_photos_are_accepted():
    """The picture comes from the person's own LinkedIn page; whichever host serves it, logos, banners, placeholders and unsafe addresses are not photos."""
    assert profile_photo(PHOTO) == PHOTO and profile_photo(PHOTO_DAN) == PHOTO_DAN
    assert profile_photo("https://images.example.net/people/ada-clarke.jpg") == "https://images.example.net/people/ada-clarke.jpg"      # another host, still a plain picture
    for bad in (None, "", 42, ["x"],
                PHOTO.replace("https://", "http://"),                                           # not https
                "https://media.licdn.com/dms/image/v2/x/company-logo_200_200/0",                # a company logo
                "https://media.licdn.com/dms/image/v2/x/profile-displaybackgroundimage-shrink_350_1400/0",     # a banner
                "https://static.licdn.com/aero-v1/sc/h/9c8pery4andzj6ohjkjp54ma2",              # LinkedIn's grey placeholder
                "https://static.licdn.com/sc/h/default-avatar",
                "https://example.com/favicon.ico", "https://example.com/mark.svg",
                "javascript:alert(1)", "data:image/png;base64,AAAA",
                "https://media.licdn.com/dms/image/v2/x/profile-displayphoto 200/0",           # whitespace
                "https://media.licdn.com/dms/image/v2/x/profile-displayphoto/" + "a" * 700):    # absurdly long
        assert profile_photo(bad) is None, bad


def test_exa_result_carries_only_a_plain_https_image():
    ok = normalise_result({"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada", "text": "x", "image": PHOTO}, "linkedin")
    assert ok["image"] == PHOTO
    assert normalise_result({"url": "https://a.example/x", "image": "javascript:alert(1)"}, "p")["image"] is None
    assert normalise_result({"url": "https://a.example/x"}, "p")["image"] is None
    c = parse_result({"url": "https://www.linkedin.com/in/ben-weber", "title": "Ben Weber - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay", "image": PHOTO_BEN}, ["kestrel"])
    assert c["photo_url"] == PHOTO_BEN


def test_a_picture_is_kept_only_on_the_person_whose_linkedin_page_it_came_from(conn, env):
    ada, dan = env["p"]["ada"], env["p"]["dan"]
    assert set_photo(conn, dan, "https://www.linkedin.com/in/ada-clarke", PHOTO) is False         # Dan has no LinkedIn address: nothing to tie it to
    assert set_photo(conn, ada, "https://www.linkedin.com/in/someone-else", PHOTO) is False       # a namesake's page
    assert set_photo(conn, ada, "https://www.linkedin.com/in/ada-clarke", None) is False
    assert q1(conn, "select photo_url from people where id=%s", (ada,))["photo_url"] is None
    assert set_photo(conn, ada, "https://uk.linkedin.com/in/ADA-clarke/", PHOTO) is True          # same profile written another way
    row = q1(conn, "select photo_url, photo_checked_at from people where id=%s", (ada,))
    assert row["photo_url"] == PHOTO and row["photo_checked_at"] is not None


def test_finding_people_stores_pictures_on_candidates_and_known_people_and_adding_carries_them(conn, env):
    caps(conn)
    oid, ada = env["o"]["open"], env["p"]["ada"]
    pages = _linkedin_pages()
    by_url = {r["url"]: r for rs in pages.values() for r in rs}
    by_url["https://uk.linkedin.com/in/ada-clarke/"]["image"] = PHOTO                              # already on file, picture missing
    by_url["https://www.linkedin.com/in/ben-weber"]["image"] = PHOTO_BEN                           # a new person
    by_url["https://www.linkedin.com/in/dan-nowak-1"]["image"] = PHOTO_DAN                         # known by name only (no LinkedIn address on file yet)
    sid = str(conn.execute("insert into people_searches(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    out = profile_jobs.people_search(conn, {"payload": {"search_id": sid}}, post_factory=fake_post(pages, []))
    assert out["status"] == "done"
    cands = {r["name"]: r for r in conn.execute("select * from people_candidates where organization_id=%s", (oid,)).fetchall()}
    assert cands["Ben Weber"]["photo_url"] == PHOTO_BEN and cands["Ada Clarke"]["photo_url"] == PHOTO
    assert q1(conn, "select photo_url from people where id=%s", (ada,))["photo_url"] == PHOTO                      # the known person got a face
    assert q1(conn, "select photo_url from people where id=%s", (env["p"]["dan"],))["photo_url"] is None          # no address to tie it to yet

    c, h = env["c"], env["h"]
    state = c.get(f"/api/accounts/{oid}/people-search", headers=h("bdm")).json()
    assert {i["name"]: i["photo_url"] for i in state["items"]}["Ben Weber"] == PHOTO_BEN
    pid = c.post(f"/api/people-candidates/{cands['Ben Weber']['id']}/add", headers=h("bdm")).json()["person_id"]
    assert q1(conn, "select photo_url from people where id=%s", (pid,))["photo_url"] == PHOTO_BEN
    # adding Dan connects his LinkedIn address, and with it the picture
    c.post(f"/api/people-candidates/{cands['Dan Nowak']['id']}/add", headers=h("bdm"))
    assert q1(conn, "select photo_url from people where id=%s", (env["p"]["dan"],))["photo_url"] == PHOTO_DAN

    people = {x["name"]: x for x in c.get(f"/api/accounts/{oid}/people", headers=h("bdm")).json()["items"]}
    assert people["Ada Clarke"]["photo_url"] == PHOTO and people["Ben Weber"]["photo_url"] == PHOTO_BEN
    listed = {x["name"]: x for x in c.get("/api/people", headers=h("bdm")).json()["items"]}
    assert listed["Ada Clarke"]["photo_url"] == PHOTO
    page = c.get(f"/api/people/{ada}/profile", headers=h("bdm")).json()
    assert page["person"]["photo_url"] == PHOTO


def _research(conn, env, pid, pages, confident=True):
    rid = str(conn.execute("insert into person_profiles(person_id, organization_id, requested_by) values (%s,%s,'bdm@x') returning id", (pid, env["o"]["open"])).fetchone()["id"])
    conn.commit()
    reply = {"identity": {"confident": confident}, "current_role": {"title": "CTO", "remit": "Engineering", "source": "W1"}}
    profile_jobs.person_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(pages, []), call_factory=fake_model(reply))
    assert q1(conn, "select status from person_profiles where id=%s", (rid,))["status"] == "done"


def test_person_research_takes_the_picture_from_the_persons_own_linkedin_page_when_the_pages_are_about_them(conn, env):
    caps(conn)
    ada = env["p"]["ada"]
    pages = {"Ada Clarke Kestrel Pay Ltd Chief": [
        {"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay since 2023.", "image": PHOTO},
        {"url": "https://www.linkedin.com/in/ada-clarke-2", "title": "Ada Clarke - Nurse - Kestrel Pay | LinkedIn", "text": "Kestrel Pay nurse", "image": PHOTO_BEN}]}
    _research(conn, env, ada, pages)
    assert q1(conn, "select photo_url from people where id=%s", (ada,))["photo_url"] == PHOTO       # not the namesake's


def test_person_research_adds_no_picture_when_unsure_who_the_pages_are_about(conn, env):
    caps(conn)
    ada = env["p"]["ada"]
    pages = {"Ada Clarke Kestrel Pay Ltd Chief": [{"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn",
                                                   "text": "CTO at Kestrel Pay.", "image": PHOTO}]}
    _research(conn, env, ada, pages, confident=False)
    assert q1(conn, "select photo_url from people where id=%s", (ada,))["photo_url"] is None


def test_person_research_ignores_a_picture_from_a_page_that_is_not_their_recorded_profile(conn, env):
    caps(conn)
    ada = env["p"]["ada"]
    pages = {"Ada Clarke Kestrel Pay Ltd Chief": [{"url": "https://www.linkedin.com/in/ada-clarke-2", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn",
                                                   "text": "CTO at Kestrel Pay.", "image": PHOTO_BEN}]}
    _research(conn, env, ada, pages)
    assert q1(conn, "select photo_url from people where id=%s", (ada,))["photo_url"] is None


def test_each_search_records_whether_pictures_came_back_and_from_where(conn, env):
    """So a missing photo can be told apart from a search that returned no picture at all (hosts only, never names or addresses)."""
    caps(conn)
    ada = env["p"]["ada"]
    rid = str(conn.execute("insert into person_profiles(person_id, organization_id, requested_by) values (%s,%s,'bdm@x') returning id", (ada, env["o"]["open"])).fetchone()["id"])
    conn.commit()
    pages = {"Ada Clarke Kestrel Pay Ltd Chief": [{"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay.", "image": PHOTO}]}
    reply = {"identity": {"confident": True}, "current_role": {"title": "CTO", "remit": "Engineering", "source": "W1"}}
    profile_jobs.person_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(pages, []), call_factory=fake_model(reply))
    searched = q1(conn, "select content->'searched' as s from person_profiles where id=%s", (rid,))["s"]
    li = next(x for x in searched if x["purpose"] == "linkedin")
    assert li["images"] == 1 and li["image_hosts"] == ["media.licdn.com"]
    assert all("images" in x for x in searched)


# ------------------------------------------------------------------ why a picture was or was not kept (plain states, no names or addresses stored)
def test_picture_addresses_are_labelled_coarsely():
    from ri.research.exa_search import image_kind
    assert image_kind(PHOTO) == "profile-photo"
    assert image_kind("https://media.licdn.com/dms/image/v2/x/profile-framedphoto-shrink_200_200/0") == "profile-photo"
    assert image_kind("https://media.licdn.com/dms/image/v2/x/company-logo_200_200/0") == "company-logo"
    assert image_kind("https://media.licdn.com/dms/image/v2/x/profile-displaybackgroundimage-shrink_350_1400/0") == "banner"
    assert image_kind("https://static.licdn.com/aero-v1/sc/h/9c8pery4andzj6ohjkjp54ma2") == "linkedin-placeholder"
    assert image_kind("https://images.example.net/people/ada.jpg") == "other-site"
    assert image_kind("https://media.licdn.com/dms/image/v2/x/something-else/0") == "other"
    assert image_kind(None) == "other-site" or image_kind(None) == "other"


def _photo_state(conn, env, pages, confident=True):
    caps(conn)
    ada = env["p"]["ada"]
    rid = str(conn.execute("insert into person_profiles(person_id, organization_id, requested_by) values (%s,%s,'bdm@x') returning id", (ada, env["o"]["open"])).fetchone()["id"])
    conn.commit()
    reply = {"identity": {"confident": confident}, "current_role": {"title": "CTO", "remit": "Engineering", "source": "W1"}}
    profile_jobs.person_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(pages, []), call_factory=fake_model(reply))
    row = q1(conn, "select content->'photo_check' as c, content->'searched' as s from person_profiles where id=%s", (rid,))
    return row["c"], row["s"]


def _page(image=None, url="https://www.linkedin.com/in/ada-clarke"):
    r = {"url": url, "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay since 2023."}
    if image:
        r["image"] = image
    return {"Ada Clarke Kestrel Pay Ltd Chief": [r]}


def test_the_research_says_when_the_photo_was_found(conn, env):
    check, searched = _photo_state(conn, env, _page(PHOTO))
    assert check == {"state": "found", "kind": "profile-photo"}
    li = next(x for x in searched if x["purpose"] == "linkedin")
    assert li["image_kinds"] == ["profile-photo"]


def test_the_research_says_when_linkedin_only_shared_a_placeholder(conn, env):
    check, searched = _photo_state(conn, env, _page("https://static.licdn.com/aero-v1/sc/h/9c8pery4andzj6ohjkjp54ma2"))
    assert check == {"state": "not_a_photo", "kind": "linkedin-placeholder"}
    assert q1(conn, "select photo_url from people where id=%s", (env["p"]["ada"],))["photo_url"] is None
    assert next(x for x in searched if x["purpose"] == "linkedin")["image_kinds"] == ["linkedin-placeholder"]


def test_the_research_says_when_the_page_came_without_a_picture_or_was_not_found_or_identity_is_unsure(conn, env):
    assert _photo_state(conn, env, _page())[0] == {"state": "no_image"}
    assert _photo_state(conn, env, _page(PHOTO_BEN, url="https://www.linkedin.com/in/ada-clarke-2"))[0] == {"state": "page_not_found"}
    assert _photo_state(conn, env, _page(PHOTO), confident=False)[0] == {"state": "unsure_identity", "kind": "profile-photo"}
