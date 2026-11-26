"""Company logos found by Exa research on the company's own LinkedIn page: only real company logos, only from the page that is clearly
this company, not searched again within 60 days, never able to fail the research, and returned by the API so the app can show them instead of
initials. Exa and the model are FAKES (no network). All names are invented."""
from datetime import datetime, timedelta, timezone

import pytest

from ri.db import q1
from ri.enrichment.exa_logos import company_logo, domain_stem, logo_due, name_key, normalise_company_page, page_matches, pick_logo, set_logo
from ri.research import profile_jobs
from ri.research.company_profile import plan
from ri.research.exa_search import normalise_result
from ri.sources.base import AuthError
from test_r28_profiles import REPLY, _company_pages, caps, env, fake_model, fake_post  # noqa: F401  (env is a fixture)
from test_r29_buying_signals import world  # noqa: F401  (a fixture: target companies with signals, people and a lead list)

LOGO = "https://media.licdn.com/dms/image/v2/D4E0BAQabc/company-logo_200_200/B4EZxyz/0/1700000000000?e=1900000000&v=beta&t=abc"
LOGO_OLD = "https://media.licdn.com/dms/image/v2/C4D0BAQold/company-logo_100_100/0/1500000000000?e=1900000000&v=beta&t=old"
PLACEHOLDER = "https://static.licdn.com/aero-v1/sc/h/9c8pery4andzj6ohjkjp54ma2"
BANNER = "https://media.licdn.com/dms/image/v2/D4E3DAQban/profile-displaybackgroundimage-shrink_350_1400/B4EZ/0/1700000000000?e=1900000000&t=ban"
PERSON = "https://media.licdn.com/dms/image/v2/D4E03AQabc/profile-displayphoto-shrink_200_200/B4EZxyz/0/1700000000000?e=1900000000&v=beta&t=abc"
QUERY = "LinkedIn company page"        # what the logo search's wording contains: how the fake Exa tells it from the other searches
NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ what counts as a logo, a company page and a match
def test_only_real_company_logos_are_accepted():
    assert company_logo(LOGO) == LOGO and company_logo(LOGO_OLD) == LOGO_OLD
    assert company_logo("https://media-exp1.licdn.com/dms/image/C4D0BAQx/company-logo_200_200/0/1500000000000?e=1&t=2")      # another licdn host
    for bad in (None, "", 42, ["x"],
                LOGO.replace("https://", "http://"),                                          # not https
                LOGO.replace("https://", "HTTPS://"),                                          # the column only takes lower-case https://
                PLACEHOLDER,                                                                  # LinkedIn's grey placeholder
                "https://static.licdn.com/aero-v1/sc/h/company-logo-default",                 # even with the words, the placeholder host is not a logo
                BANNER,                                                                       # a banner
                "https://media.licdn.com/dms/image/v2/x/company-logo_banner_1128/0",         # a banner whose path also says company-logo
                PERSON,                                                                       # a person's photo
                "https://media.licdn.com/dms/image/v2/x/image-scale_191_1128/0",              # no company-logo in the path
                "https://example.com/company-logo.png",                                       # not LinkedIn's picture host
                "https://media.licdn.com.evil.example/dms/image/company-logo_200_200/0",     # a look-alike host
                "https://media.licdn.com/dms/image/v2/x/company-logo_200_200.svg", "https://media.licdn.com/dms/image/v2/x/company-logo.ico",
                "javascript:alert(1)", "data:image/png;base64,AAAA",
                "https://media.licdn.com/dms/image/v2/x/company-logo 200/0",                  # whitespace
                "https://media.licdn.com/dms/image/v2/x/company-logo_200_200/" + "a" * 700):  # absurdly long
        assert company_logo(bad) is None, bad


def test_only_linkedin_company_pages_count():
    assert normalise_company_page("https://www.linkedin.com/company/kestrel-pay") == "https://www.linkedin.com/company/kestrel-pay"
    assert normalise_company_page("https://uk.linkedin.com/company/Kestrel-Pay/about/?x=1") == "https://www.linkedin.com/company/kestrel-pay"
    assert normalise_company_page("http://linkedin.com/company/kestrel-pay#top") == "https://www.linkedin.com/company/kestrel-pay"
    for bad in (None, "", 7, "https://www.linkedin.com/in/ada-clarke", "https://www.linkedin.com/school/kestrel-university", "https://www.linkedin.com/showcase/kestrel-x",
                "https://www.linkedin.com/posts/ada-clarke_kestrel", "https://www.linkedin.com/company/", "https://linkedin.com.evil.example/company/kestrel-pay",
                "https://notlinkedin.com/company/kestrel-pay", "https://kestrel.example/company/kestrel-pay", "https://linkedin.com@evil.example/company/kestrel-pay"):
        assert normalise_company_page(bad) is None, bad


def test_the_page_must_be_this_company_by_its_whole_name_or_its_website_name():
    page = "https://www.linkedin.com/company/"
    assert name_key("The Harbour Bank (UK) Ltd") == "harbour bank" and name_key("Société Générale S.A.") == "societe generale" and name_key("AG") == ""
    assert domain_stem("www.kestrel.co.uk") == "kestrel" and domain_stem("ing.com") is None and domain_stem(None) is None      # a three-letter stem is too weak
    # the whole name, ignoring case, legal suffixes, parentheses, accents and "&"
    assert page_matches("Kestrel Pay | LinkedIn", page + "kestrel-pay", "Kestrel Pay Ltd", "kestrel.example") == "name"
    assert page_matches("KESTREL PAY: Instant payments for merchants | LinkedIn", page + "x", "Kestrel Pay (UK) Limited", None) == "name"
    assert page_matches("Kestrel Pay - Overview, Products | LinkedIn", page + "x", "Kestrel Pay", None) == "name"
    assert page_matches("Société Générale | LinkedIn", page + "sg", "Societe Generale SA", None) == "name"
    assert page_matches("Fjord & Co | LinkedIn", page + "x", "Fjord and Company", None) == "name"
    assert page_matches("Kestrel Pay Group | LinkedIn", page + "x", "Kestrel Pay", None) == "name"                 # the name plus only generic words
    assert page_matches("Monzo Bank | LinkedIn", page + "monzo", "Monzo", None) == "name"
    # never a partial or similar name
    for title in ("Kestrel Payments | LinkedIn", "Kestrel | LinkedIn", "Pay | LinkedIn", "Kestrel Pay Asset Management | LinkedIn", "Harbour Bank | LinkedIn",
                  "LinkedIn", "", None, "Kestrel Pay Foundation | LinkedIn", "Not Kestrel Pay | LinkedIn"):
        assert page_matches(title, page + "something-else", "Kestrel Pay Ltd", "kestrel.example") is None, title
    assert page_matches("Nordea Asset Management | LinkedIn", page + "x", "Nordea", None) is None
    # or the LinkedIn handle IS the website name, whatever the page is called
    assert page_matches("Kestrel Payments Hub | LinkedIn", page + "kestrel", "Kestrel Pay", "www.kestrel.example") == "domain"
    assert page_matches("Kestrel Payments Hub | LinkedIn", page + "kestrel-pay", "Kestrel Pay", "kestrel.example") is None   # a handle that only resembles it is not enough
    assert page_matches("Ing | LinkedIn", page + "ing", "Some Other Bank", "ing.com") is None                       # too short a website name to rely on


def test_what_the_logo_search_found_is_summed_up_in_one_plain_state():
    ok = {"url": "https://www.linkedin.com/company/kestrel-pay", "title": "Kestrel Pay | LinkedIn", "image": LOGO}
    assert pick_logo([ok], "Kestrel Pay Ltd", "kestrel.example") == (LOGO, {"state": "found", "kind": "company-logo"})
    assert pick_logo([], "Kestrel Pay Ltd", None) == (None, {"state": "no_linkedin_page"})
    assert pick_logo([{**ok, "url": "https://www.linkedin.com/in/ada-clarke"}], "Kestrel Pay Ltd", None) == (None, {"state": "no_linkedin_page"})
    assert pick_logo([{**ok, "title": "Harbour Bank | LinkedIn"}], "Kestrel Pay Ltd", None) == (None, {"state": "name_mismatch"})
    assert pick_logo([{**ok, "image": None}], "Kestrel Pay Ltd", None) == (None, {"state": "no_logo"})
    assert pick_logo([{**ok, "image": PLACEHOLDER}], "Kestrel Pay Ltd", None) == (None, {"state": "not_a_logo", "kind": "linkedin-placeholder"})
    assert pick_logo([{**ok, "image": BANNER}], "Kestrel Pay Ltd", None) == (None, {"state": "not_a_logo", "kind": "banner"})
    assert pick_logo([{**ok, "image": PERSON}], "Kestrel Pay Ltd", None) == (None, {"state": "not_a_logo", "kind": "profile-photo"})
    # the matching page is used even when a different company's page comes first, and a page without a usable picture does not stop the next one
    other = {"url": "https://www.linkedin.com/company/harbour-bank", "title": "Harbour Bank | LinkedIn", "image": LOGO_OLD}
    assert pick_logo([other, {**ok, "image": PLACEHOLDER}, ok], "Kestrel Pay Ltd", None)[0] == LOGO


def test_a_logo_is_looked_for_again_only_after_sixty_days():
    assert logo_due(None, NOW) is True
    assert logo_due(NOW - timedelta(days=59, hours=23), NOW) is False and logo_due(NOW - timedelta(days=61), NOW) is True
    assert logo_due(datetime(2026, 11, 1), NOW) is False          # a time without a zone is read as UTC


def test_exa_result_keeps_a_plain_https_image_for_the_company_page():
    r = normalise_result({"url": "https://www.linkedin.com/company/kestrel-pay", "title": "Kestrel Pay | LinkedIn", "text": "x", "image": LOGO}, "logo")
    assert r["image"] == LOGO and r["title"] == "Kestrel Pay | LinkedIn"


# ------------------------------------------------------------------ the search in the plan
REC = {"name": "Kestrel Pay Ltd", "website": "kestrel.example", "country": "GB"}


def test_the_plan_adds_one_small_linkedin_search_only_when_a_logo_is_due():
    def logo_search(rec):
        return [b for p, b in plan(rec, NOW) if p == "logo"]
    assert logo_search(REC) == [] and logo_search({**REC, "logo_due": False}) == []
    [body] = logo_search({**REC, "logo_due": True})
    assert body == {"query": "Kestrel Pay Ltd LinkedIn company page", "type": "auto", "numResults": 3, "includeDomains": ["linkedin.com"], "contents": {"text": {"maxCharacters": 200}}}
    assert "category" not in body and "startPublishedDate" not in body
    assert [p for p, _ in plan({**REC, "logo_due": True}, NOW)] == ["about", "news", "vendors", "hiring", "logo"]      # the other four are untouched, the logo search comes last
    assert [p for p, _ in plan(REC, NOW)] == ["about", "news", "vendors", "hiring"]


# ------------------------------------------------------------------ research stores (or refuses) the logo
def _logo_page(image=LOGO, url="https://www.linkedin.com/company/kestrel-pay", title="Kestrel Pay | LinkedIn"):
    r = {"url": url, "title": title, "text": "Kestrel Pay | 12,000 followers on LinkedIn."}
    if image:
        r["image"] = image
    return {QUERY: [r]}


def _research(conn, env, logo_pages=None, *, fail=None, exa=1.0):
    """Runs a company research on Kestrel Pay Ltd with fake Exa answers. Returns (requests sent, the profile row, the organisation row)."""
    caps(conn, exa=exa)
    oid = env["o"]["open"]
    rid = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    calls: list = []
    out = profile_jobs.account_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post({**_company_pages(), **(logo_pages or {})}, calls, fail=fail),
                                       call_factory=fake_model(REPLY))
    assert out["status"] == "done", out
    return calls, q1(conn, "select * from account_profiles where id=%s", (rid,)), q1(conn, "select logo_url, logo_checked_at from organizations where id=%s", (oid,))


def _is_logo_search(body):
    return body.get("includeDomains") == ["linkedin.com"]


def test_research_finds_stores_and_explains_the_logo(conn, env):
    calls, row, org = _research(conn, env, _logo_page())
    [sent] = [c for c in calls if _is_logo_search(c)]
    assert sent["query"] == "Kestrel Pay Ltd LinkedIn company page" and sent["numResults"] == 3 and "category" not in sent and sent["contents"] == {"text": {"maxCharacters": 200}}
    assert org["logo_url"] == LOGO and org["logo_checked_at"] is not None
    assert row["content"]["logo_check"] == {"state": "found", "kind": "company-logo"}
    searched = {s["purpose"]: s for s in row["content"]["searched"]}
    assert searched["logo"]["images"] == 1 and searched["logo"]["image_hosts"] == ["media.licdn.com"] and searched["logo"]["image_kinds"] == ["company-logo"]
    assert row["searches"] == 5
    # the LinkedIn page is only used for the logo: it is not one of the pages the profile cites, and it is not in the profile's facts
    assert all("linkedin.com" not in s["url"] for s in row["sources"]) and "logo_due" not in row["content"]["recorded"]
    assert "logo" not in {s["purpose"] for s in row["sources"]}


def test_a_placeholder_is_not_a_logo(conn, env):
    _, row, org = _research(conn, env, _logo_page(PLACEHOLDER))
    assert org["logo_url"] is None and org["logo_checked_at"] is not None                # not stored, but checked: not searched again for 60 days
    assert row["content"]["logo_check"] == {"state": "not_a_logo", "kind": "linkedin-placeholder"}
    assert next(s for s in row["content"]["searched"] if s["purpose"] == "logo")["image_kinds"] == ["linkedin-placeholder"]


def test_a_banner_is_not_a_logo(conn, env):
    _, row, org = _research(conn, env, _logo_page(BANNER))
    assert org["logo_url"] is None and org["logo_checked_at"] is not None
    assert row["content"]["logo_check"] == {"state": "not_a_logo", "kind": "banner"}


def test_a_page_of_another_company_never_lends_its_logo(conn, env):
    _, row, org = _research(conn, env, _logo_page(LOGO_OLD, url="https://www.linkedin.com/company/harbour-bank", title="Harbour Bank | LinkedIn"))
    assert org["logo_url"] is None and org["logo_checked_at"] is not None
    assert row["content"]["logo_check"] == {"state": "name_mismatch"}
    # a similar name is still another company
    conn.execute("update organizations set logo_checked_at=null where id=%s", (env["o"]["open"],))
    conn.commit()
    _, row2, org2 = _research(conn, env, _logo_page(LOGO_OLD, url="https://www.linkedin.com/company/kestrel-payments", title="Kestrel Payments | LinkedIn"))
    assert org2["logo_url"] is None and row2["content"]["logo_check"] == {"state": "name_mismatch"}


def test_a_persons_page_is_not_a_company_page(conn, env):
    _, row, org = _research(conn, env, _logo_page(PERSON, url="https://www.linkedin.com/in/ada-clarke", title="Ada Clarke - CTO - Kestrel Pay | LinkedIn"))
    assert org["logo_url"] is None and org["logo_checked_at"] is not None
    assert row["content"]["logo_check"] == {"state": "no_linkedin_page"}


def test_a_company_page_without_a_picture_and_no_page_at_all_are_told_apart(conn, env):
    _, row, org = _research(conn, env, _logo_page(None))
    assert row["content"]["logo_check"] == {"state": "no_logo"} and org["logo_url"] is None and org["logo_checked_at"] is not None
    conn.execute("update organizations set logo_checked_at=null where id=%s", (env["o"]["open"],))
    conn.commit()
    _, row2, org2 = _research(conn, env, {QUERY: []})
    assert row2["content"]["logo_check"] == {"state": "no_linkedin_page"} and org2["logo_checked_at"] is not None


def test_the_logo_is_not_searched_again_while_the_last_check_is_recent(conn, env):
    oid = env["o"]["open"]
    conn.execute("update organizations set logo_url=%s, logo_checked_at=now() - interval '59 days' where id=%s", (LOGO_OLD, oid))
    conn.commit()
    calls, row, org = _research(conn, env, _logo_page())
    assert not any(_is_logo_search(c) for c in calls) and len(calls) == 4 and row["searches"] == 4          # no extra Exa money
    assert org["logo_url"] == LOGO_OLD                                                                     # untouched
    assert "logo_check" not in row["content"] and all(s["purpose"] != "logo" for s in row["content"]["searched"])
    # a company with no logo whose last look found nothing is not searched again either
    conn.execute("update organizations set logo_url=null, logo_checked_at=now() - interval '5 days' where id=%s", (oid,))
    conn.commit()
    calls2, _, org2 = _research(conn, env, _logo_page())
    assert not any(_is_logo_search(c) for c in calls2) and org2["logo_url"] is None


def test_the_logo_is_searched_again_after_sixty_days_and_replaced(conn, env):
    oid = env["o"]["open"]
    conn.execute("update organizations set logo_url=%s, logo_checked_at=now() - interval '61 days' where id=%s", (LOGO_OLD, oid))
    conn.commit()
    calls, row, org = _research(conn, env, _logo_page())
    assert sum(1 for c in calls if _is_logo_search(c)) == 1
    assert org["logo_url"] == LOGO and org["logo_checked_at"] > datetime.now(timezone.utc) - timedelta(minutes=5)
    assert row["content"]["logo_check"]["state"] == "found"


def test_a_failed_search_for_the_logo_keeps_the_logo_on_file_and_nothing_found_leaves_it_alone(conn, env):
    oid = env["o"]["open"]
    conn.execute("update organizations set logo_url=%s, logo_checked_at=now() - interval '90 days' where id=%s", (LOGO_OLD, oid))
    conn.commit()
    _, row, org = _research(conn, env, _logo_page(PLACEHOLDER))
    assert org["logo_url"] == LOGO_OLD and org["logo_checked_at"] > datetime.now(timezone.utc) - timedelta(minutes=5)      # the old logo stays; only the check is noted
    assert row["content"]["logo_check"]["state"] == "not_a_logo"


def test_research_still_succeeds_when_the_logo_search_fails(conn, env):
    def refuse_logo(body):
        if _is_logo_search(body):
            raise AuthError("401 unauthorized", status=401)
    calls, row, org = _research(conn, env, _logo_page(), fail=refuse_logo)
    assert row["status"] == "done" and row["content"]["about"] and row["content"]["partial"] is False and row["content"]["notes"] == []     # the profile is whole
    assert org["logo_url"] is None and org["logo_checked_at"] is None                      # not marked as checked: it is tried again next time
    assert "logo_check" not in row["content"]
    assert next(s for s in row["content"]["searched"] if s["purpose"] == "logo") == {"purpose": "logo", "results": 0, "error": "auth"}
    assert float(q1(conn, "select reserved from provider_budgets where provider='exa'")["reserved"]) == 0     # the reservation was given back


def test_research_still_succeeds_when_the_logo_search_breaks_unexpectedly(conn, env):
    def boom(body):
        if _is_logo_search(body):
            raise RuntimeError("connection dropped")
    _, row, org = _research(conn, env, _logo_page(), fail=boom)
    assert row["status"] == "done" and row["content"]["about"] and org["logo_checked_at"] is None
    assert float(q1(conn, "select reserved from provider_budgets where provider='exa'")["reserved"]) == 0


def test_research_still_succeeds_when_the_budget_has_no_room_left_for_the_logo_search(conn, env):
    # room for the four research searches (0.012 each, 0.02 reserved while one runs) but not for a fifth
    calls, row, org = _research(conn, env, _logo_page(), exa=0.06)
    assert len(calls) == 4 and row["status"] == "done" and row["content"]["partial"] is False
    assert org["logo_url"] is None and org["logo_checked_at"] is None and "logo_check" not in row["content"]


def test_the_logo_is_stored_even_when_exa_found_no_pages_about_the_company(conn, env):
    pages = {k: [] for k in _company_pages()}
    caps(conn)
    oid = env["o"]["open"]
    rid = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    profile_jobs.account_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post({**pages, **_logo_page()}, []), call_factory=fake_model(REPLY))
    row = q1(conn, "select status, content from account_profiles where id=%s", (rid,))
    assert row["status"] == "done" and row["content"]["empty"] and row["content"]["logo_check"]["state"] == "found"
    assert q1(conn, "select logo_url from organizations where id=%s", (oid,))["logo_url"] == LOGO


def test_set_logo_checks_the_address_again_and_keeps_the_old_logo_when_given_none(conn, env):
    oid = env["o"]["open"]
    assert set_logo(conn, oid, PLACEHOLDER) is False and set_logo(conn, oid, "http://media.licdn.com/x/company-logo/0") is False
    row = q1(conn, "select logo_url, logo_checked_at from organizations where id=%s", (oid,))
    assert row["logo_url"] is None and row["logo_checked_at"] is not None
    assert set_logo(conn, oid, LOGO) is True and set_logo(conn, oid, None) is False
    assert q1(conn, "select logo_url from organizations where id=%s", (oid,))["logo_url"] == LOGO


# ------------------------------------------------------------------ the API returns the logo (additive: null when there is none)
def _set_logo(conn, oid, url=LOGO):
    conn.execute("update organizations set logo_url=%s, logo_checked_at=now() where id=%s", (url, oid))
    conn.commit()


def test_company_page_and_research_state_return_the_logo(conn, env):
    c, h, oid = env["c"], env["h"], env["o"]["open"]
    page = c.get(f"/api/accounts/{oid}", headers=h("bdm")).json()
    assert "logo_url" in page["account"] and page["account"]["logo_url"] is None and page["account"]["logo_checked_at"] is None and page["account"]["display_name"] == "Kestrel Pay Ltd"
    state = c.get(f"/api/accounts/{oid}/research", headers=h("bdm")).json()
    assert state["logo_url"] is None and state["logo_checked_at"] is None and state["profile"] is None
    _research(conn, env, _logo_page())
    page = c.get(f"/api/accounts/{oid}", headers=h("bdm")).json()
    assert page["account"]["logo_url"] == LOGO and page["account"]["logo_checked_at"]
    state = c.get(f"/api/accounts/{oid}/research", headers=h("bdm")).json()
    assert state["logo_url"] == LOGO and state["logo_checked_at"] and state["profile"]["content"]["logo_check"] == {"state": "found", "kind": "company-logo"}
    assert {"profile", "latest", "running", "setup", "can_run"} <= set(state)           # nothing that was there is gone


def test_people_rows_and_the_person_page_return_the_company_logo(conn, env):
    c, h, ada = env["c"], env["h"], env["p"]["ada"]
    _set_logo(conn, env["o"]["open"])
    listed = {x["name"]: x for x in c.get("/api/people", headers=h("bdm")).json()["items"]}
    assert listed["Ada Clarke"]["company"]["logo_url"] == LOGO
    page = c.get(f"/api/people/{ada}/profile", headers=h("bdm")).json()
    assert page["company"]["logo_url"] == LOGO and page["employment"][0]["company"]["logo_url"] == LOGO
    assert {"id", "name", "domain", "country", "segment"} <= set(page["employment"][0]["company"])
    # a company without a logo says so with null (the app then draws initials)
    conn.execute("update organizations set logo_url=null where id=%s", (env["o"]["open"],))
    conn.commit()
    assert c.get(f"/api/people/{ada}/profile", headers=h("bdm")).json()["company"]["logo_url"] is None


def test_search_results_return_the_company_logo(conn, env):
    c, h = env["c"], env["h"]
    _set_logo(conn, env["o"]["open"])
    groups = {g["key"]: g for g in c.get("/api/search?q=Kestrel", headers=h("bdm")).json()["groups"]}
    item = next(i for i in groups["accounts"]["items"] if i["title"] == "Kestrel Pay Ltd")
    assert item["logo_url"] == LOGO and item["link"].startswith("/accounts/")


def test_lists_signals_and_today_return_the_company_logo(conn, world):
    c, h, o = world["c"], world["h"], world["o"]
    _set_logo(conn, o["kestrel"])
    leads = c.get("/api/leads", headers=h("bdm")).json()["items"]
    by_name = {r["name"]: r for r in leads}
    assert by_name["Kestrel Pay"]["logo_url"] == LOGO and by_name["Fjord Bank"]["logo_url"] is None
    scoped = c.get("/api/leads?scope=target&by_region=false", headers=h("bdm")).json()["items"]
    assert all("logo_url" in r for r in scoped)
    sig = c.get("/api/buying-signals?days=90", headers=h("bdm")).json()
    orgs = {x["organization"]["name"]: x["organization"] for x in sig["companies"]}
    assert orgs["Kestrel Pay"]["logo_url"] == LOGO and orgs["Fjord Bank"]["logo_url"] is None
    assert all("logo_url" in x["organization"] for x in sig["companies"])
    if sig["pulse"]["top"]:
        assert "logo_url" in sig["pulse"]["top"]["organization"]
    brief = c.get("/api/today/brief", headers=h("bdm")).json()
    shown = [i["organization"] for i in [*brief["actions"], *brief["new"], *brief["next_up"]] if i.get("organization")]
    assert shown and all("logo_url" in x for x in shown)
    assert {x["logo_url"] for x in shown if x["name"] == "Kestrel Pay"} <= {LOGO}


def test_today_cards_carry_the_logo_through():
    from ri.api.today import _org
    o = {"id": "1", "name": "Kestrel Pay", "country": "GB", "logo_url": LOGO, "list_priority": "P1", "relationship": None, "people": 2, "people_with_email": 1}
    assert _org(o, True)["logo_url"] == LOGO and _org({k: v for k, v in o.items() if k != "logo_url"}, True)["logo_url"] is None


@pytest.mark.parametrize("bad", ["http://media.licdn.com/x/company-logo_1/0", "ftp://x/y", "https://" + "a" * 600])
def test_the_database_itself_refuses_addresses_that_are_not_plain_https(conn, env, bad):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("update organizations set logo_url=%s where id=%s", (bad, env["o"]["open"]))
    conn.rollback()
