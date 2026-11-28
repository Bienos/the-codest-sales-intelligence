"""The in-app assistant: Claude answers a person's question by calling the same read-only tools the MCP server offers, as that person.

Paid with the company key under its OWN monthly token cap (provider 'anthropic', kind 'assistant', RI_ASSISTANT_MONTHLY_TOKEN_CAP), so a busy
chat can never starve the page reading and research that share the main cap. A person who saved their own Anthropic key pays with it instead
(no company budget is touched). People who prefer their own Claude or ChatGPT subscription connect it through the MCP server instead.

The loop streams small events so the page can say what it is looking up: {"type": "tool"}, {"type": "answer"}, {"type": "done"}, {"type": "error"}."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from typing import Any

import httpx

from .. import budget, usage
from ..config import LIST_PRICES, Settings
from ..sources.base import BudgetExceeded
from . import tools as T

MAX_STEPS = 8                  # model calls per question (each may run several tools)
MAX_TOKENS = 3000
MAX_TURNS = 30
MAX_TURN_CHARS = 8000
API = "https://api.anthropic.com/v1/messages"

Poster = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]     # (api key, body) -> response JSON; replaced in tests


def company_model(st: Settings) -> str:
    return os.environ.get("RI_ASSISTANT_MODEL") or st.anthropic_model


def personal_model() -> str:
    return os.environ.get("RI_ASSISTANT_PERSONAL_MODEL") or "claude-sonnet-5-5"


def monthly_cap() -> float:
    return float(os.environ.get("RI_ASSISTANT_MONTHLY_TOKEN_CAP", "3000000") or 0)


async def default_post(key: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(API, json=body, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:300]
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        raise AssistantError(_explain(r.status_code, msg))
    return r.json()


class AssistantError(Exception):
    """Something a person can read."""


def _explain(status: int, msg: str) -> str:
    if status in (401, 403):
        return "The AI key was refused. If you saved your own key, check it under My connections."
    if status == 429:
        return "The AI service is busy or the key's rate limit was reached. Try again in a minute."
    if status == 529 or status >= 500:
        return "The AI service is overloaded right now. Try again in a minute."
    return f"The AI service refused the request: {msg}"


def system_prompt(ctx: T.ToolContext, lang: str) -> str:
    p = ctx.principal
    return (T.INSTRUCTIONS + "\n\n"
            f"Today is {date.today().isoformat()}. You are the assistant inside the app, talking to {p.email} (role: {p.role}). "
            "Answer in the person's language (" + ("Polish" if lang == "pl" else "English, unless they write in another language") + "). "
            "Be concise and concrete: lead with the answer, then the supporting facts. Use short Markdown (bold, bullet lists, small tables). "
            "When you mention an account, person, opportunity or campaign, link it with a relative link to its page, e.g. [Acme](/accounts/<id>). "
            "Never invent records, numbers or contacts: if the tools do not show it, say so and suggest where in the app to look. "
            "You can only read; if asked to change something, explain where in the app the person can do it themselves.")


def _clean_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages[-MAX_TURNS:]:
        role, text = m.get("role"), str(m.get("content") or "").strip()[:MAX_TURN_CHARS]
        if role not in ("user", "assistant") or not text:
            continue
        if out and out[-1]["role"] == role:                    # the API wants turns to alternate
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise AssistantError("Ask a question first.")
    return out


async def run(ctx: T.ToolContext, st: Settings, db: Any, messages: list[dict[str, Any]], *, api_key: str, personal: bool,
              lang: str = "en", post: Poster | None = None) -> AsyncIterator[dict[str, Any]]:
    post = post or default_post
    model = personal_model() if personal else company_model(st)
    tools = T.tools_for(ctx.app, ctx.principal)
    tool_defs: list[dict[str, Any]] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
    if tool_defs:
        tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}       # tools and system prompt are the same every call: cache them
    convo: list[dict[str, Any]] = _clean_history(messages)
    system = [{"type": "text", "text": system_prompt(ctx, lang), "cache_control": {"type": "ephemeral"}}]
    titles = {t.name: t.title for t in tools}
    tin_total = tout_total = 0
    period = usage.period_now()
    for step in range(MAX_STEPS + 1):
        body: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": convo, "tools": tool_defs}
        if step == MAX_STEPS:
            body["tool_choice"] = {"type": "none"}            # enough looking up: answer with what is known
        est = float(sum(len(str(m["content"])) for m in convo) // 3 + 6000 + MAX_TOKENS)
        key = f"assistant:{ctx.principal.user_id}:{os.urandom(6).hex()}"
        if not personal:
            with db.tx() as conn:
                budget.set_cap(conn, "anthropic", "assistant", period, monthly_cap(), "tokens")
                try:
                    budget.reserve(conn, "anthropic", "assistant", period, est, key=key, ref_type="assistant_chat", purpose="assistant_chat")
                except BudgetExceeded as exc:
                    raise AssistantError("This month's AI allowance for the assistant is used up. An administrator can raise it "
                                         "(RI_ASSISTANT_MONTHLY_TOKEN_CAP), you can save your own Anthropic key under My connections, "
                                         "or connect your own Claude or ChatGPT there.") from exc
        try:
            data = await post(api_key, body)
        except Exception:
            if not personal:
                with db.tx() as conn:
                    budget.release(conn, "anthropic", "assistant", period, est, key=key)
            raise
        u = data.get("usage") or {}
        tin = int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0)
        tout = int(u.get("output_tokens", 0))
        tin_total, tout_total = tin_total + tin, tout_total + tout
        pin, pout = LIST_PRICES.get(model, (None, None))
        cost, basis = usage.model_cost(tin, tout, pin, pout)
        with db.tx() as conn:
            if not personal:
                budget.settle(conn, "anthropic", "assistant", period, est, float(tin + tout), key=key)
            usage.record(conn, provider="anthropic", operation="assistant_chat", units=float(tin + tout), unit="tokens", tokens_in=tin, tokens_out=tout,
                         cost_usd=None if personal else cost, cost_basis="unmetered" if personal else basis,        # their own key: not the company's money
                         detail={"model": model, "personal_key": personal, "user_id": ctx.principal.user_id, "step": step})
        content = data.get("content") or []
        if data.get("stop_reason") == "tool_use" and step < MAX_STEPS:
            convo.append({"role": "assistant", "content": content})
            results = []
            for blk in content:
                if blk.get("type") != "tool_use":
                    continue
                name, args = blk.get("name", ""), blk.get("input") or {}
                yield {"type": "tool", "name": name, "title": titles.get(name, name), "args": _short_args(args)}
                text, is_error = await T.call_tool(ctx, name, args)
                results.append({"type": "tool_result", "tool_use_id": blk.get("id"), "content": text, "is_error": is_error})
            convo.append({"role": "user", "content": results})
            continue
        answer = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if data.get("stop_reason") == "refusal":
            answer = answer or "I can't help with that one."
        yield {"type": "answer", "text": answer or "I could not find an answer in the app's data."}
        yield {"type": "done", "model": model, "tokens": {"in": tin_total, "out": tout_total}, "reads": ctx.calls[:30], "personal_key": personal}
        return


def _short_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if not isinstance(v, str) or len(v) < 120 else v[:117] + "...") for k, v in list(args.items())[:6]} if isinstance(args, dict) else {}
