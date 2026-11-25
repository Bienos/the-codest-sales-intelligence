"""Open tenders, 25 Nov 2026: every open notice gets a plain English and Polish summary (what they buy, what they want, kind of work,
technologies, requirements, fit for The Codest), written in cheap budget-gated batches, our markets and soonest deadline first, capped per
run. The list searches, filters and sorts by it. No network: the model and TED are faked."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)

from ri import budget, usage
from ri import tender_summary as ts
from ri.sources.base import BudgetExceeded

NOW = datetime.now(timezone.utc)


def notice(conn, nid, title, *, days=20, country="DEU", source="procurement-ted", cpv=("72000000",), scope=None, value=None, published_days_ago=5,
           buyer="Beispiel Sparkasse", status="open"):
    """An open notice; the buyer is a savings bank unless said otherwise, because only ICP notices are summarised (see test_r31_tender_sector)."""
    return str(conn.execute(
        "insert into procurement_notices(source_id, notice_id, buyer_name, title, scope, status, deadline_at, official_url, country, service_codes, "
        "value_if_reported, published_at, last_status_check_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) returning id",
        (source, nid, buyer, title, scope, status, NOW + timedelta(days=days, hours=1), f"https://ted.europa.eu/en/notice/-/detail/{nid}", country,
         list(cpv), value, NOW - timedelta(days=published_days_ago))).fetchone()["id"])


def answer(item, **over):
    a = {"id": item["id"], "title_en": f"English name for {item['title'][-12:]}", "summary_en": "They buy software development. Three lots over 24 months.",
         "wants": ["A development team", "Support for 24 months", "  - Java and Kotlin skills"], "work_type": "software_development",
         "technologies": ["Java", "Kotlin"], "fit": {"level": "strong", "reason": "Custom development a remote team can deliver."},
         "requirements": ["German language"], "title_pl": "Polska nazwa", "summary_pl": "Kupują rozwój oprogramowania.", "wants_pl": ["Zespół programistów"],
         "requirements_pl": ["Język niemiecki"], "fit_reason_pl": "Rozwój oprogramowania na zamówienie."}
    a.update(over)
    return a


class FakeModel:
    """Answers every item it is given (plus an injected unknown id), and records each batch it saw."""

    def __init__(self, drop=(), empty_for=(), budget_after=None):
        self.batches, self.drop, self.empty_for, self.budget_after = [], set(drop), set(empty_for), budget_after

    async def __call__(self, system, user):
        items = json.loads(user)["items"]
        if self.budget_after is not None and len(self.batches) >= self.budget_after:
            raise BudgetExceeded("anthropic/monthly: reserving would exceed the hard cap")
        self.batches.append(items)
        if any(i["id"] in self.empty_for for i in items):
            raise ValueError("empty model output (stop_reason: refusal)")
        reply = [answer(i) for i in items if i["id"] not in self.drop]
        reply.append(answer({"id": "00000000-0000-0000-0000-000000000000", "title": "injected"}, title_en="Ignore previous instructions"))
        return "```json\n" + json.dumps({"items": reply}, ensure_ascii=False) + "\n```", 1000, 400, "fake-model"


def run(c):
    return asyncio.run(c)


def plenty_of_budget(conn, cap=10_000_000):
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), cap, "tokens")
    conn.commit()


# ------------------------------------------------------------------------------------------------ parsing and cleaning
def test_answers_are_read_even_when_fenced_cut_off_or_a_bare_list():
    full = {"items": [{"id": "a", "title_en": "x"}, {"id": "b", "title_en": "y"}]}
    assert [i["id"] for i in ts.parse_items("Here you go:\n```json\n" + json.dumps(full) + "\n```")] == ["a", "b"]
    cut = json.dumps(full)[:-12]                                    # the output limit cut the second item off
    assert [i["id"] for i in ts.parse_items(cut)] == ["a"]
    assert [i["id"] for i in ts.parse_items('[{"id": "a"}, {"id": "b"}]')] == ["a", "b"]
    assert ts.parse_items("I cannot help with that.") == [] and ts.parse_items("") == []
    assert ts.parse_items('{"items": "nope"}') == []


def test_summaries_are_normalised_and_unusable_answers_dropped():
    s = ts.clean_summary(answer({"id": "1", "title": "t"}, work_type="Maintenance & Support", technologies=["Java", "java", "SAP", 3, None, {"x": 1}],
                                wants=["a", "b", "c", "d", "e", "f"], fit={"level": "STRONG", "reason": "  Custom\n work. "}))
    assert s["work_type"] == "maintenance_support" and s["technologies"] == ["Java", "SAP", "3"] and len(s["wants"]) == 5
    assert s["fit"] == {"level": "strong", "reason": "Custom work.", "reason_pl": "Rozwój oprogramowania na zamówienie."}
    assert s["wants"][2] == "c" and ts.clean_summary(answer({"id": "1", "title": "t"}))["wants"][2] == "Java and Kotlin skills"     # bullets stripped
    assert ts.clean_summary(answer({"id": "1", "title": "t"}, work_type="painting"))["work_type"] == "other"
    assert "fit" not in ts.clean_summary(answer({"id": "1", "title": "t"}, fit={"level": "excellent"}))
    assert ts.clean_summary(answer({"id": "1", "title": "t"}, title_en="")) is None
    assert ts.clean_summary(answer({"id": "1", "title": "t"}, summary_en=None)) is None
    assert len(ts.clean_summary(answer({"id": "1", "title": "t"}, summary_en="x" * 5000))["summary_en"]) == 900
    assert "untrusted" in ts.SYSTEM and "Never invent" in ts.SYSTEM


def test_ted_extra_fields_are_kept_short_and_placeholders_dropped():
    # the shape TED v3 returns for these fields (checked against the live API on 25 Nov 2026)
    n = {"publication-number": "737260-2026", "title-proc": {"gle": "Córas ama", "eng": "Time and attendance system"},
         "description-proc": {"gle": "x", "eng": "Framework for a national time and attendance system."},
         "description-lot": {"eng": ["Implementation and support of the system for the retained fire service."]},
         "title-lot": {"eng": ["Lot 1 software", "Lot 2 support"]}, "estimated-value-proc": "1000000", "estimated-value-cur-proc": "EUR",
         "duration-period-value-lot": ["48", "48"], "duration-period-unit-lot": ["MONTH", "MONTH"], "contract-nature-main-proc": "services",
         "selection-criterion-description-lot": {"eng": ["Turnover of at least EUR 2m.", "Turnover of at least EUR 2m."]}}
    d = ts.ted_detail(n)
    assert d["title_original"] == "Córas ama" and d["description"].startswith("Framework for a national") and "retained fire service" in d["description"]
    assert d["lots"] == ["Lot 1 software", "Lot 2 support"] and d["lot_count"] == 2
    assert d["value"] == 1_000_000 and d["currency"] == "EUR" and d["duration"] == "48 month" and d["nature"] == "services"
    assert d["selection"] == "Turnover of at least EUR 2m."
    assert "value" not in ts.ted_detail({**n, "estimated-value-proc": "1"})                      # a placeholder, not a value
    assert asyncio.run(ts.fetch_ted_details(["1; DROP", ""])) == {}                                 # malformed numbers never reach the query


# ------------------------------------------------------------------------------------------------ the job
def test_batches_go_our_markets_first_then_soonest_deadline_capped_per_run(conn, env):
    plenty_of_budget(conn)
    ids = {
        "de_late": notice(conn, "1-2026", "Deutschland – IT-Dienste – Portal neu", days=40, country="DEU"),
        "lt_soon": notice(conn, "2-2026", "Lietuva – IT paslaugos – Sistemos kūrimas", days=5, country="LTU"),
        "pl_soon": notice(conn, "3-2026", "Polska – Usługi IT – System informacji", days=6, country="POL"),
        "uk_mid": notice(conn, "ocds-uk-1", "Digital service build", days=10, country="GB", source="procurement-uk-contracts-finder", cpv=()),
        "se_too_soon": notice(conn, "4-2026", "Sverige – IT-tjänster – Plattform", days=1, country="SWE"),
        "gr_mid": notice(conn, "5-2026", "Ελλάδα – IT – Σύστημα", days=8, country="GRC"),
        "not_software": notice(conn, "6-2026", "Deutschland – Bau – Schule", days=9, country="DEU", cpv=("45200000",)),
        "closed": notice(conn, "7-2026", "Deutschland – IT – Alt", days=-3, country="DEU"),
    }
    conn.commit()
    model = FakeModel()
    out = ts.summarise(conn, model, run, cap=5, batch=2)
    seen = [i["id"] for b in model.batches for i in b]
    # our markets first (still biddable before the one closing tomorrow), soonest deadline first; then the rest; capped at 5
    assert seen == [ids["pl_soon"], ids["uk_mid"], ids["de_late"], ids["se_too_soon"], ids["lt_soon"]]
    assert [len(b) for b in model.batches] == [2, 2, 1]
    assert out["summarised"] == 5 and out["batches"] == 3 and out["waiting"] == 1 and out["tokens"] == 3 * 1400 and out["model"] == "fake-model"
    row = conn.execute("select summary, summary_model, summary_basis, summary_skipped from procurement_notices where id=%s", (ids["pl_soon"],)).fetchone()
    assert row["summary"]["title_en"].startswith("English name for") and row["summary"]["fit"]["level"] == "strong"
    assert row["summary"]["title_pl"] == "Polska nazwa" and row["summary"]["wants_pl"] == ["Zespół programistów"]
    assert row["summary_model"] == "fake-model" and row["summary_basis"] and row["summary_skipped"] is None
    assert conn.execute("select count(*) n from procurement_notices where summary->>'title_en' like 'Ignore%%'").fetchone()["n"] == 0     # unknown id ignored
    assert [str(r["id"]) for r in ts.pending(conn)] == [ids["gr_mid"]]
    prompt = model.batches[0][0]
    assert prompt["country"] == "PL" and prompt["cpv"] == ["72000000"] and prompt["source"] == "EU tenders (TED)" and prompt["buyer"] == "Beispiel Sparkasse"
    assert prompt["title"] == "Polska – Usługi IT – System informacji" and prompt["deadline"] == (NOW + timedelta(days=6, hours=1)).date().isoformat()
    again = ts.summarise(conn, FakeModel(), run)                                   # the next run picks up only what is left
    assert again["summarised"] == 1 and again["waiting"] == 0
    assert ts.summarise(conn, FakeModel(), run)["batches"] == 0                    # nothing left: no call at all


def test_a_changed_title_makes_the_summary_due_again(conn, env):
    plenty_of_budget(conn)
    nid = notice(conn, "1-2026", "Deutschland – IT – Portal", days=20)
    conn.commit()
    ts.summarise(conn, FakeModel(), run)
    assert ts.pending(conn) == []
    conn.execute("update procurement_notices set title='Deutschland – IT – Portal und App' where id=%s", (nid,))
    assert [str(r["id"]) for r in ts.pending(conn)] == [nid]


def test_a_notice_the_model_will_not_answer_is_skipped_and_never_blocks_the_rest(conn, env):
    plenty_of_budget(conn)
    a = notice(conn, "1-2026", "Deutschland – IT – A", days=10)
    bad = notice(conn, "2-2026", "Deutschland – IT – B", days=11)
    c = notice(conn, "3-2026", "Deutschland – IT – C", days=12)
    conn.commit()
    model = FakeModel(empty_for={bad})
    out = ts.summarise(conn, model, run, batch=3)
    assert out["summarised"] == 2 and out["skipped"] == 1
    assert [len(b) for b in model.batches] == [3, 1, 1, 1]                         # the empty batch was asked again one notice at a time
    rows = {str(r["id"]): r for r in conn.execute("select id, summary, summary_skipped from procurement_notices")}
    assert rows[a]["summary"] and rows[c]["summary"]
    assert rows[bad]["summary"] is None and rows[bad]["summary_skipped"].startswith("empty model output")
    assert ts.pending(conn) == [] and ts.summarise(conn, FakeModel(), run)["batches"] == 0
    conn.execute("update procurement_notices set summary_at = now() - interval '8 days' where id=%s", (bad,))
    assert [str(r["id"]) for r in ts.pending(conn)] == [bad]                     # tried again, alone, after a week
    # a notice left out of the answer is asked again in a smaller batch, then skipped
    x = notice(conn, "5-2026", "Deutschland – IT – X", days=14)
    y = notice(conn, "6-2026", "Deutschland – IT – Y", days=15)
    dropped = notice(conn, "7-2026", "Deutschland – IT – Z", days=16)
    conn.execute("update procurement_notices set summary_at = now() where id=%s", (bad,))
    conn.commit()
    model = FakeModel(drop={dropped})
    out = ts.summarise(conn, model, run, batch=3)
    assert [len(b) for b in model.batches] == [3, 1] and out["summarised"] == 2 and out["skipped"] == 1
    skipped = conn.execute("select summary, summary_skipped from procurement_notices where id=%s", (dropped,)).fetchone()
    assert skipped["summary"] is None and "did not include" in skipped["summary_skipped"]
    assert {str(r["id"]) for r in conn.execute("select id from procurement_notices where summary is not null")} == {a, c, x, y}


def test_budget_short_or_used_up_stops_the_run_cleanly(conn, env):
    for i in range(5):
        notice(conn, f"{i}-2026", f"Deutschland – IT – Projekt {i}", days=10 + i)
    conn.commit()
    model = FakeModel()
    out = ts.summarise(conn, model, run, batch=2)                                 # no budget set this month: nothing runs, nothing is reserved
    assert model.batches == [] and out["summarised"] == 0 and "no model budget" in out["stopped"] and out["waiting"] == 5
    assert conn.execute("select count(*) n from budget_ledger").fetchone()["n"] == 0
    plenty_of_budget(conn, cap=16_000)                                             # a batch would eat into the 30% kept for sellers
    out = ts.summarise(conn, model, run, batch=2)
    assert model.batches == [] and "30%" in out["stopped"]
    plenty_of_budget(conn)
    model = FakeModel(budget_after=1)                                             # the hard cap is reached after the first batch
    out = ts.summarise(conn, model, run, batch=2)
    assert out["summarised"] == 2 and "used up" in out["stopped"] and out["waiting"] == 3
    assert conn.execute("select count(*) n from procurement_notices where summary is not null").fetchone()["n"] == 2     # the paid answers were kept


def test_ted_extra_text_is_read_once_per_batch_and_given_to_the_model(conn, env):
    plenty_of_budget(conn)
    a = notice(conn, "737260-2026", "Ireland – IT – Time and attendance", days=10, country="IRL")
    b = notice(conn, "ocds-1", "Digital service build", days=11, country="GB", source="procurement-uk-fts", cpv=(), scope="Build a citizen portal in React.")
    conn.commit()
    asked = []

    def fetch(numbers):
        asked.append(numbers)
        return {"737260-2026": {"from": "ted", "description": "National time and attendance system.", "value": 1_000_000.0, "currency": "EUR", "lot_count": 2,
                                "duration": "48 month"}, "999-2026": {"from": "ted", "description": "not asked for"}}
    model = FakeModel()
    out = ts.summarise(conn, model, run, fetch_ted=fetch)
    assert asked == [["737260-2026"]] and out["ted_details"] == 1
    sent = {i["id"]: i for i in model.batches[0]}
    assert sent[b]["description"] == "Build a citizen portal in React." and sent[a]["description"] == "National time and attendance system."
    assert sent[a]["value"] == 1_000_000.0 and sent[a]["currency"] == "EUR" and sent[a]["duration"] == "48 month"
    assert conn.execute("select source_detail from procurement_notices where id=%s", (a,)).fetchone()["source_detail"]["currency"] == "EUR"

    def broken(numbers):
        raise RuntimeError("TED is down")
    c = notice(conn, "737261-2026", "Ireland – IT – Payroll", days=12, country="IRL")
    conn.commit()
    out = ts.summarise(conn, FakeModel(), run, fetch_ted=broken)                  # the extra text is a bonus: the summary is still written
    assert out["summarised"] == 1 and out["ted_detail_errors"] == 1
    assert conn.execute("select summary is not null as ok from procurement_notices where id=%s", (c,)).fetchone()["ok"]


def test_the_job_reserves_and_settles_model_tokens_and_keeps_its_lease(conn, env, monkeypatch):
    from ri.jobs import queue
    from ri.jobs.handlers import build_handlers
    from ri.sources import http as http_mod
    plenty_of_budget(conn)
    notice(conn, "1-2026", "Deutschland – IT – Portal", days=10)
    conn.commit()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async def fake_ted(numbers):
        return {}

    async def fake_request_json(client, method, url, **kw):
        assert url == "https://api.anthropic.com/v1/messages" and kw["json"]["max_tokens"] == ts.MAX_TOKENS
        items = json.loads(kw["json"]["messages"][0]["content"])["items"]
        return {"content": [{"type": "text", "text": json.dumps({"items": [answer(i) for i in items]})}], "usage": {"input_tokens": 900, "output_tokens": 300}}
    monkeypatch.setattr(ts, "fetch_ted_details", fake_ted)
    monkeypatch.setattr(http_mod, "request_json", fake_request_json)
    queue.enqueue(conn, "tender_summaries", {"cap": 150}, idempotency_key="t1")
    conn.commit()
    assert queue.run_one(conn, "w", build_handlers(connector_factory=lambda s: None), ["tender_summaries"]) == "completed"
    job = conn.execute("select status, result, lease_expires_at from jobs where idempotency_key='t1'").fetchone()
    assert job["result"]["summarised"] == 1 and job["result"]["tokens"] == 1200
    b = conn.execute("select reserved, spent from provider_budgets where provider='anthropic'").fetchone()
    assert float(b["reserved"]) == 0 and float(b["spent"]) == 1200
    u = conn.execute("select operation, tokens_in, tokens_out from provider_usage").fetchone()
    assert (u["operation"], u["tokens_in"], u["tokens_out"]) == ("tender_summary", 900, 300)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    queue.enqueue(conn, "tender_summaries", {}, idempotency_key="t2")
    conn.commit()
    queue.run_one(conn, "w", build_handlers(connector_factory=lambda s: None), ["tender_summaries"])
    assert conn.execute("select result from jobs where idempotency_key='t2'").fetchone()["result"] == {"skipped": "AI is not configured (no API key)"}


def test_the_scheduler_queues_one_capped_run_every_two_hours_only_when_needed(conn, env):
    from ri.scheduler import run_maintenance
    t = datetime(2026, 11, 25, 10, 15, tzinfo=timezone.utc)
    assert ts.schedule(conn, t) == 0                                              # nothing waiting
    notice(conn, "1-2026", "Deutschland – IT – Portal", days=10)
    assert ts.schedule(conn, t) == 1 and ts.schedule(conn, t + timedelta(minutes=50)) == 0      # same two-hour window, and one is already waiting
    conn.execute("update jobs set status='completed' where kind='tender_summaries'")
    assert ts.schedule(conn, t + timedelta(minutes=110)) == 1                     # next window
    job = conn.execute("select payload, max_attempts from jobs where kind='tender_summaries' order by created_at limit 1").fetchone()
    assert job["payload"] == {"cap": ts.RUN_CAP} and job["max_attempts"] == 2
    conn.execute("update jobs set status='completed' where kind='tender_summaries'")
    assert run_maintenance(conn, t + timedelta(hours=4))["tender_summaries_queued"] == 1


# ------------------------------------------------------------------------------------------------ the list
def summarised(conn, nid, *, title_en, fit="strong", work_type="software_development", summary_en="They buy software.", title_pl="Nazwa", technologies=("Java",),
               detail=None):
    conn.execute("update procurement_notices set summary=%s, summary_at=now(), summary_basis=md5(concat_ws('|', title, scope)), source_detail=%s where id=%s",
                 (json.dumps({"title_en": title_en, "summary_en": summary_en, "wants": ["A team"], "work_type": work_type, "technologies": list(technologies),
                              "fit": {"level": fit, "reason": "Because."}, "title_pl": title_pl, "summary_pl": "Kupują oprogramowanie.", "secret": "not shown", "v": 2}),
                  json.dumps(detail) if detail else None, nid))


def test_the_list_shows_searches_filters_and_sorts_by_the_summaries(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        a = notice(conn, "1-2026", "Deutschland – IT-Dienste – Neuentwicklung Bürgerportal", days=30, published_days_ago=1)
        b = notice(conn, "2-2026", "Polska – Usługi IT – Utrzymanie systemu kadrowego", days=12, country="POL", published_days_ago=9)
        d = notice(conn, "ocds-1", "Data platform", days=20, country="GB", source="procurement-uk-fts", cpv=(), value=200_000, published_days_ago=3)
        e = notice(conn, "3-2026", "Sverige – IT – Licenser", days=50, country="SWE", published_days_ago=2)
        notice(conn, "4-2026", "Deutschland – IT – Noch offen", days=40, published_days_ago=4)                    # no summary yet
        summarised(conn, a, title_en="Citizen portal rebuild", detail={"from": "ted", "value": 900_000.0, "currency": "EUR", "lot_count": 3, "duration": "24 month"})
        summarised(conn, b, title_en="HR system maintenance", fit="possible", work_type="maintenance_support", title_pl="Utrzymanie systemu kadr", technologies=("SAP",))
        summarised(conn, d, title_en="Data platform build", fit="strong", work_type="data_ai")
        summarised(conn, e, title_en="Licence renewal", fit="none", work_type="licences_products")
    get = lambda qs="": c.get(f"/api/tenders?region=core&{qs}", headers=h("bdm")).json()      # noqa: E731
    out = get()
    assert [i["notice_id"] for i in out["items"]] == ["2-2026", "ocds-1", "1-2026", "4-2026", "3-2026"]          # soonest deadline first, as before
    first = out["items"][0]
    assert first["summary"]["title_en"] == "HR system maintenance" and first["summary"]["title_pl"] == "Utrzymanie systemu kadr"
    assert "secret" not in first["summary"] and first["summary_state"] == "written" and first["title"] == "Utrzymanie systemu kadrowego"
    waiting = next(i for i in out["items"] if i["notice_id"] == "4-2026")
    assert waiting["summary"] is None and waiting["summary_state"] == "waiting"
    portal = next(i for i in out["items"] if i["notice_id"] == "1-2026")
    assert (portal["value"], portal["currency"], portal["lots"], portal["duration"]) == (900_000.0, "EUR", 3, "24 month")
    assert next(i for i in out["items"] if i["notice_id"] == "ocds-1")["currency"] == "GBP"
    assert out["counts"]["summarised"] == 4 and out["counts"]["waiting_summary"] == 1 and out["summaries"] == {"waiting": 1, "running": False}
    assert out["facets"]["fit"] == {"possible": 1, "strong": 2, "waiting": 1, "none": 1}
    # search reads the English and Polish summaries, not only the original title
    assert [i["notice_id"] for i in get("q=citizen portal")["items"]] == ["1-2026"]
    assert [i["notice_id"] for i in get("q=kadr")["items"]] == ["2-2026"]
    assert [i["notice_id"] for i in get("q=SAP")["items"]] == ["2-2026"]
    assert [i["notice_id"] for i in get("q=Bürgerportal")["items"]] == ["1-2026"]
    # filters
    assert [i["notice_id"] for i in get("fit=strong")["items"]] == ["ocds-1", "1-2026"]
    assert [i["notice_id"] for i in get("fit=possible")["items"]] == ["2-2026", "ocds-1", "1-2026"]
    assert [i["notice_id"] for i in get("work_type=maintenance_support")["items"]] == ["2-2026"]
    assert get("work_type=maintenance_support")["facets"]["fit"] == {"possible": 1}
    assert [i["notice_id"] for i in get("max_days=25")["items"]] == ["2-2026", "ocds-1"]
    assert [i["notice_id"] for i in get("min_value=240000")["items"]] == ["1-2026"]           # GBP 200,000 is about EUR 234,000
    assert [i["notice_id"] for i in get("min_value=200000")["items"]] == ["ocds-1", "1-2026"]
    # sorting
    assert [i["notice_id"] for i in get("sort=fit")["items"]] == ["ocds-1", "1-2026", "2-2026", "3-2026", "4-2026"]
    assert [i["notice_id"] for i in get("sort=value")["items"]][:2] == ["1-2026", "ocds-1"]
    assert [i["notice_id"] for i in get("sort=newest")["items"]] == ["1-2026", "3-2026", "ocds-1", "4-2026", "2-2026"]
    for bad in ("fit=great", "work_type=painting", "sort=random", "max_days=-1", "min_value=-5"):
        assert c.get(f"/api/tenders?{bad}", headers=h("bdm")).status_code == 422
    # still compatible: no new parameter, every region, same counts as before
    assert c.get("/api/tenders", headers=h("bdm")).json()["counts"]["total"] == 5


def test_administrators_can_ask_for_summaries_now_once(world):
    c, h = world["c"], world["h"]
    assert c.post("/api/tenders/summarise", headers=h("admin")).json() == {"queued": False, "already_running": False, "job_id": None, "waiting": 0, "cap": ts.RUN_CAP}
    with world["app"].state.db.tx() as conn:
        notice(conn, "1-2026", "Deutschland – IT – Portal", days=10)
    assert c.post("/api/tenders/summarise", headers=h("bdm")).status_code == 403
    first = c.post("/api/tenders/summarise", headers=h("admin")).json()
    assert first["queued"] is True and first["waiting"] == 1 and first["job_id"]
    again = c.post("/api/tenders/summarise", headers=h("admin")).json()
    assert again["queued"] is False and again["already_running"] is True and again["job_id"] == first["job_id"]
    assert c.get("/api/tenders", headers=h("bdm")).json()["summaries"] == {"waiting": 1, "running": True}
    with world["app"].state.db.tx() as conn:
        assert conn.execute("select count(*) n from jobs where kind='tender_summaries'").fetchone()["n"] == 1
        assert conn.execute("select count(*) n from audit_events where operation='tender_summaries_requested' and actor='admin@x'").fetchone()["n"] == 3


@pytest.mark.parametrize("cap,expected", [(0, ts.RUN_CAP), (999, ts.RUN_CAP), (20, 20)])
def test_a_run_never_handles_more_than_the_cap(conn, cap, expected, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}
    monkeypatch.setattr(ts, "summarise", lambda conn, call, run, **kw: seen.update(kw) or {})
    ts.run_job(conn, {"id": "00000000-0000-0000-0000-000000000001", "payload": {"cap": cap}, "attempts": 1})
    assert seen["cap"] == expected
