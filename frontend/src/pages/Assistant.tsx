import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, getToken, renewSession } from "../api";
import { getLanguage, tr } from "../locale";
import { Err, Skeleton } from "../components/ui";
import { Markdown } from "../components/Markdown";
import "../styles/assistant.css";

type Step = { name: string; title: string; args?: Record<string, unknown> };
type Msg = { role: "user" | "assistant"; content: string; steps?: Step[]; error?: boolean; model?: string };
type Status = { ready: boolean; source: "personal" | "company" | null; model: string; personal_key: { saved: boolean; usable: boolean; hint?: string | null };
  company: { configured: boolean; tokens_left: number; cap: number }; tools: { name: string; title: string }[]; mcp_url: string };

const SUGGESTIONS = [
  "What should I do today, and why?",
  "Which of my accounts have the strongest buying signals this month?",
  "Summarise everything we know about our biggest open opportunity.",
  "Which open tenders fit us best right now?",
  "How does the revenue forecast look for the next three months?",
  "Where did we meet competitors in deals, and how did it go?",
];

const storeKey = (email: string) => `ri_assistant_${email}`;
function loadConvo(email: string): Msg[] {
  try { const v = JSON.parse(localStorage.getItem(storeKey(email)) || "[]"); return Array.isArray(v) ? v.slice(-40) : []; } catch { return []; }
}
function saveConvo(email: string, m: Msg[]) {
  try { localStorage.setItem(storeKey(email), JSON.stringify(m.slice(-40))); } catch { /* storage unavailable: this tab keeps it */ }
}

/** One question, answered as a stream of small events: what is being looked up, then the answer. */
async function ask(history: Msg[], onStep: (s: Step) => void): Promise<{ text: string; model?: string }> {
  await renewSession();
  const t = getToken();
  const res = await fetch("/api/assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    body: JSON.stringify({ lang: getLanguage(), messages: history.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content })) }),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : detail; } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", text = "", model: string | undefined;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.type === "tool") onStep({ name: ev.name, title: ev.title, args: ev.args });
      else if (ev.type === "answer") text = ev.text;
      else if (ev.type === "done") model = ev.model;
      else if (ev.type === "error") throw new ApiError(500, ev.message);
    }
    if (done) break;
  }
  return { text, model };
}

function argText(a?: Record<string, unknown>): string {
  if (!a) return "";
  const v = Object.values(a).filter((x) => typeof x === "string" || typeof x === "number").map(String);
  return v.length ? v.join(" · ") : "";
}

export default function Assistant({ email }: { email: string }) {
  const [st, setSt] = useState<Status | null>(null);
  const [stErr, setStErr] = useState<unknown>(null);
  const [msgs, setMsgs] = useState<Msg[]>(() => loadConvo(email));
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<Step[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { api<Status>("/api/assistant/status").then(setSt).catch(setStErr); }, []);
  useEffect(() => { saveConvo(email, msgs); }, [msgs, email]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [msgs.length, live.length, busy]);

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const history: Msg[] = [...msgs, { role: "user", content: question }];
    setMsgs(history); setDraft(""); setBusy(true); setLive([]);
    const steps: Step[] = [];
    try {
      const r = await ask(history, (s) => { steps.push(s); setLive([...steps]); });
      setMsgs([...history, { role: "assistant", content: r.text, steps, model: r.model }]);
    } catch (e) {
      const m = e instanceof ApiError && e.message ? e.message : tr("The assistant did not answer. Try again in a minute.");
      setMsgs([...history, { role: "assistant", content: m, steps, error: true }]);
    } finally {
      setBusy(false); setLive([]);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void send(draft); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(draft); }
  };

  const who = st?.source === "personal" ? tr("your own Anthropic key ({})", st.personal_key.hint ?? "") : st?.source === "company" ? tr("the company's AI allowance") : null;
  return <div className="as">
    <header className="page-head as-head">
      <div>
        <h1>{tr("Ask the assistant")}</h1>
        <p className="sub">{tr("Ask anything about your accounts, buying signals, people, tenders, the forecast or competitors. It reads the app as you, so it sees exactly what you can see, and it never changes anything.")}</p>
      </div>
      {msgs.length > 0 && <button type="button" className="as-new" onClick={() => { setMsgs([]); inputRef.current?.focus(); }} disabled={busy}>{tr("New conversation")}</button>}
    </header>

    {!st && (stErr ? <Err e={stErr} onRetry={() => { setStErr(null); api<Status>("/api/assistant/status").then(setSt).catch(setStErr); }} /> : <Skeleton rows={1} label={tr("Checking the assistant")} />)}
    {st && !st.ready && <p className="as-notice" role="note">
      {st.company.configured ? tr("This month's company AI allowance for the assistant is used up.") : tr("The assistant is not switched on for the company yet.")}{" "}
      {tr("You can save your own Anthropic key, or connect your own Claude or ChatGPT to the app instead.")} <Link to="/connections#ai">{tr("Set it up under My connections")}</Link>
    </p>}
    {st?.ready && <p className="as-meta">{tr("Answers are paid with {}.", who)} <Link to="/connections#ai">{tr("Prefer your own Claude or ChatGPT?")}</Link></p>}

    <section className="as-log" aria-label={tr("Conversation")} aria-live="polite" aria-busy={busy}>
      {msgs.length === 0 && <div className="as-empty">
        <h2>{tr("Try one of these")}</h2>
        <ul>{SUGGESTIONS.map((s) => <li key={s}><button type="button" onClick={() => void send(tr(s))} disabled={busy || !st?.ready}>{tr(s)}</button></li>)}</ul>
      </div>}
      {msgs.map((m, i) => <article key={i} className={`as-msg ${m.role}${m.error ? " error" : ""}`}>
        <span className="as-sr">{m.role === "user" ? tr("You asked") : tr("The assistant answered")}</span>
        {m.steps && m.steps.length > 0 && <details className="as-steps"><summary>{tr("Looked up {} things", m.steps.length)}</summary>
          <ol>{m.steps.map((s, j) => <li key={j}><b>{tr(s.title)}</b>{argText(s.args) && <span> {argText(s.args)}</span>}</li>)}</ol></details>}
        {m.role === "assistant" && !m.error ? <Markdown text={m.content} /> : <p className="m-0">{m.content}</p>}
      </article>)}
      {busy && <article className="as-msg assistant pending">
        <p className="as-thinking"><span className="as-dots" aria-hidden="true"><i /><i /><i /></span>{live.length ? tr("Looking up: {}", tr(live[live.length - 1].title)) : tr("Thinking")}</p>
        {live.length > 1 && <ol className="as-live">{live.slice(0, -1).map((s, j) => <li key={j}>{tr(s.title)}{argText(s.args) && <span> {argText(s.args)}</span>}</li>)}</ol>}
      </article>}
      <div ref={endRef} />
    </section>

    <form className="as-compose" onSubmit={submit}>
      <label htmlFor="as-q" className="as-sr">{tr("Your question")}</label>
      <textarea id="as-q" ref={inputRef} rows={2} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} maxLength={8000}
        placeholder={tr("Ask about an account, a person, a signal, a tender...")} disabled={!st?.ready} />
      <button type="submit" className="primary" disabled={busy || !draft.trim() || !st?.ready}>{busy ? tr("Answering") : tr("Ask")}</button>
    </form>
    <p className="as-fine">{tr("The assistant can be wrong. Check important facts on the linked pages. Your conversation is kept in this browser only.")}</p>
  </div>;
}
