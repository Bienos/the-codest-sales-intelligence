"""The verified findings shared by Today, the archive and the account timeline."""
from __future__ import annotations

from uuid import UUID

from .access import Principal, org_scope_sql
from .db import Conn, qa
from .events.classify import non_engineering_title_sql_pattern
from .feed import STORY_KEY_SQL, fit_for
from .market import REGIONS
from .readmodel import evidence_for, story_sources
from .target_market import target_region, target_sql

# The current notice document and buyer must agree with the finding. Previous
# versions cannot leave an open alert after an amendment or cancellation.
_NOTICE = """
    SELECT pn.*
    FROM event_documents ed JOIN procurement_notices pn ON pn.document_id=ed.document_id
    WHERE ed.event_id=e.id AND pn.buyer_org_id=o.id AND pn.notice_id=e.attrs->>'notice_id'
    ORDER BY pn.last_status_check_at DESC NULLS LAST, pn.id
    LIMIT 1
"""
_OPEN = """n.status IN ('open','amended') AND n.deadline_at>now()
    AND n.last_status_check_at>=now()-interval '48 hours'
    AND n.official_url ~* '^https?://' AND e.attrs->'relevant_services'='true'::jsonb"""
_SIGNAL = """s.event_id=e.id AND s.organization_id=o.id AND s.status='active'
    AND s.verification_status='verified' AND (s.expires_at IS NULL OR s.expires_at>now())"""
_EVIDENCE = """EXISTS (SELECT 1 FROM event_claims ec JOIN claims cl ON cl.id=ec.claim_id
    LEFT JOIN source_documents doc ON doc.id=cl.source_document_id
    WHERE ec.event_id=e.id AND cl.status='supported'
    AND (cl.claim_kind='structured' OR (cl.claim_kind='quote' AND cl.anchor_verified))
    AND coalesce(doc.canonical_url,cl.object_json->>'official_url',cl.object_json->>'url') ~* '^https?://')"""


def findings(conn: Conn, p: Principal, *, scope: str, days: int, limit: int, page: int = 1, kind: str = "",
             region: str = "", organization_id: UUID | None = None, event: UUID | None = None, search: str = "", qualification: str = "target",
             rule: str = "") -> list[dict]:
    from .api.search import all_like, tokens

    where, args = org_scope_sql(p, "o.id")
    query = tokens(search)
    match, search_args = all_like("concat_ws(' ',o.display_name,e.summary,e.attrs->>'summary_en',e.attrs->>'summary_pl')", query) if query else ("TRUE", [])
    region_members = {key: sorted(members) for key, _, members in REGIONS if key != "us"}
    region_clause, region_args = "TRUE", []
    if region == "international":
        region_clause = "NOT (o.country_code=ANY(%s))"
        region_args = [sorted({country for members in region_members.values() for country in members})]
    elif region:
        region_clause, region_args = "o.country_code=ANY(%s)", [region_members[region]]
    target = target_sql()
    review = target_sql(review=True)
    eligible = review if qualification == "review" else f"({target} OR {review})" if qualification == "all" else target
    rows = qa(conn, f"""
        WITH candidates AS (
            SELECT e.id, e.event_type, e.summary, e.attrs, e.event_at, e.pub_at, e.first_observed_at,
                   coalesce(e.event_at,e.pub_at,e.first_observed_at) AS happened,
                   o.id AS org_id, o.display_name, o.country_code, {target} AS qualified,
                   CASE WHEN n.id IS NOT NULL THEN jsonb_build_array('tender',o.id,n.source_id,n.notice_id)::text
                        ELSE {STORY_KEY_SQL} END AS story_key,
                   CASE WHEN ({_OPEN}) IS TRUE THEN jsonb_build_object(
                        'deadline',n.deadline_at,'checked_at',n.last_status_check_at,
                        'url',n.official_url,'notice_id',n.notice_id,'status',n.status,'title',n.title,'scope',n.scope) END AS tender,
                   ARRAY(SELECT DISTINCT s.rule_id FROM signals s WHERE {_SIGNAL} ORDER BY s.rule_id) AS rules
            FROM events e JOIN organizations o ON o.id=e.subject_org_id
            LEFT JOIN LATERAL ({_NOTICE}) n ON e.event_type LIKE 'procurement_%%'
            WHERE ({where}) AND {eligible} AND ({region_clause}) AND ({match}) AND {_EVIDENCE}
            AND (%s::uuid IS NULL OR o.id=%s::uuid) AND e.verification_status='verified'
            AND e.lifecycle_status='active' AND NOT e.is_historical_replay
            AND (({target} AND e.event_type IN ('procurement_published','procurement_amended') AND ({_OPEN}))
                 OR (e.lane IS DISTINCT FROM 'public' AND e.event_type NOT LIKE 'procurement_%%'
                     AND e.event_type NOT IN ('crm_buyer_response','relationship_changed')
                     AND (e.event_type NOT IN ('engineering_job_opened','contractor_job_opened')
                          OR (coalesce(e.attrs->>'title','') !~* %s AND
                              (NOT (e.attrs ? 'job_id') OR EXISTS (SELECT 1 FROM job_postings j
                               WHERE j.provider=e.attrs->>'provider' AND j.board=e.attrs->>'board'
                               AND j.job_id=e.attrs->>'job_id' AND j.open_status='open'))))
                     AND coalesce(e.event_at,e.pub_at,e.first_observed_at)>now()-make_interval(days => %s)
                     AND coalesce(e.event_at,e.pub_at,e.first_observed_at)<=now()
                     AND (%s <> 'today' OR EXISTS (SELECT 1 FROM signals s WHERE {_SIGNAL} AND s.strength>0)
                          OR e.attrs->'fit'->>'level'='strong')))
        ), stories AS (
            SELECT DISTINCT ON (story_key) *, array_agg(id) OVER (PARTITION BY story_key) AS event_ids
            FROM candidates
            WHERE (%s='' OR (%s='tender')=(tender IS NOT NULL)) AND (%s='' OR %s=ANY(rules))
            ORDER BY story_key, happened DESC, id
        )
        SELECT * FROM stories WHERE (%s::uuid IS NULL OR %s::uuid=ANY(event_ids))
        ORDER BY CASE WHEN %s='today' THEN tender IS NULL ELSE false END,
                 CASE WHEN %s='today' THEN (tender->>'deadline')::timestamptz END NULLS LAST,
                 happened DESC, id
        LIMIT %s OFFSET %s
        """, [*args, *region_args, *search_args, organization_id, organization_id, non_engineering_title_sql_pattern(), days, scope, kind, kind,
              rule, rule, event, event, scope, scope, limit + 1, (page - 1) * limit])
    names = {x["id"]: x for x in qa(conn, "SELECT id, signal, strength, grp FROM legacy_signals WHERE id = ANY(%s)",
                                    (sorted({x for r in rows for x in (r["rules"] or [])}),))} if rows else {}
    from .outbound.signal_campaigns import ANGLES, DEFAULT_ANGLE
    out = []
    for r in rows:
        # Collect corroborating sources only from the events that passed the same
        # scope, buyer and evidence checks as the representative.
        claims = qa(conn, """SELECT DISTINCT ec.claim_id FROM event_claims ec JOIN claims cl ON cl.id=ec.claim_id
                            WHERE ec.event_id=ANY(%s::uuid[]) AND cl.status='supported'
                            AND (cl.claim_kind='structured' OR (cl.claim_kind='quote' AND cl.anchor_verified))""", (r["event_ids"],))
        evidence = [e for e in evidence_for(conn, [c["claim_id"] for c in claims], limit=6)
                    if isinstance(e["url"], str) and e["url"].lower().startswith(("https://", "http://"))]
        sources = story_sources(conn, r["event_ids"])
        if not evidence and not sources and not r["tender"]:
            continue
        action = qa(conn, """
            SELECT h.id AS hypothesis_id, a.action_type, a.due_at, (a.owner_id IS NOT NULL) AS has_owner,
                   u.display_name AS owner_name
            FROM recommended_actions a JOIN opportunity_hypotheses h ON h.id=a.hypothesis_id
            LEFT JOIN users u ON u.pipedrive_user_id=a.owner_id AND u.active
            WHERE h.organization_id=%s AND h.state NOT IN ('superseded','expired','disqualified')
            AND a.status IN ('accepted','proposed')
            AND EXISTS (SELECT 1 FROM initiative_events ie WHERE ie.initiative_id=h.initiative_id
                        AND ie.event_id=ANY(%s::uuid[]) AND ie.link_strength IN ('confirmed_link','probable_link')
                        AND ie.role IN ('evidence','capacity_evidence'))
            ORDER BY (a.status='accepted') DESC, h.score_total DESC, a.due_at NULLS LAST, a.id LIMIT 1
        """, (r["org_id"], r["event_ids"]))
        attrs = r["attrs"] or {}
        out.append({"id": r["id"], "event_ids": sorted(r["event_ids"]), "type": r["event_type"], "summary": r["summary"],
                    "happened": r["happened"], "published": r["pub_at"], "first_seen": r["first_observed_at"],
                    "organization": {"id": r["org_id"], "name": r["display_name"], "country": r["country_code"],
                                     "region": target_region(r["country_code"]), "qualified": r["qualified"]},
                    "tender": r["tender"], "evidence": evidence, "sources": sources,
                    "next_action": action[0] if action else None,
                    "fit": fit_for(r["rules"]), "why": attrs.get("fit"), "language": attrs.get("language"),
                    "signals": [{"rule_id": x, "name": names[x]["signal"], "strength": names[x]["strength"], "group": names[x]["grp"],
                                 "meaning": ANGLES.get(x, DEFAULT_ANGLE)} for x in (r["rules"] or []) if x in names and not x.startswith("NEG-")],
                    "summary_en": attrs.get("summary_en"), "summary_pl": attrs.get("summary_pl")})
    return out


def account_leads(conn: Conn, organization_id: UUID) -> list[dict]:
    from .api.intel import org_urls

    tracked = qa(conn, """
        SELECT v.id, v.url, v.origin, v.claimed_text, v.status, v.created_at, v.updated_at,
               v.result->>'reason' AS reason, linked.event_id
        FROM verification_requests v
        LEFT JOIN LATERAL (
            SELECT e.id AS event_id FROM event_documents ed JOIN events e ON e.id=ed.event_id
            WHERE ed.document_id=v.document_id AND e.subject_org_id=%s
            AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
            ORDER BY e.verified_at DESC NULLS LAST, e.id LIMIT 1
        ) linked ON v.status='verified'
        WHERE (v.organization_id=%s OR (v.organization_id IS NULL AND linked.event_id IS NOT NULL))
        AND v.url ~* '^https?://'
        ORDER BY v.updated_at DESC, v.id LIMIT 100
        """, (organization_id, organization_id))
    urls = {lead["url"] for lead in tracked}
    for url, item in org_urls(conn, organization_id).items():
        if url not in urls:
            tracked.append({"id": f"workbook:{url}", "url": url, "origin": "sheet_signal", "status": "not_checked",
                            "claimed_text": item["claimed_text"], "updated_at": None, "event_id": None})
    return tracked


def rfp_research(conn: Conn, p: Principal) -> list[dict]:
    """Private-RFP search hits stay in research until there is verified evidence."""
    scope, args = org_scope_sql(p, "v.organization_id")
    return qa(conn, f"""
        SELECT v.id, v.url, v.origin, v.claimed_text, v.status, v.updated_at
        FROM verification_requests v JOIN discovery_plans p ON v.plan_id=p.plan_id
        WHERE p.theme='private-rfp' AND (v.organization_id IS NULL OR ({scope})) AND v.origin='exa_discovery'
        AND v.status IN ('queued','fetching','extracting','waiting_budget','failed','blocked','no_event')
        AND v.created_at >= now() - interval '30 days' AND v.url ~* '^https?://'
        ORDER BY v.created_at DESC, v.id LIMIT 12
        """, args)          # the scope's own parameters: without them every seller's archive page failed
