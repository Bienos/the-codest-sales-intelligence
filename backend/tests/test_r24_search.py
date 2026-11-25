"""Search across the workspace: accents and case ignored, short words matched whole, and every seller can open the findings archive."""
from ri.api.search import all_like, fold, tokens
from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)


def test_fold_matches_the_sql_function(conn):
    for word in ("Kraków", "Wisła", "Zürich", "Straße", "Ålesund", "Ørsted", "Łódź", "São"):
        assert conn.execute("select ri_fold(%s) f", (word,)).fetchone()["f"] == fold(word)
    assert fold("Straße") == "strasse" and fold("ŁÓDŹ") == "lodz"


def test_short_tokens_match_whole_words_long_tokens_match_inside(conn):
    def hits(text, q):
        where, args = all_like("%s::text", tokens(q))            # each condition reads the text, then its pattern
        params = [x for pattern in args for x in (text, pattern)]
        return conn.execute(f"select {where} AS ok", params).fetchone()["ok"]
    assert hits("Our new CTO starts in March", "cto") and not hits("The director of the sector", "cto")
    assert hits("Kraków Fintech Hub opens", "krakow fintech") and hits("Wisła Bank Cyfrowy", "WISLA")
    assert not hits("Kraków Fintech Hub", "krakow insurance")


def test_every_seller_can_open_the_findings_archive_and_search_without_accents(world):
    """The archive page used to fail for sellers: the private-RFP lead query dropped its scope parameters."""
    c, h = world["c"], world["h"]
    for who in ("bdm", "bdm2", "mgr", "analyst"):
        r = c.get("/api/findings?scope=archive&q=bank&qualification=all", headers=h(who))
        assert r.status_code == 200, (who, r.text)
    with world["app"].state.db.tx() as conn:
        conn.execute("update organizations set display_name='Bank Śląski Przykład' where id=%s", (world["env"]["bank"],))
    names = [i["display_name"] for i in c.get("/api/accounts?q=slaski", headers=h("bdm")).json()["items"]]
    assert names == ["Bank Śląski Przykład"]
    assert c.get("/api/accounts?q=slaski", headers=h("bdm2")).json()["items"] == []          # still row-scoped
    groups = {g["key"] for g in c.get("/api/search?q=slaski", headers=h("bdm")).json()["groups"]}
    assert "accounts" in groups
