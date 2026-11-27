"""Worker jobs for Exa research: company profile, person profile, find decision makers. Interactive lane (a person is waiting).

Each job records its outcome on its own row (status, error, cost); a failure is written there in plain words, never silent, and never
retried automatically (it costs money). Exa and the model are both budget-gated; an exhausted budget ends the job cleanly.
"""
from __future__ import annotations

from typing import Any, Callable

from ..db import Conn, jb, q1
from ..sources.base import BudgetExceeded
from .exa_search import ExaSearcher, ExaStopped, configured, exa_post

BUDGET_MSG = {"exa": "The monthly Exa budget is used up (or not set), so no search was run.",
              "anthropic": "Not enough AI budget left this month to write the profile."}


def _settings():
    from ..config import Settings
    return Settings.from_env(require_auth=False)


def _caps(conn: Conn, st) -> None:
    """The worker sets caps when it starts; set them again so a research job early in a new month is not refused for want of a row."""
    from .. import budget, usage
    if st.exa_monthly_cap_usd > 0:
        budget.set_cap(conn, "exa", "monthly", usage.period_now(), st.exa_monthly_cap_usd, "usd")
    if st.anthropic_monthly_token_cap > 0:
        budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), st.anthropic_monthly_token_cap, "tokens")
    conn.commit()


def _fail(conn: Conn, table: str, rid: str, msg: str) -> None:
    conn.execute(f"UPDATE {table} SET status='failed', error=%s, finished_at=now() WHERE id=%s", (msg[:400], rid))      # noqa: S608 - fixed table names
    conn.commit()


def _budget_msg(exc: BudgetExceeded) -> str:
    return BUDGET_MSG["exa"] if str(exc).startswith("exa/") else BUDGET_MSG["anthropic"]


def _run_research(conn: Conn, job: dict[str, Any], *, table: str, kind: str, research: Callable[..., dict[str, Any]], subject_col: str,
                  schema: dict[str, Any] | None = None, post_factory=None, call_factory=None) -> dict[str, Any]:
    from ..ingest.pipeline import _run
    from ..jobs.modelcall import make_call
    from ..jobs.queue import JobError
    st = _settings()
    rid = job["payload"]["profile_id"]
    row = q1(conn, f"SELECT * FROM {table} WHERE id=%s", (rid,))       # noqa: S608
    if not row:
        raise JobError("research row not found", "not_found")
    setup = configured(st)
    if post_factory is None and not setup["ready"]:
        _fail(conn, table, rid, setup["note"])
        return {"status": "failed", "reason": "not_configured"}
    conn.execute(f"UPDATE {table} SET status='running' WHERE id=%s", (rid,))      # noqa: S608
    conn.commit()
    if post_factory is None:
        _caps(conn, st)
    org_id = str(row["organization_id"]) if row.get("organization_id") else None
    searcher = ExaSearcher(conn, post=(post_factory or (lambda: exa_post(st.exa_api_key)))(), operation=kind, ref_type=kind, ref_id=rid, org_id=org_id,
                           log_query=kind != "person_profile")
    call = (call_factory or (lambda: make_call(conn, st, org_id=org_id, operation=kind, key_prefix=f"{kind}:{rid}",
                                               models=[st.anthropic_research_model, st.anthropic_model], max_tokens=8000,
                                               schema=schema)))()
    try:
        r = research(conn, str(row[subject_col]), searcher=searcher, call=call, run=_run)
    except BudgetExceeded as exc:
        conn.rollback()
        _fail(conn, table, rid, _budget_msg(exc))
        _cost(conn, table, rid, searcher)
        return {"status": "failed", "reason": "budget"}
    except ExaStopped as exc:
        conn.rollback()
        _fail(conn, table, rid, str(exc))
        _cost(conn, table, rid, searcher)
        return {"status": "failed", "reason": exc.error_class}
    except Exception as exc:  # noqa: BLE001 - recorded on the row; the person sees it and can try again
        conn.rollback()
        _fail(conn, table, rid, f"The profile could not be written ({type(exc).__name__}: {exc})")
        _cost(conn, table, rid, searcher)
        return {"status": "failed", "reason": type(exc).__name__}
    conn.execute(f"UPDATE {table} SET status='done', content=%s, sources=%s, searches=%s, exa_cost_usd=%s, model=%s, tokens_in=%s, tokens_out=%s, dropped=%s, "      # noqa: S608
                 "finished_at=now() WHERE id=%s",
                 (jb(r["content"]), jb(r["sources"]), searcher.calls, round(searcher.cost, 4), r["model"], r["tokens_in"], r["tokens_out"], r["dropped"], rid))
    if table == "person_profiles" and r.get("photo_url"):
        from ..enrichment.exa_people import set_photo
        person = q1(conn, "SELECT linkedin_url FROM people WHERE id=%s", (row["person_id"],))
        set_photo(conn, str(row["person_id"]), person["linkedin_url"] if person else None, r["photo_url"])
    if table == "account_profiles" and org_id and r.get("logo_checked"):
        from ..enrichment.exa_logos import set_logo      # the logo search ran: keep the logo it found, and note the check so it is not repeated for 60 days
        set_logo(conn, org_id, r.get("logo_url"))
    conn.commit()
    return {"status": "done", "searches": searcher.calls, "cost_usd": round(searcher.cost, 4), "dropped": r["dropped"]}


def _cost(conn: Conn, table: str, rid: str, searcher: ExaSearcher) -> None:
    conn.execute(f"UPDATE {table} SET searches=%s, exa_cost_usd=%s WHERE id=%s", (searcher.calls, round(searcher.cost, 4), rid))      # noqa: S608
    conn.commit()


def account_profile(conn: Conn, job: dict[str, Any], **kw: Any) -> dict[str, Any]:
    from .company_profile import SCHEMA, research_company
    return _run_research(conn, job, table="account_profiles", kind="account_profile", research=research_company, subject_col="organization_id",
                         schema=SCHEMA, **kw)


def person_profile(conn: Conn, job: dict[str, Any], **kw: Any) -> dict[str, Any]:
    from .person_profile import SCHEMA, research_person
    return _run_research(conn, job, table="person_profiles", kind="person_profile", research=research_person, subject_col="person_id",
                         schema=SCHEMA, **kw)


def people_search(conn: Conn, job: dict[str, Any], *, post_factory=None, call_factory=None) -> dict[str, Any]:
    """LinkedIn through Exa first; when that gives fewer than three decision makers, the company's own pages and news are read too (one
    model call names the people they mention, each checked against its page). Without an AI key the LinkedIn part still runs."""
    from ..enrichment.exa_people import WEB_SCHEMA, find_people
    from ..ingest.pipeline import _run
    from ..jobs.modelcall import make_call
    from ..jobs.queue import JobError
    st = _settings()
    sid = job["payload"]["search_id"]
    row = q1(conn, "SELECT * FROM people_searches WHERE id=%s", (sid,))
    if not row:
        raise JobError("search not found", "not_found")
    if post_factory is None and not (st.exa_api_key and st.exa_monthly_cap_usd > 0):
        _fail(conn, "people_searches", sid, "Finding people with Exa is not set up in this environment. It needs EXA_API_KEY and RI_EXA_MONTHLY_CAP_USD on the worker.")
        return {"status": "failed", "reason": "not_configured"}
    conn.execute("UPDATE people_searches SET status='running' WHERE id=%s", (sid,))
    conn.commit()
    if post_factory is None:
        _caps(conn, st)
    searcher = ExaSearcher(conn, post=(post_factory or (lambda: exa_post(st.exa_api_key)))(), operation="people_search", ref_type="people_search", ref_id=sid,
                           org_id=str(row["organization_id"]))
    call = None
    if call_factory is not None:
        call = call_factory()
    elif post_factory is None and st.anthropic_api_key:
        call = make_call(conn, st, org_id=str(row["organization_id"]), operation="people_search", key_prefix=f"people_search:{sid}",
                         models=[st.anthropic_model], max_tokens=2500, schema=WEB_SCHEMA)
    try:
        r = find_people(conn, str(row["organization_id"]), sid, searcher=searcher, by=row["requested_by"], call=call, run=_run if call else None)
    except BudgetExceeded:
        conn.rollback()
        _fail(conn, "people_searches", sid, BUDGET_MSG["exa"])
        return {"status": "failed", "reason": "budget"}
    except ExaStopped as exc:
        conn.rollback()
        _fail(conn, "people_searches", sid, str(exc))
        return {"status": "failed", "reason": exc.error_class}
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _fail(conn, "people_searches", sid, f"The search failed ({type(exc).__name__}: {exc})")
        return {"status": "failed", "reason": type(exc).__name__}
    conn.execute("UPDATE people_searches SET status='done', searches=%s, exa_cost_usd=%s, found=%s, new_candidates=%s, already_known=%s, skipped=%s, error=%s, finished_at=now() WHERE id=%s",
                 (searcher.calls, round(searcher.cost, 4), r["found"], r["new"], r["already_known"], r["skipped"], r["stopped"], sid))
    conn.commit()
    return {"status": "done", **{k: v for k, v in r.items() if k != "stopped"}, "searches": searcher.calls, "log": searcher.log}


for _f in (account_profile, person_profile, people_search):
    _f.own_transactions = True  # type: ignore[attr-defined]       # budget reservations commit before each paid call: no wrapping transaction
