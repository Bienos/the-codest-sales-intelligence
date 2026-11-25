"""Open tenders for software work, from every buyer: relevant ones only, soonest first, nothing expired, closing-too-soon counted but hidden."""
from datetime import datetime, timedelta, timezone

from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)

NOW = datetime.now(timezone.utc)


def notice(conn, nid, title, buyer, *, days, cpv, source="procurement-ted", status="open", country="DEU"):
    conn.execute("insert into procurement_notices(source_id, notice_id, buyer_name, title, status, deadline_at, official_url, country, service_codes, last_status_check_at) "
                 "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
                 (source, nid, buyer, title, status, NOW + timedelta(days=days, hours=1), f"https://ted.example/{nid}", country, cpv))


def test_tender_list_is_relevant_sorted_and_labelled_by_buyer(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        notice(conn, "A", "Germany – IT services: consulting, software development – Portal neu entwickeln", "Stadt Beispiel", days=20, cpv=["72000000"])
        notice(conn, "B", "Germany – IT services – Banking platform rebuild", "Beispiel Bank AG", days=8, cpv=["72200000"])
        notice(conn, "C", "Germany – Construction – Neubau Schule", "Stadt Beispiel", days=9, cpv=["45200000"])             # not software work
        notice(conn, "D", "Germany – IT services – Ohne Frist", "Stadt Beispiel", days=-2, cpv=["72000000"])                # already closed
        notice(conn, "E", "Germany – IT services – Zu knapp", "Stadt Beispiel", days=1, cpv=["72000000"])                   # closes too soon to bid
        notice(conn, "F", "Software engineering for a digital service", "Example Council", days=12, cpv=[], source="procurement-uk-contracts-finder", country="GB")
    items = c.get("/api/tenders", headers=h("bdm")).json()
    titles = [i["title"] for i in items["items"]]
    assert titles == ["Banking platform rebuild", "Software engineering for a digital service", "Portal neu entwickeln"]      # by deadline; the buyer's own title, not the country prefix
    assert items["counts"]["total"] == 3 and items["counts"]["finance"] == 1 and items["counts"]["closing_too_soon"] == 1
    assert items["items"][0]["buyer_kind"] == "finance" and items["items"][2]["buyer_kind"] == "public"
    assert all(i["url"].startswith("https://") for i in items["items"])
    assert [i["title"] for i in c.get("/api/tenders?buyer=finance", headers=h("bdm")).json()["items"]] == ["Banking platform rebuild"]
    assert [i["title"] for i in c.get("/api/tenders?region=uk", headers=h("bdm")).json()["items"]] == ["Software engineering for a digital service"]
    assert len(c.get("/api/tenders?min_days=0", headers=h("bdm")).json()["items"]) == 4                                      # the one closing tomorrow comes back when asked for
    assert [i["title"] for i in c.get("/api/tenders?q=portal", headers=h("bdm")).json()["items"]] == ["Portal neu entwickeln"]
    assert c.get("/api/tenders", headers=h("mkt")).status_code == 403                                                        # marketing has no access to market data
    assert c.get("/api/tenders").status_code == 401


def test_administrators_can_open_the_people_database(world):
    assert world["c"].get("/api/people", headers=world["h"]("admin")).status_code == 200
