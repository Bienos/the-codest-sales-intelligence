"""The in-app assistant's API: ask a question (streamed), see whether the assistant is ready, and save or remove one's own Anthropic key.

The answer streams as newline-delimited JSON events (see ri/assistant/chat.py) so the page can show what is being looked up. The person's key is
sealed with ri/vault.py (bound to them), never returned and never logged."""
from __future__ import annotations

import json
import re

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import usage, vault
from ..access import Principal
from ..assistant import chat
from ..assistant import tools as T
from ..audit import audit
from ..db import jb, q1
from .auth import principal_dep
from .oauth import issuer


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20000)


class AskIn(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=60)
    lang: str = Field("en", pattern="^(en|pl)$")


class KeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=300)


async def check_key(key: str) -> str | None:
    """None when Anthropic accepts the key; otherwise a sentence for the person. A network problem does not block saving."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    except httpx.HTTPError:
        return None
    if r.status_code in (401, 403):
        return "Anthropic did not accept this key. Copy it again from the Claude Console (console.anthropic.com > API keys)."
    return None


def _aad(p: Principal) -> str:
    return f"{p.user_id}:anthropic"


def personal_key(conn, st, p: Principal) -> str | None:
    row = q1(conn, "SELECT secret_enc FROM ai_personal_keys WHERE user_id=%s AND provider='anthropic'", (p.user_id,))
    if not row or not vault.available(st.vault_key):
        return None
    try:
        return vault.unseal(st.vault_key, row["secret_enc"], _aad(p))
    except vault.VaultError:
        return None


def register(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/assistant/status")
    def status(request: Request, p: Principal = Depends(principal_dep)):
        """Whether the assistant can answer, and who pays: the person's own key, the company allowance, or nobody yet."""
        st = request.app.state.settings
        with tx(request) as conn:
            row = q1(conn, "SELECT hint, created_at, last_used_at FROM ai_personal_keys WHERE user_id=%s AND provider='anthropic'", (p.user_id,))
            has_personal = bool(row) and personal_key(conn, st, p) is not None
            period = usage.period_now()
            cap = chat.monthly_cap()
            b = q1(conn, "SELECT cap, reserved, spent FROM provider_budgets WHERE provider='anthropic' AND kind='assistant' AND period=%s", (period,))
            left = max(0.0, cap - float(b["reserved"]) - float(b["spent"])) if b else cap
        company = bool(st.anthropic_api_key) and cap > 0
        source = "personal" if has_personal else "company" if company and left > 0 else None
        return {"ready": source is not None, "source": source, "model": chat.personal_model() if has_personal else chat.company_model(st),
                "personal_key": {"saved": bool(row), "usable": has_personal, "hint": row["hint"] if row else None, "saved_at": row["created_at"] if row else None},
                "company": {"configured": company, "tokens_left": left if company else 0, "cap": cap}, "vault_ready": vault.available(st.vault_key),
                "tools": [{"name": t.name, "title": t.title} for t in T.tools_for(request.app, p)], "mcp_url": issuer(request) + "/mcp"}

    @app.post("/api/assistant/chat")
    async def ask(body: AskIn, request: Request, p: Principal = Depends(principal_dep)):
        st = request.app.state.settings
        with tx(request) as conn:
            mine = personal_key(conn, st, p)
            if mine:
                conn.execute("UPDATE ai_personal_keys SET last_used_at=now() WHERE user_id=%s AND provider='anthropic'", (p.user_id,))
            conn.execute("INSERT INTO product_events(user_id, event, detail) VALUES (%s,'assistant_question',%s)",
                         (p.user_id, jb({"turns": len(body.messages), "chars": len(body.messages[-1].content), "personal_key": bool(mine)})))
        key = mine or st.anthropic_api_key
        if not key or (not mine and chat.monthly_cap() <= 0):
            raise HTTPException(503, "The assistant is not set up yet: an administrator adds the company AI key, or you save your own Anthropic key under My connections.")
        ctx = T.ToolContext(request.app, p, "in-app", issuer(request))
        post = getattr(request.app.state, "assistant_post", None)          # tests put a fake model here

        async def events():
            try:
                async for ev in chat.run(ctx, st, request.app.state.db, [m.model_dump() for m in body.messages], api_key=key, personal=bool(mine),
                                         lang=body.lang, post=post):
                    yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
            except chat.AssistantError as exc:
                yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"
            except Exception:  # noqa: BLE001 - never leak internals into the page
                import logging
                logging.getLogger("ri.assistant").exception("assistant failed")
                yield json.dumps({"type": "error", "message": "Something went wrong while answering. Try again."}) + "\n"
        return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.put("/api/me/ai-key")
    async def save_key(body: KeyIn, request: Request, p: Principal = Depends(principal_dep)):
        st = request.app.state.settings
        k = body.api_key.strip()
        if not re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{10,}", k):
            raise HTTPException(400, "That does not look like an Anthropic API key (it starts with sk-ant-).")
        if not vault.available(st.vault_key):
            raise HTTPException(503, "Personal keys are not switched on yet: the server has no key to keep credentials safe (an administrator sets RI_VAULT_KEY).")
        checker = getattr(request.app.state, "assistant_check_key", None) or check_key
        problem = await checker(k)
        if problem:
            raise HTTPException(400, problem)
        sealed = vault.seal(st.vault_key, k, _aad(p))
        with tx(request) as conn:
            conn.execute("INSERT INTO ai_personal_keys(user_id, provider, secret_enc, hint) VALUES (%s,'anthropic',%s,%s) "
                         "ON CONFLICT (user_id, provider) DO UPDATE SET secret_enc=EXCLUDED.secret_enc, hint=EXCLUDED.hint, created_at=now(), last_used_at=NULL",
                         (p.user_id, sealed, "..." + k[-4:]))
            audit(conn, actor=p.email, operation="ai_personal_key_saved", record_type="user", record_id=p.user_id)
        return {"ok": True, "hint": "..." + k[-4:]}

    @app.delete("/api/me/ai-key")
    def remove_key(request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            conn.execute("DELETE FROM ai_personal_keys WHERE user_id=%s AND provider='anthropic'", (p.user_id,))
            audit(conn, actor=p.email, operation="ai_personal_key_removed", record_type="user", record_id=p.user_id)
        return {"ok": True}

