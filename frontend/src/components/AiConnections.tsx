import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, fmtDateTime, put } from "../api";
import { tr } from "../locale";
import { Err, Skeleton } from "./ui";

/* My connections > Claude and ChatGPT: connect your own Claude or ChatGPT to the app (the MCP address and how), see and disconnect the ones you
   connected, and optionally save your own Anthropic key so the in-app assistant runs on it. */
type App = { id: string; name: string; where: { host: string; known_as: string | null; loopback: boolean }; connected_at: string; last_used_at: string | null };
type Apps = { mcp_url: string; items: App[] };
type Status = { ready: boolean; source: string | null; personal_key: { saved: boolean; usable: boolean; hint?: string | null }; vault_ready: boolean;
  company: { configured: boolean } };

function plain(e: unknown) {
  return e instanceof ApiError && e.status < 500 && e.message ? e.message : tr("The server did not respond. Try again in a minute.");
}

function Copy({ text, what }: { text: string; what: string }) {
  const [done, setDone] = useState(false);
  const t = useRef<number>();
  useEffect(() => () => window.clearTimeout(t.current), []);
  return <>
    <button type="button" className="cn-copy" aria-label={tr("Copy {}", what)} onClick={async () => {
      try { await navigator.clipboard.writeText(text); } catch { /* the field is selectable */ }
      setDone(true); window.clearTimeout(t.current); t.current = window.setTimeout(() => setDone(false), 2200);
    }}>{done ? tr("Copied") : tr("Copy")}</button>
    <span className="cn-sr" role="status">{done ? tr("Copied") : ""}</span>
  </>;
}

export function AiSection() {
  const [apps, setApps] = useState<Apps | null>(null);
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const load = () => Promise.all([api<Apps>("/api/me/ai-apps"), api<Status>("/api/assistant/status")]).then(([a, s]) => { setApps(a); setSt(s); setErr(null); }).catch(setErr);
  useEffect(() => { void load(); }, []);
  useEffect(() => { if (window.location.hash === "#ai") document.getElementById("ai")?.scrollIntoView(); }, [apps !== null]);

  const run = async (k: string, fn: () => Promise<string | void>) => {
    setBusy(k); setNote(null);
    try { const ok = await fn(); if (ok) setNote({ ok: true, text: ok }); await load(); } catch (e) { setNote({ ok: false, text: plain(e) }); } finally { setBusy(null); }
  };
  const saveKey = (e: FormEvent) => {
    e.preventDefault();
    void run("key", async () => { await put("/api/me/ai-key", { api_key: key.trim() }); setKey(""); return tr("Your key is saved. The assistant now answers with it."); });
  };

  return <section id="ai" className="cn-ai" aria-labelledby="cn-ai-h">
    <h2 id="cn-ai-h">{tr("Claude and ChatGPT")}</h2>
    <p className="cn-for">{tr("Use your own Claude or ChatGPT subscription to ask about everything in the app. It signs in as you, sees only what you can see, and can only read.")}</p>
    {!apps && (err ? <Err e={err} onRetry={() => void load()} /> : <Skeleton rows={2} label={tr("Loading your AI connections")} />)}
    {apps && st && <div className="cn-cards">
      <section className="cn-card" data-status={apps.items.length ? "connected" : "disconnected"} aria-labelledby="cn-mcp">
        <header className="cn-head">
          <span className="cn-glyph" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" className="cn-ico"><path d="M4 12h5l2-4 2 8 2-4h5" /></svg></span>
          <h2 id="cn-mcp">{tr("Your own assistant")}</h2>
          <span className={`badge ${apps.items.length ? "ok" : ""}`}>{apps.items.length ? tr("Connected") : tr("Not connected")}</span>
        </header>
        <div className="cn-secret">
          <label htmlFor="cn-mcp-url">{tr("The app's address for Claude and ChatGPT")}</label>
          <div className="cn-secret-row"><input id="cn-mcp-url" readOnly value={apps.mcp_url} onFocus={(e) => e.currentTarget.select()} /><Copy text={apps.mcp_url} what={tr("the address")} /></div>
        </div>
        <div className="cn-ai-how">
          <div><h3>Claude</h3><ol>
            <li>{tr("In Claude, open Settings > Connectors and choose Add custom connector.")}</li>
            <li>{tr("Name it The Codest and paste the address above. Leave the advanced settings empty.")}</li>
            <li>{tr("Choose Connect, sign in with your company Google account and allow read access.")}</li>
          </ol><p className="cn-hint">{tr("Works in claude.ai, the desktop and mobile apps. Team and Enterprise plans: an owner adds it once for everyone.")}</p></div>
          <div><h3>ChatGPT</h3><ol>
            <li>{tr("In ChatGPT, open Plugins (or Settings > Apps) and choose Add custom MCP server. On some plans, turn on Developer mode first.")}</li>
            <li>{tr("Paste the address above and choose OAuth as the authentication.")}</li>
            <li>{tr("Create it, sign in with your company Google account and allow read access. Then mention it with @ in a chat.")}</li>
          </ol></div>
          <div><h3>Claude Code</h3>
            <div className="cn-secret-row"><input readOnly aria-label={tr("Command for Claude Code")} value={`claude mcp add --transport http the-codest ${apps.mcp_url}`} onFocus={(e) => e.currentTarget.select()} />
              <Copy text={`claude mcp add --transport http the-codest ${apps.mcp_url}`} what={tr("the command")} /></div>
            <p className="cn-hint">{tr("Then type /mcp in Claude Code and sign in.")}</p></div>
        </div>
        {apps.items.length > 0 && <div className="cn-ai-list"><h3>{tr("Connected")}</h3><ul>
          {apps.items.map((a) => <li key={a.id}>
            <div><b>{a.where.known_as ?? a.name}</b> <span className="cn-meta">{a.where.loopback ? tr("on your computer") : a.where.host}</span>
              <p className="cn-meta m-0">{tr("Connected {}.", fmtDateTime(a.connected_at))} {a.last_used_at ? tr("Last used {}.", fmtDateTime(a.last_used_at)) : ""}</p></div>
            {asking === a.id ? <div className="cn-actions">
              <button type="button" className="primary" disabled={busy !== null} onClick={() => void run("dis", async () => { await api(`/api/me/ai-apps/${a.id}`, { method: "DELETE" }); setAsking(null); return tr("Disconnected. That assistant can no longer read anything."); })}>{tr("Disconnect")}</button>
              <button type="button" onClick={() => setAsking(null)}>{tr("Keep it connected")}</button></div>
              : <button type="button" className="cn-quiet" onClick={() => setAsking(a.id)}>{tr("Disconnect")}</button>}
          </li>)}
        </ul></div>}
      </section>

      <section className="cn-card" data-status={st.personal_key.usable ? "connected" : "disconnected"} aria-labelledby="cn-key">
        <header className="cn-head">
          <span className="cn-glyph" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" className="cn-ico"><circle cx="8" cy="14" r="4" /><path d="M11 11l9-9M16 6l3 3" /></svg></span>
          <h2 id="cn-key">{tr("Your own Anthropic key")}</h2>
          <span className={`badge ${st.personal_key.usable ? "ok" : ""}`}>{st.personal_key.usable ? tr("Saved") : tr("Optional")}</span>
        </header>
        <p className="cn-for">{tr("The assistant inside the app (Ask in the menu) uses the company's AI allowance. Save your own key to use it without limits on your own account.")} <Link to="/assistant">{tr("Open the assistant")}</Link></p>
        {!st.vault_ready ? <p className="cn-hint">{tr("Saving keys is switched off until an administrator sets RI_VAULT_KEY on the server.")}</p> : <>
          {st.personal_key.usable && <p className="cn-who"><i className="cn-dot ok" aria-hidden="true" />{tr("Saved key ending")} <b>{st.personal_key.hint}</b></p>}
          <form className="cn-field" onSubmit={saveKey}>
            <label htmlFor="cn-key-in">{st.personal_key.usable ? tr("Replace it") : tr("Anthropic API key")}</label>
            <div className="cn-secret-row"><input id="cn-key-in" type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-ant-..." />
              <button type="submit" className="primary" disabled={busy !== null || key.trim().length < 20}>{busy === "key" ? tr("Checking") : tr("Save key")}</button></div>
            <p className="cn-hint">{tr("Create one at console.anthropic.com > API keys. It is stored encrypted, only for you, and never shown again.")}</p>
          </form>
          {st.personal_key.saved && <button type="button" className="cn-quiet" disabled={busy !== null} onClick={() => void run("rm", async () => { await api("/api/me/ai-key", { method: "DELETE" }); return tr("Your key is removed."); })}>{tr("Remove my key")}</button>}
        </>}
      </section>
    </div>}
    {note && (note.ok ? <p className="okmsg" role="status">{note.text}</p> : <p className="err" role="alert">{note.text}</p>)}
  </section>;
}
