"""The read-only tools an assistant uses to answer questions about the app's data. Shared by the MCP server (a person's own Claude or ChatGPT)
and the in-app assistant, so both see exactly the same things.

Every tool reads the app's OWN API, in process, as the person who is asking: the same endpoints the pages use, with the same permission
checks and the same row scope (a BDM's assistant sees a BDM's accounts, never more). Nothing here writes anything: the internal token only
opens GET requests (see api/auth.py). The tool list and each tool's parameters are taken from the routes themselves, so they stay in step
with the code: a new GET endpoint is reachable through `api_get` at once, with its parameters described from its own signature."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute

from ..access import Principal
from ..api.auth import issue_internal

# GET routes an assistant may not read through api_get: files rather than JSON, raw webhook payloads, the person's own signature HTML,
# and the assistant / sign-in plumbing itself.
DENY_EXACT = {"/api/config", "/api/leads.csv", "/api/me/connections/bluedot/events", "/api/me/email-signature", "/api/replies/config"}
DENY_PREFIX = ("/api/auth/", "/api/assistant", "/api/oauth", "/api/admin/kb")
MAX_RESULT_CHARS = 40_000            # about 10k tokens per tool result: enough for a page of records, small enough to keep a conversation going


@dataclass
class Endpoint:
    path: str                         # the route template, e.g. /api/accounts/{oid}
    summary: str
    perm: str | None
    params: list[dict[str, Any]]      # OpenAPI parameter objects (query and path)
    regex: re.Pattern[str]


def _perm_of(route: APIRoute) -> str | None:
    """The permission a route asks for through `require(perm)` (found in the dependency's closure); None when any signed-in person may call it."""
    stack = [route.dependant]
    while stack:
        d = stack.pop()
        call = d.call
        if call is not None and getattr(call, "__name__", "") == "dep" and getattr(call, "__closure__", None):
            for cell in call.__closure__ or ():
                v = cell.cell_contents
                if isinstance(v, str) and re.fullmatch(r"[a-z_]+\.[a-z_]+", v):
                    return v
        stack.extend(d.dependencies)
    return None


def _clean_schema(s: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI parameter schema -> a plain JSON schema a model reads easily. `^(|a|b)$` patterns become enums."""
    s = dict(s)
    if "anyOf" in s:                                   # Optional[...]: keep the non-null branch
        branches = [b for b in s.pop("anyOf") if b.get("type") != "null"]
        if branches:
            s = {**branches[0], **s}
    pat = s.get("pattern")
    if isinstance(pat, str):
        m = re.fullmatch(r"\^\(([\w|\-]*)\)\$", pat)
        if m:
            s.pop("pattern")
            s["enum"] = m.group(1).split("|")
    s.pop("title", None)
    return s


def catalogue(app: FastAPI) -> dict[str, Endpoint]:
    """Every readable JSON endpoint of the app, from its own routes and OpenAPI description. Cached on the app."""
    cached = getattr(app.state, "assistant_catalogue", None)
    if cached is not None:
        return cached
    spec = app.openapi()
    out: dict[str, Endpoint] = {}
    for r in app.routes:
        if not isinstance(r, APIRoute) or "GET" not in r.methods or not r.path.startswith("/api/") or not r.include_in_schema:
            continue
        if r.path in DENY_EXACT or r.path.startswith(DENY_PREFIX):
            continue
        op = (spec.get("paths", {}).get(r.path) or {}).get("get") or {}
        params = []
        for prm in op.get("parameters", []):
            if prm.get("in") not in {"query", "path"}:
                continue
            params.append({"name": prm["name"], "in": prm["in"], "required": bool(prm.get("required")), "schema": _clean_schema(prm.get("schema") or {})})
        doc = (r.endpoint.__doc__ or "").strip().split("\n")[0].strip()
        out[r.path] = Endpoint(r.path, doc or r.name.replace("_", " "), _perm_of(r), params, re.compile(r.path_regex.pattern))
    app.state.assistant_catalogue = out
    return out


def _cap(v: Any, items: int, chars: int) -> Any:
    if isinstance(v, dict):
        return {k: _cap(x, items, chars) for k, x in v.items() if x not in (None, "", [], {})}
    if isinstance(v, list):
        head = [_cap(x, items, chars) for x in v[:items]]
        return head + ([f"... {len(v) - items} more not shown: narrow the question (filters, q, limit) to see them"] if len(v) > items else [])
    if isinstance(v, str) and len(v) > chars:
        return v[:chars] + "..."
    return v


def compact(obj: Any, limit: int = MAX_RESULT_CHARS) -> str:
    """JSON text no longer than `limit`: lists and long texts are cut step by step (saying so), never silently."""
    dump = lambda o: json.dumps(o, ensure_ascii=False, default=str, separators=(",", ":"))  # noqa: E731
    text = dump(obj)
    if len(text) <= limit:
        return text
    for items, chars in ((60, 2000), (30, 1000), (15, 500), (8, 250), (4, 120)):
        text = dump(_cap(obj, items, chars))
        if len(text) <= limit:
            return text
    return text[:limit] + "...[cut: the answer was too long; ask for less]"


class ToolError(Exception):
    """A tool could not answer; the message is meant for the model (and the person) to read."""


@dataclass
class ToolContext:
    """One person asking, through one assistant (`via`: 'mcp:<client name>' or 'in-app')."""
    app: FastAPI
    principal: Principal
    via: str
    base_url: str
    calls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._token = issue_internal(self.app.state.internal_key, self.principal.email, self.via)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
            raise ToolError("path must be an /api/... endpoint without a query string (pass filters in params)")
        ep = next((e for e in catalogue(self.app).values() if e.regex.fullmatch(path)), None)
        if ep is None:
            raise ToolError(f"{path} is not a readable endpoint; call list_endpoints to see what is available")
        if ep.perm and not self.principal.can(ep.perm):
            raise ToolError(f"your role ({self.principal.role}) cannot read {ep.path} (it needs {ep.perm})")
        clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (params or {}).items() if v is not None and v != ""}
        self.calls.append(path)
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ri.internal", timeout=120) as c:
            r = await c.get(path, params=clean, headers={"Authorization": f"Bearer {self._token}"})
        if r.status_code == 404:
            raise ToolError("not found (or not visible to you)")
        if r.status_code in (401, 403):
            raise ToolError(_detail(r) or "not allowed")
        if r.status_code == 422:
            raise ToolError("the parameters were not accepted: " + (_detail(r) or r.text[:500]))
        if r.status_code >= 400:
            raise ToolError(f"the app answered {r.status_code}: {_detail(r) or 'error'}")
        if "json" not in r.headers.get("content-type", ""):
            raise ToolError("that endpoint does not return data an assistant can read")
        return r.json()

    def link(self, path: str) -> str:
        return self.base_url.rstrip("/") + path


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except Exception:  # noqa: BLE001
        return ""
    return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)[:500]


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    perm: str | None = None           # hidden from people without it (the endpoint would refuse anyway)

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description, "inputSchema": self.input_schema,
                "annotations": {"title": self.title, "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}


def _schema_for(ep: Endpoint, rename: dict[str, str] | None = None, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    rename = rename or {}
    props, req = {}, []
    for p in ep.params:
        if p["name"] in skip:
            continue
        name = rename.get(p["name"], p["name"])
        props[name] = p["schema"]
        if p["required"]:
            req.append(name)
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def _endpoint_tool(app: FastAPI, name: str, title: str, path: str, description: str, rename: dict[str, str] | None = None) -> Tool | None:
    ep = catalogue(app).get(path)
    if ep is None:
        return None
    rename = rename or {}
    back = {v: k for k, v in rename.items()}
    path_names = {p["name"] for p in ep.params if p["in"] == "path"}

    async def run(ctx: ToolContext, args: dict[str, Any]) -> Any:
        args = {back.get(k, k): v for k, v in args.items()}
        url = path
        for pn in path_names:
            v = str(args.pop(pn, "") or "")
            if not re.fullmatch(r"[\w\-.:]{1,120}", v):
                raise ToolError(f"{rename.get(pn, pn)} is missing or not a valid id")
            url = url.replace("{" + pn + "}", v)
        return await ctx.get(url, args)
    return Tool(name, title, description, _schema_for(ep, rename), run, ep.perm)


ACCOUNT_SECTIONS = ("research", "people", "relationship", "conversations", "tenders", "vendors", "solutions", "deadlines", "briefs", "campaigns",
                    "context", "contacts-plan", "endpoints")
PERSON_SECTIONS = ("profile", "brief", "conversations", "email-thread")

ID_ROUTES = (("/accounts/", "account"), ("/people/", "person"), ("/opportunities/", "opportunity"), ("/campaigns/", "campaign"), ("/knowledge/", "kb"))


def _id_from_link(link: str | None, url: str | None) -> str | None:
    for prefix, kind in ID_ROUTES:
        if link and link.startswith(prefix):
            rest = link[len(prefix):].split("?")[0].split("/")[0]
            if rest:
                return f"{kind}:{rest}"
    return None


def _title(rec: Any) -> str | None:
    """A record's name, wherever the endpoint keeps it (top level or one level down, e.g. account.name)."""
    if not isinstance(rec, dict):
        return None
    for obj in [rec, *[v for v in rec.values() if isinstance(v, dict)]]:
        for k in ("display_name", "full_name", "name", "title"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                return obj[k]
    return None


def build_tools(app: FastAPI) -> dict[str, Tool]:
    """The tool set, built once per app from its routes."""
    cached = getattr(app.state, "assistant_tools", None)
    if cached is not None:
        return cached
    cat = catalogue(app)
    tools: dict[str, Tool] = {}

    def add(t: Tool | None) -> None:
        if t is not None:
            tools[t.name] = t

    # ---- search and fetch: the shape ChatGPT's company knowledge and deep research expect, and handy for Claude too
    async def search(ctx: ToolContext, args: dict[str, Any]) -> Any:
        q = str(args.get("query") or "").strip()
        if len(q) < 2:
            raise ToolError("give a query of at least two characters")
        data = await ctx.get("/api/search", {"q": q[:120]})
        results = []
        for g in data.get("groups", []):
            for it in g.get("items", []):
                rid = _id_from_link(it.get("link"), it.get("url"))
                url = ctx.link(it["link"]) if it.get("link") else it.get("url")
                results.append({"id": rid or (it.get("url") or it.get("link") or it.get("title")), "title": it.get("title"), "url": url,
                                "kind": g.get("key"), "meta": it.get("meta") or None, "snippet": it.get("snippet") or None})
        return {"results": results, "total": len(results)}
    add(Tool("search", "Search the workspace",
             "Keyword search across everything in The Codest Revenue Intelligence app: accounts (and what the research sheets say about them), verified "
             "events, pages the platform has read, claims being verified, the 64 buying-signal rules, sources, and the knowledge base. Returns results "
             "with an id; pass an id like 'account:<uuid>' or 'person:<uuid>' to `fetch` for the full record. Every word must match (case and accents ignored).",
             {"type": "object", "properties": {"query": {"type": "string", "description": "One to five keywords, e.g. a company name, a technology, a country"}},
              "required": ["query"], "additionalProperties": False}, search))

    async def fetch(ctx: ToolContext, args: dict[str, Any]) -> Any:
        rid = str(args.get("id") or "").strip()
        kind, _, key = rid.partition(":")
        if not key or not re.fullmatch(r"[\w\-.]{1,120}", key):
            raise ToolError("id must look like 'account:<id>', 'person:<id>', 'opportunity:<id>' or 'campaign:<id>' (from search results)")
        routes = {"account": ("/api/accounts/{}", "/accounts/{}"), "person": ("/api/people/{}", "/people/{}"),
                  "opportunity": ("/api/hypotheses/{}", "/opportunities/{}"), "campaign": ("/api/signal-campaigns/{}", "/campaigns/{}")}
        if kind not in routes:
            raise ToolError(f"cannot fetch '{kind}' records; use search or api_get")
        api, page = (x.format(key) for x in routes[kind])
        rec = await ctx.get(api)
        title = _title(rec)
        return {"id": rid, "title": title or rid, "url": ctx.link(page), "text": compact(rec, MAX_RESULT_CHARS - 2000), "metadata": {"kind": kind}}
    add(Tool("fetch", "Open a record", "Open one record found with `search` by its id ('account:<id>', 'person:<id>', 'opportunity:<id>', 'campaign:<id>'). "
             "Returns the full record as the app shows it, with a link to its page.",
             {"type": "object", "properties": {"id": {"type": "string", "description": "An id from search results"}}, "required": ["id"], "additionalProperties": False},
             fetch))

    add(_endpoint_tool(app, "whoami", "Who am I", "/api/me", "The signed-in person: email, role and permissions. Use it to know what you can see."))
    add(_endpoint_tool(app, "today", "Today", "/api/today", "Today's worklist: who to contact today and why, the strongest fresh signals on the person's accounts."))
    add(_endpoint_tool(app, "today_brief", "Today's brief", "/api/today/brief", "A short written brief of what matters today for the signed-in person."))
    add(_endpoint_tool(app, "list_accounts", "Accounts", "/api/accounts", "Accounts (companies) the person can see, optionally filtered by a keyword. Each has an id for get_account."))

    acc = cat.get("/api/accounts/{oid}")
    if acc is not None:
        sections = [s for s in ACCOUNT_SECTIONS if f"/api/accounts/{{oid}}/{s}" in cat]

        async def get_account(ctx: ToolContext, args: dict[str, Any]) -> Any:
            oid = str(args.get("account_id") or "")
            if not re.fullmatch(r"[\w\-]{1,80}", oid):
                raise ToolError("account_id is missing or not valid (take it from list_accounts or search)")
            out: dict[str, Any] = {"account": await ctx.get(f"/api/accounts/{oid}"), "page": ctx.link(f"/accounts/{oid}")}
            for s in (args.get("include") or [])[:6]:
                if s in sections:
                    try:
                        out[s] = await ctx.get(f"/api/accounts/{oid}/{s}")
                    except ToolError as exc:
                        out[s] = {"unavailable": str(exc)}
            return out
        add(Tool("get_account", "Account", "One account in full: profile, signals, opportunities, ownership and tier, plus any of these sections on request: "
                 + ", ".join(sections) + ". Ask only for the sections you need (at most six).",
                 {"type": "object", "properties": {"account_id": {"type": "string"}, "include": {"type": "array", "items": {"type": "string", "enum": sections}}},
                  "required": ["account_id"], "additionalProperties": False}, get_account, acc.perm))

    add(_endpoint_tool(app, "buying_signals", "Buying signals", "/api/buying-signals", "Recent buying signals across accounts (hiring, funding, tenders, "
                       "leadership changes, regulation...), filterable by region, rule, family, keyword and age in days."))
    add(_endpoint_tool(app, "list_people", "People", "/api/people", "Contacts (people at accounts) with filters for keyword, source, email status, engagement and region."))

    per = cat.get("/api/people/{pid}")
    if per is not None:
        psections = [s for s in PERSON_SECTIONS if f"/api/people/{{pid}}/{s}" in cat]

        async def get_person(ctx: ToolContext, args: dict[str, Any]) -> Any:
            pid = str(args.get("person_id") or "")
            if not re.fullmatch(r"[\w\-]{1,80}", pid):
                raise ToolError("person_id is missing or not valid (take it from list_people or search)")
            out: dict[str, Any] = {"person": await ctx.get(f"/api/people/{pid}"), "page": ctx.link(f"/people/{pid}")}
            for s in (args.get("include") or [])[:4]:
                if s in psections:
                    try:
                        out[s] = await ctx.get(f"/api/people/{pid}/{s}")
                    except ToolError as exc:
                        out[s] = {"unavailable": str(exc)}
            return out
        add(Tool("get_person", "Person", "One contact in full, plus any of: " + ", ".join(psections) + ".",
                 {"type": "object", "properties": {"person_id": {"type": "string"}, "include": {"type": "array", "items": {"type": "string", "enum": psections}}},
                  "required": ["person_id"], "additionalProperties": False}, get_person, per.perm))

    add(_endpoint_tool(app, "get_opportunity", "Opportunity", "/api/hypotheses/{hid}", "One opportunity (hypothesis) with its evidence, state and next step.",
                       rename={"hid": "opportunity_id"}))
    add(_endpoint_tool(app, "open_tenders", "Open tenders", "/api/tenders", "Open public tenders that fit The Codest, filterable by region, buyer type, fit and work type."))
    add(_endpoint_tool(app, "findings", "All findings", "/api/feed", "The market feed: verified findings across all accounts, newest first, filterable by keyword and days."))
    add(_endpoint_tool(app, "renewals", "Contract renewals", "/api/renewals", "Public contracts coming up for renewal, with the incumbent supplier."))
    add(_endpoint_tool(app, "deadlines", "Deadlines", "/api/deadlines", "Upcoming deadlines on the person's accounts (tenders, regulation, follow-ups)."))
    add(_endpoint_tool(app, "forecast", "Revenue forecast", "/api/forecast", "The revenue forecast: pipeline by stage, win rates, expected revenue by month, "
                       "lead sources and data quality, from Pipedrive history."))
    add(_endpoint_tool(app, "competitors", "Competitors", "/api/competitors", "The competitor tracker: every competitor's profile, scorecard, recent moves and where we meet them."))
    add(_endpoint_tool(app, "competitor_deals", "Deals against competitors", "/api/competitors/deals", "Deals where we met a competitor, and how they went."))

    async def list_endpoints(ctx: ToolContext, args: dict[str, Any]) -> Any:
        word = str(args.get("filter") or "").lower()
        rows = []
        for ep in cat.values():
            if ep.perm and not ctx.principal.can(ep.perm):
                continue
            if word and word not in (ep.path + " " + ep.summary).lower():
                continue
            rows.append({"path": ep.path, "what": ep.summary,
                         "params": {p["name"]: {k: v for k, v in p["schema"].items() if k in ("type", "enum", "default", "minimum", "maximum")} | ({"required": True} if p["required"] else {})
                                    for p in ep.params}})
        return {"endpoints": rows, "count": len(rows), "how": "Call api_get with a path (fill {placeholders} with ids) and params."}
    add(Tool("list_endpoints", "List everything readable", "Every read-only endpoint of the app this person may call (accounts, people, campaigns, replies, "
             "calendar, tenders, renewals, forecast, competitors, library, administration views...), with what each returns and its parameters. "
             "Use it when no other tool fits, then call api_get.",
             {"type": "object", "properties": {"filter": {"type": "string", "description": "Optional word to narrow the list, e.g. 'campaign' or 'admin'"}},
              "additionalProperties": False}, list_endpoints))

    async def api_get(ctx: ToolContext, args: dict[str, Any]) -> Any:
        params = args.get("params") or {}
        if not isinstance(params, dict):
            raise ToolError("params must be an object")
        return await ctx.get(str(args.get("path") or ""), params)
    add(Tool("api_get", "Read any endpoint", "Read any endpoint from list_endpoints, e.g. path '/api/accounts/<id>/tenders' or '/api/calendar/events' with "
             "params {\"from\": \"2026-12-01\", \"to\": \"2026-12-31\"}. Read-only.",
             {"type": "object", "properties": {"path": {"type": "string"}, "params": {"type": "object", "additionalProperties": True}},
              "required": ["path"], "additionalProperties": False}, api_get))

    app.state.assistant_tools = tools
    return tools


def tools_for(app: FastAPI, p: Principal) -> list[Tool]:
    return [t for t in build_tools(app).values() if not t.perm or p.can(t.perm)]


async def call_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool. Returns (text for the model, is_error). Never raises for a tool-level problem."""
    t = build_tools(ctx.app).get(name)
    if t is None or (t.perm and not ctx.principal.can(t.perm)):
        return f"unknown tool {name}", True
    if not isinstance(args, dict):
        return "arguments must be an object", True
    try:
        out = await t.handler(ctx, args)
    except ToolError as exc:
        return str(exc), True
    return compact(out), False


INSTRUCTIONS = (
    "You are connected to The Codest Revenue Intelligence app (a software house's sales intelligence: accounts, buying signals, people, campaigns, "
    "tenders, renewals, the revenue forecast and competitors). Every tool is read-only and answers as the signed-in person, with their role and "
    "account visibility. Start with `search` for names and keywords, `today` for what to do now, or `list_endpoints` when unsure. Quote numbers and "
    "dates as the tools give them, say when something is not in the data, and link to the app page (the `page` or `url` fields) for the records you mention.")
