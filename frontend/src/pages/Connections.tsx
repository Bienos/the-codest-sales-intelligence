import { FormEvent, ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, fmtDateTime, patch, post } from "../api";
import { counted, getLanguage, tr } from "../locale";
import { Err, Skeleton } from "../components/ui";
import { SignatureSection } from "../components/EmailSignature";
import "../styles/connections.css";

/* ------------------------------------------------------------------ icons (same drawing rules as the rest of the app) */
const L = (d: ReactNode) => ({ className = "" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={`cn-ico ${className}`}>{d}</svg>
);
const Ico = {
  mail: L(<><rect x="3" y="5" width="18" height="14" /><path d="M3 6l9 7 9-7" /></>),
  campaign: L(<><path d="M3 10v4h3l6 4V6L6 10z" /><path d="M16 9a4 4 0 010 6M18.5 6.5a7.5 7.5 0 010 11" /></>),
  meeting: L(<><rect x="3" y="6" width="12" height="12" /><path d="M15 10.5l6-3.5v10l-6-3.5" /></>),
  alert: L(<><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 17.5v.5" /></>),
  person: L(<><circle cx="12" cy="8" r="3.5" /><path d="M5 20c.6-3.6 3.2-5.5 7-5.5s6.4 1.9 7 5.5" /></>),
  lock: L(<><rect x="5" y="11" width="14" height="10" /><path d="M8 11V8a4 4 0 018 0v3" /></>),
  db: L(<><path d="M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3z" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></>),
};

/* ------------------------------------------------------------------ small helpers */
type Provider = "gmail" | "lemlist" | "bluedot";
type Conn = { provider: Provider; status: "connected" | "needs_attention" | "disconnected"; account_label?: string | null; connected_at?: string | null;
  last_sync_at?: string | null; last_error?: string | null; settings?: { share_email_gist?: boolean }; has_webhook?: boolean; stored?: number };
type Overview = { vault_ready: boolean; gmail_ready: boolean; items: Conn[] };
type Note = { kind: "ok" | "err"; text: string } | null;

/** The server's own plain sentence when it sent one (a wrong key, a missing setting); otherwise one calm line. Never raw JSON. */
function plainError(e: unknown): string {
  const m = String((e as Error)?.message ?? "").trim();
  if (e instanceof ApiError && e.status >= 400 && e.status < 500 && m && !/^[\[{]/.test(m)) return m;
  if (e instanceof ApiError && e.status >= 400 && e.status < 500) return tr("That did not work. Check what you entered and try again.");
  return tr("The server did not respond. Try again in a minute.");
}

/** One action at a time per card: which one is running, and the plain result of the last one. */
function useAct() {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setNote(null);
    try { const ok = await fn(); if (ok) setNote({ kind: "ok", text: ok }); } catch (e) { setNote({ kind: "err", text: plainError(e) }); } finally { setBusy(null); }
  };
  return { busy, note, setNote, run };
}
const Msg = ({ n }: { n: Note }) => n ? (n.kind === "ok" ? <p className="okmsg m-0" role="status">{n.text}</p> : <p className="err m-0" role="alert">{n.text}</p>) : null;

const UNITS: Record<Provider, (n: number) => string> = {
  gmail: (n) => counted(n, ["email", "emails"], ["e-mail", "e-maile", "e-maili"]),
  lemlist: (n) => counted(n, ["campaign event", "campaign events"], ["zdarzenie kampanii", "zdarzenia kampanii", "zdarzeń kampanii"]),
  bluedot: (n) => counted(n, ["meeting summary", "meeting summaries"], ["podsumowanie spotkania", "podsumowania spotkań", "podsumowań spotkań"]),
};

/** "12 Nov", or "12 Nov 2025" when it is not this year. */
function dayText(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}

/** Copies a line to the clipboard and says so (also to screen readers). */
function CopyButton({ text, what }: { text: string; what: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); } catch {
      const t = document.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } finally { t.remove(); }
    }
    setDone(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setDone(false), 2200);
  };
  return <>
    <button type="button" className="cn-copy" onClick={copy} aria-label={tr("Copy {}", what)}>{done ? tr("Copied") : tr("Copy")}</button>
    <span className="cn-sr" role="status">{done ? tr("Copied") : ""}</span>
  </>;
}

/* ------------------------------------------------------------------ the page */
export default function Connections() {
  const [d, setD] = useState<Overview | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [sp, setSp] = useSearchParams();
  // coming back from Google: say how it went once, then clear the address so a reload does not say it again
  const [banner] = useState<Note>(() => {
    const ok = sp.get("connected"), bad = sp.get("error");
    if (bad) return { kind: "err", text: tr("Gmail was not connected: {}", tr(bad)) };
    if (ok === "gmail") return { kind: "ok", text: tr("Gmail is connected. The first sync is running and can take a few minutes.") };
    return null;
  });
  useEffect(() => { if (sp.has("connected") || sp.has("error")) setSp({}, { replace: true }); }, []);
  const load = (): Promise<Overview | null> => api<Overview>("/api/me/connections").then((x) => { setD(x); setErr(null); return x; }).catch((e) => { setErr(e); return null; });
  useEffect(() => { void load(); }, []);

  // a connection with no sync yet: look again every few seconds for a while, so "waiting for the first sync" turns into a time
  const tries = useRef(0);
  useEffect(() => {
    if (!d || tries.current >= 20) return;
    if (!d.items.some((c) => c.provider !== "bluedot" && c.status === "connected" && !c.last_sync_at)) return;
    const t = window.setTimeout(() => { tries.current += 1; void load(); }, 6000);
    return () => window.clearTimeout(t);
  }, [d]);
  // "Sync now": look for the new sync time for a minute
  const timers = useRef<Partial<Record<Provider, number>>>({});
  useEffect(() => () => { Object.values(timers.current).forEach((t) => window.clearInterval(t)); }, []);
  const watchSync = (p: Provider, before?: string | null) => {
    window.clearInterval(timers.current[p]);
    let n = 0;
    timers.current[p] = window.setInterval(async () => {
      n += 1;
      const x = await load();
      const now = x?.items.find((c) => c.provider === p)?.last_sync_at;
      if ((now && now !== before) || n >= 12) window.clearInterval(timers.current[p]);
    }, 5000);
  };

  const get = (p: Provider): Conn => d?.items.find((c) => c.provider === p) ?? { provider: p, status: "disconnected", settings: { share_email_gist: false }, has_webhook: false, stored: 0 };
  const vault = d?.vault_ready ?? true;
  return <div className="cn">
    <header className="page-head">
      <h1>{tr("My connections")}</h1>
      <p className="sub">{tr("Connect your own Gmail, Lemlist and Bluedot. What they bring shows up as Conversations on each person's and company's page.")}</p>
    </header>
    <ul className="cn-points" aria-label={tr("How connections work")}>
      <li><Ico.person /><span><b>{tr("Personal.")}</b> {tr("Each person connects their own accounts. Only you can see or change yours.")}</span></li>
      <li><Ico.lock /><span><b>{tr("Private by default.")}</b> {tr("Only you see the subject and first line of your emails. The team sees that a contact happened, with whom and when.")}</span></li>
      <li><Ico.db /><span><b>{tr("The shared database stays the master.")}</b> {tr("Connections only add conversations to people we already hold.")}</span></li>
    </ul>
    <Msg n={banner} />
    {!d && (err ? <Err e={err} onRetry={() => { setErr(null); void load(); }} /> : <Skeleton rows={3} label={tr("Loading your connections")} />)}
    {d && <>
      {!d.vault_ready && <p className="cn-notice" role="note"><Ico.alert /><span>{tr("Connections are switched off until an administrator sets RI_VAULT_KEY on the server.")}</span></p>}
      {d.vault_ready && !d.gmail_ready && <p className="cn-notice" role="note"><Ico.alert /><span>{tr("Gmail needs an administrator to set RI_GOOGLE_CLIENT_ID and RI_GOOGLE_CLIENT_SECRET on the server first. Lemlist and Bluedot can be connected now.")}</span></p>}
      <div className="cn-cards">
        <GmailCard c={get("gmail")} vault={vault} ready={d.gmail_ready} reload={load} watch={watchSync} />
        <LemlistCard c={get("lemlist")} vault={vault} reload={load} watch={watchSync} />
        <BluedotCard c={get("bluedot")} vault={vault} reload={load} />
      </div>
      <SignatureSection />
    </>}
  </div>;
}

/* ------------------------------------------------------------------ one card per provider */
function Card({ id, name, Icon, purpose, c, children, keep }: { id: Provider; name: string; Icon: typeof Ico.mail; purpose: string; c: Conn; children: ReactNode; keep: ReactNode }) {
  const st = c.status;
  return <section className="cn-card" aria-labelledby={`cn-${id}`} data-status={st}>
    <header className="cn-head">
      <span className="cn-glyph" aria-hidden="true"><Icon /></span>
      <h2 id={`cn-${id}`}>{name}</h2>
      <span className={`badge ${st === "connected" ? "ok" : st === "needs_attention" ? "warn" : ""}`}>{st === "connected" ? tr("Connected") : st === "needs_attention" ? tr("Needs attention") : tr("Not connected")}</span>
    </header>
    <p className="cn-for">{purpose}</p>
    <div className="cn-body">
      <div className="cn-main">{children}</div>
      <div className="cn-keep">{keep}</div>
    </div>
  </section>;
}

/** The plain list of what is stored, and what never is. */
function Keep({ keeps, never }: { keeps: string[]; never: string }) {
  return <>
    <h3>{tr("What we keep")}</h3>
    <ul>{keeps.map((k, i) => <li key={i}>{k}</li>)}</ul>
    <p className="cn-never">{never}</p>
  </>;
}

/** Who it is connected as, when it last ran, and what is wrong when something is. */
function Status({ c, who }: { c: Conn; who: string }) {
  if (c.status === "disconnected") return null;
  const stored = c.stored ?? 0;
  return <div className="cn-status">
    {c.status === "needs_attention" && <div className="cn-attn" role="alert"><Ico.alert /><div>
      <b>{tr("Needs attention")}</b>
      <p>{c.last_error ? c.last_error : tr("The last sync did not work.")}</p>
      <p>{tr("Connect again to fix this.")}</p></div></div>}
    {c.account_label && <p className="cn-who"><i className={`cn-dot${c.status === "connected" ? " ok" : ""}`} aria-hidden="true" />{who} <b>{c.account_label}</b></p>}
    <p className="cn-meta">
      {c.last_sync_at ? tr("Last sync {}.", fmtDateTime(c.last_sync_at)) : tr("Waiting for the first sync. This can take a few minutes.")}
      {stored > 0 && <> {tr("Kept so far: {}.", UNITS[c.provider](stored))}</>}
    </p>
  </div>;
}

/** Disconnecting asks first. It only forgets the access: everything the connection already brought stays in the database. */
function Disconnect({ name, busy, onConfirm }: { name: string; c: Conn; busy: boolean; onConfirm: () => void }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className="cn-quiet" onClick={() => setAsking(true)}>{tr("Disconnect")}</button>;
  return <div className="cn-confirm" role="group" aria-label={tr("Disconnect {}", name)}>
    <p className="m-0"><b>{tr("Disconnect {}?", name)}</b> {tr("We forget your access straight away. Everything it already brought stays.")}</p>
    <div className="cn-actions">
      <button type="button" className="primary" disabled={busy} onClick={onConfirm}>{tr("Disconnect {}", name)}</button>
      <button type="button" disabled={busy} onClick={() => setAsking(false)} autoFocus>{tr("Keep it connected")}</button>
    </div>
  </div>;
}

type CardProps = { c: Conn; vault: boolean; reload: () => Promise<Overview | null> };

function GmailCard({ c, vault, ready, reload, watch }: CardProps & { ready: boolean; watch: (p: Provider, before?: string | null) => void }) {
  const { busy, note, run } = useAct();
  const [pending, setPending] = useState<boolean | null>(null);
  const on = pending ?? Boolean(c.settings?.share_email_gist);
  const live = c.status !== "disconnected";
  const off = !vault || !ready;
  const start = () => run("start", async () => {
    const r = await post<{ url?: string }>("/api/me/connections/gmail/start");
    if (!r?.url || !/^https:\/\//i.test(r.url)) throw new ApiError(500, "no address");
    window.location.href = r.url;
    await new Promise(() => undefined);                           // the page is leaving: keep the button busy
  });
  const sync = () => run("sync", async () => {
    const r = await post<{ queued?: boolean }>("/api/me/connections/gmail/sync");
    watch("gmail", c.last_sync_at);
    return r?.queued ? tr("Sync started. New emails appear within a few minutes.") : tr("A sync is already waiting. Nothing more to do.");
  });
  const share = (next: boolean) => { setPending(next); void run("share", async () => {
    try { await patch("/api/me/connections/gmail/settings", { share_email_gist: next }); await reload(); } finally { setPending(null); }
    return next ? tr("The team now sees the subject and first line of your emails.") : tr("Only you see the subject and first line of your emails again.");
  }); };
  const disconnect = () => run("disconnect", async () => {
    await post("/api/me/connections/gmail/disconnect", {});
    await reload();
    return tr("Gmail is disconnected. We forgot your access. What it brought is kept.");
  });
  return <Card id="gmail" name="Gmail" Icon={Ico.mail} c={c} purpose={tr("Shows your emails with people in our database on their pages.")}
    keep={<Keep keeps={[tr("The subject and Gmail's own short snippet of each email."), tr("Whether it was sent or received, and the date."), tr("Only for people who are already in our database.")]}
      never={tr("We never keep full emails or attachments, and nothing is sent to an AI. Access is read-only: we cannot send, change or delete your mail.")} />}>
    <Status c={c} who={tr("Connected as")} />
    <div className="cn-actions">
      {c.status !== "connected" && <button type="button" className="primary" disabled={off || busy !== null} onClick={start}>{busy === "start" ? tr("Opening Google…") : c.status === "needs_attention" ? tr("Connect again with Google") : tr("Connect with Google")}</button>}
      {live && <button type="button" disabled={off || busy !== null} onClick={sync}>{busy === "sync" ? tr("Starting…") : tr("Sync now")}</button>}
    </div>
    {off && <p className="cn-hint m-0">{!vault ? tr("Not available yet: an administrator has to switch connections on first.") : tr("Not available yet: an administrator has to finish the Google setup first.")}</p>}
    {live && <label className="cn-switch">
      <input type="checkbox" role="switch" checked={on} disabled={busy !== null} onChange={(e) => share(e.target.checked)} />
      <span><b>{tr("Share the gist of my emails with the team")}</b>
        <small>{on ? tr("On: the team also sees the subject and first line of your emails with people in our database.") : tr("Off: the team sees that a contact happened, with whom and when, but not the subject or first line.")}</small></span>
    </label>}
    <Msg n={note} />
    {live && <Disconnect name="Gmail" c={c} busy={busy !== null} onConfirm={disconnect} />}
  </Card>;
}

function LemlistCard({ c, vault, reload, watch }: CardProps & { watch: (p: Provider, before?: string | null) => void }) {
  const { busy, note, run } = useAct();
  const [key, setKey] = useState("");
  const id = useId();
  const live = c.status !== "disconnected";
  const connect = (e: FormEvent) => { e.preventDefault(); if (!key.trim()) return; void run("connect", async () => {
    await post("/api/me/connections/lemlist", { api_key: key.trim() });
    setKey("");                                                   // the key is not kept on this page once the server has it
    await reload();
    return tr("Lemlist is connected. The first sync starts shortly.");
  }); };
  const sync = () => run("sync", async () => {
    const r = await post<{ queued?: boolean }>("/api/me/connections/lemlist/sync");
    watch("lemlist", c.last_sync_at);
    return r?.queued ? tr("Sync started. New campaign activity appears within a few minutes.") : tr("A sync is already waiting. Nothing more to do.");
  });
  const disconnect = () => run("disconnect", async () => {
    await post("/api/me/connections/lemlist/disconnect", {});
    await reload();
    return tr("Lemlist is disconnected. We forgot your key. What it brought is kept.");
  });
  return <Card id="lemlist" name="Lemlist" Icon={Ico.campaign} c={c} purpose={tr("Records the campaign activity of your own Lemlist account for people in our database.")}
    keep={<Keep keeps={[tr("Campaign events for people we already hold: sent, opened, clicked, replied or bounced."), tr("The campaign name and the date."), tr("Visible to the whole team.")]}
      never={tr("Your key is stored encrypted and used only to read these events. The team's shared Lemlist connection is not affected.")} />}>
    <Status c={c} who={tr("Connected to")} />
    {c.status !== "connected" && <form className="cn-field" onSubmit={connect}>
      <label htmlFor={`${id}-key`}>{c.status === "needs_attention" ? tr("Enter your Lemlist API key again") : tr("Your Lemlist API key")}</label>
      <input id={`${id}-key`} type="password" name="lemlist-api-key" autoComplete="new-password" spellCheck={false} autoCapitalize="off" value={key} disabled={!vault}
        onChange={(e) => setKey(e.target.value)} aria-describedby={`${id}-hint`} />
      <p className="cn-hint m-0" id={`${id}-hint`}>{tr("Find it in Lemlist: Settings, Integrations, API.")}</p>
      <div className="cn-actions"><button type="submit" className="primary" disabled={!vault || !key.trim() || busy !== null}>{busy === "connect" ? tr("Checking the key…") : tr("Connect Lemlist")}</button></div>
    </form>}
    {!vault && <p className="cn-hint m-0">{tr("Not available yet: an administrator has to switch connections on first.")}</p>}
    {live && <div className="cn-actions"><button type="button" disabled={!vault || busy !== null} onClick={sync}>{busy === "sync" ? tr("Starting…") : tr("Sync now")}</button></div>}
    <Msg n={note} />
    {live && <Disconnect name="Lemlist" c={c} busy={busy !== null} onConfirm={disconnect} />}
  </Card>;
}

type Delivery = { received_at: string; handled: boolean; note?: string | null; payload?: unknown };

/** The last few raw deliveries from Bluedot, so the format can be checked. Loaded when the list is opened. */
function Deliveries({ fresh }: { fresh: string | null | undefined }) {
  const [rows, setRows] = useState<Delivery[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => { setBusy(true); return api<{ items?: Delivery[] }>("/api/me/connections/bluedot/events").then((x) => { setRows(x.items ?? []); setErr(null); }).catch(setErr).finally(() => setBusy(false)); };
  useEffect(() => { if (open && rows !== null) void load(); }, [fresh]);       // a new delivery arrived while it is open
  const json = (p: unknown) => { try { return typeof p === "string" ? p : JSON.stringify(p, null, 2); } catch { return String(p); } };
  return <details className="cn-events" onToggle={(e) => { const o = (e.currentTarget as HTMLDetailsElement).open; setOpen(o); if (o && rows === null) void load(); }}>
    <summary>{tr("Latest deliveries")}</summary>
    <p className="cn-hint">{tr("The last 10 things Bluedot sent to your address, as they arrived. Useful when a meeting does not show up.")}</p>
    {err ? <Err e={err} onRetry={() => void load()} /> : null}
    {rows === null && !err && <p className="cn-hint m-0" role="status">{tr("Loading")}…</p>}
    {rows !== null && rows.length === 0 && <p className="cn-hint m-0">{tr("Nothing has arrived yet. Add the address in Bluedot and finish a meeting.")}</p>}
    {rows !== null && rows.length > 0 && <ol className="cn-evlist">{rows.map((r, i) => <li key={i}>
      <div className="cn-ev-top"><time dateTime={r.received_at}>{fmtDateTime(r.received_at)}</time>
        <span className={`badge ${r.handled ? "ok" : "warn"}`}>{r.handled ? tr("Handled") : tr("Not handled")}</span></div>
      {r.note && <p className="cn-ev-note">{r.note}</p>}
      <pre tabIndex={0} aria-label={tr("Raw delivery")}>{json(r.payload)}</pre>
    </li>)}</ol>}
    {rows !== null && <button type="button" className="cn-quiet" disabled={busy} onClick={() => void load()}>{tr("Refresh")}</button>}
  </details>;
}

function BluedotCard({ c, vault, reload }: CardProps) {
  const { busy, note, run } = useAct();
  const [url, setUrl] = useState<string | null>(null);        // shown once, only here: the server keeps just a fingerprint
  const [replace, setReplace] = useState(false);
  const live = c.status !== "disconnected" && Boolean(c.has_webhook);
  const create = () => run("token", async () => {
    const r = await post<{ webhook_url?: string }>("/api/me/connections/bluedot/token");
    if (!r?.webhook_url) throw new ApiError(500, "no address");
    setUrl(r.webhook_url); setReplace(false);
    await reload();
  });
  const disconnect = () => run("disconnect", async () => {
    await post("/api/me/connections/bluedot/disconnect", {});
    setUrl(null);
    await reload();
    return tr("Bluedot is disconnected. The old address stopped working. What it brought is kept.");
  });
  const status: Conn = live ? c : { ...c, status: "disconnected" };
  return <Card id="bluedot" name="Bluedot" Icon={Ico.meeting} c={status} purpose={tr("Puts the summaries of your recorded meetings on the pages of the people you met, for the whole team.")}
    keep={<Keep keeps={[tr("For each meeting: the title, the summary and the date."), tr("Only for people who are already in our database."), tr("Visible to the whole team."), tr("The last 10 raw deliveries, for 30 days, so we can fix the format if needed.")]}
      never={tr("Your webhook address is stored only as a fingerprint, so it cannot be shown again.")} />}>
    {live && <div className="cn-status">
      <p className="cn-who"><i className="cn-dot ok" aria-hidden="true" />{tr("Your webhook address is active.")}</p>
      <p className="cn-meta">{c.last_sync_at ? tr("Last delivery {}.", fmtDateTime(c.last_sync_at)) : tr("Nothing has arrived yet.")}{(c.stored ?? 0) > 0 && <> {tr("Kept so far: {}.", UNITS.bluedot(c.stored ?? 0))}</>}</p>
    </div>}
    {url && <div className="cn-secret">
      <label htmlFor="cn-bd-url"><b>{tr("Your webhook address")}</b></label>
      <div className="cn-secret-row">
        <input id="cn-bd-url" readOnly value={url} onFocus={(e) => e.currentTarget.select()} spellCheck={false} />
        <CopyButton text={url} what={tr("webhook address")} />
      </div>
      <p className="cn-warn" role="note"><Ico.alert /><span>{tr("Copy it now. We will not show it again. Creating a new address replaces this one.")}</span></p>
      <ol className="cn-steps">
        <li>{tr("Copy the address above.")}</li>
        <li>{tr("In Bluedot, open Settings, then Webhooks, and add a new webhook.")}</li>
        <li>{tr("Paste the address and save. Your next meeting summary then shows on the person's page.")}</li>
      </ol>
    </div>}
    {!live && !url && <p className="cn-hint m-0">{tr("You get a secret address of your own. You paste it into Bluedot once, and every meeting summary comes here.")}</p>}
    {!replace && <div className="cn-actions">
      <button type="button" className={live ? "" : "primary"} disabled={!vault || busy !== null} onClick={() => (live ? setReplace(true) : void create())}>
        {busy === "token" ? tr("Creating…") : live ? tr("Create a new address") : tr("Create my webhook address")}</button>
    </div>}
    {replace && <div className="cn-confirm" role="group" aria-label={tr("Create a new address")}>
      <p className="m-0"><b>{tr("Create a new address?")}</b> {tr("The old one stops working. Paste the new one into Bluedot.")}</p>
      <div className="cn-actions">
        <button type="button" className="primary" disabled={busy !== null} onClick={() => void create()}>{busy === "token" ? tr("Creating…") : tr("Create a new address")}</button>
        <button type="button" disabled={busy !== null} onClick={() => setReplace(false)} autoFocus>{tr("Keep the old one")}</button>
      </div>
    </div>}
    {!vault && <p className="cn-hint m-0">{tr("Not available yet: an administrator has to switch connections on first.")}</p>}
    <Msg n={note} />
    {live && <Deliveries fresh={c.last_sync_at} />}
    {live && <Disconnect name="Bluedot" c={status} busy={busy !== null} onConfirm={disconnect} />}
  </Card>;
}

/* ------------------------------------------------------------------ conversations: shown on the person page and the company page */
type Who = { name?: string | null; email?: string | null };
type ConvItem = { id: string; channel: "email" | "lemlist" | "meeting"; occurred_at: string; direction?: "in" | "out" | "both" | null; title?: string | null; gist?: string | null;
  owner: Who; mine?: boolean; detail_hidden?: boolean; person?: { id: string; name: string } | null; people?: { id: string; name: string }[] };
type ConvData = { summary: { last_contact_at?: string | null; last_contact_by?: Who | null; emails?: number; meetings?: number; campaign_events?: number }; items: ConvItem[] };

const whoName = (w?: Who | null) => w?.name?.trim() || (w?.email ? w.email.split("@")[0] : tr("A colleague"));
const CHANNEL_ICON = { email: Ico.mail, lemlist: Ico.campaign, meeting: Ico.meeting };

/** "Ann emailed Maja", "Maja replied to a campaign email", "Meeting with Maja": who did what, in one short line. */
function whatLine(it: ConvItem, other: string): string {
  const owner = it.mine ? tr("You") : whoName(it.owner);
  const person = it.person?.name || other;
  if (it.channel === "meeting") {                                   // on a company page one meeting lists everyone we know who was in it
    const names = (it.people?.length ? it.people : it.person ? [it.person] : []).map((x) => x.name).filter(Boolean);
    return names.length ? tr("Meeting with {}", names.join(", ")) : tr("Meeting");
  }
  if (it.channel === "lemlist") return it.direction === "in" ? tr("{} replied to a campaign email", person) : it.direction === "out" ? tr("Campaign email sent to {}", person) : tr("Campaign activity with {}", person);
  if (it.direction === "in") return it.mine ? tr("{} emailed you", person) : tr("{} emailed {}", person, owner);
  if (it.direction === "both") return tr("{} and {} exchanged emails", owner, person);
  return tr("{} emailed {}", owner, person);
}
/** The line for a row whose details only its owner may see: that it happened is still shared. */
function hiddenLine(it: ConvItem, other: string): string {
  const owner = whoName(it.owner), person = it.person?.name || other, day = dayText(it.occurred_at);
  if (it.channel !== "email") return tr("{} had a conversation with {} on {}. Only {} can see the details.", owner, person, day, owner);
  if (it.direction === "in") return tr("{} emailed {} on {}. Only {} can see the details.", person, owner, day, owner);
  if (it.direction === "both") return tr("{} and {} exchanged emails on {}. Only {} can see the details.", owner, person, day, owner);
  return tr("{} emailed {} on {}. Only {} can see the details.", owner, person, day, owner);
}

/** A summary can be long: three lines, and a button for the rest (only when there is more to show). */
function Gist({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => { if (!open) setOver(el.scrollHeight > el.clientHeight + 1); };
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, [text, open]);
  return <>
    <p ref={ref} className={`cn-gist${open ? " open" : ""}`}>{text}</p>
    {(over || open) && <button type="button" className="linkish cn-more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? tr("Show less") : tr("Show more")}</button>}
  </>;
}

const FEW = 5;

/** Every email, campaign event and meeting we hold with a person (or with the people of a company), newest first. Renders nothing when the server cannot say. */
export function Conversations({ scope, id, name }: { scope: "person" | "account"; id: string; name: string }) {
  const [data, setData] = useState<ConvData | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "none" | "failed">("loading");
  const [all, setAll] = useState(false);
  const hid = useId();
  const load = () => {
    const q = new AbortController();
    setState("loading");
    api<ConvData>(`/api/${scope === "person" ? "people" : "accounts"}/${id}/conversations`, { signal: q.signal })
      .then((x) => { if (q.signal.aborted) return; if (x && Array.isArray(x.items)) { setData(x); setState("ok"); } else setState("none"); })
      .catch((e) => { if (!q.signal.aborted) setState(e instanceof ApiError && e.status >= 500 ? "failed" : "none"); });   // no access, no such route yet, not switched on: nothing to show
    return q;
  };
  useEffect(() => { setData(null); setAll(false); const q = load(); return () => q.abort(); }, [scope, id]);
  if (state === "loading" || state === "none") return null;
  const purpose = scope === "person" ? tr("Emails, campaign activity and meetings with {}, from your team's connected accounts.", name)
    : tr("Emails, campaign activity and meetings with people at {}, from your team's connected accounts.", name);
  const head = <><h2 id={hid}>{tr("Conversations")}</h2><p className="ac-purpose">{purpose}</p></>;
  if (state === "failed" || !data) return <section className="ac-panel cn-conv" aria-labelledby={hid}>{head}
    <p className="cn-quietline">{tr("Conversations could not be loaded.")} <button type="button" className="linkish" onClick={() => void load()}>{tr("Try again")}</button></p></section>;
  const s = data.summary ?? {};
  const first = data.items[0];
  const other = scope === "person" ? name.split(/\s+/)[0] || name : tr("someone at this company");
  const parts = [(s.emails ?? 0) > 0 && UNITS.gmail(s.emails!), (s.meetings ?? 0) > 0 && counted(s.meetings!, ["meeting", "meetings"], ["spotkanie", "spotkania", "spotkań"]),
    (s.campaign_events ?? 0) > 0 && UNITS.lemlist(s.campaign_events!)].filter(Boolean) as string[];
  const by = first?.mine && first.owner?.email && s.last_contact_by?.email === first.owner.email ? tr("you") : whoName(s.last_contact_by ?? first?.owner);
  const via = first ? first.channel === "email" ? tr("by email") : first.channel === "lemlist" ? tr("in a campaign") : tr("in a meeting") : "";
  const shown = all ? data.items : data.items.slice(0, FEW);
  return <section className="ac-panel cn-conv" aria-labelledby={hid}>
    {head}
    {data.items.length === 0 ? <p className="cn-empty">{tr("No conversations recorded yet. Connect your Gmail, Lemlist or Bluedot and they will show up here.")} <Link to="/connections">{tr("My connections")}</Link></p> : <>
      <p className="cn-sum">
        {s.last_contact_at && <b>{via ? tr("Last contact {} by {}, {}.", dayText(s.last_contact_at), by, via) : tr("Last contact {} by {}.", dayText(s.last_contact_at), by)}</b>}
        {parts.length > 0 && <> {parts.join(", ")}.</>}
      </p>
      <ol className="cn-list">{shown.map((it) => { const Icon = CHANNEL_ICON[it.channel] ?? Ico.mail;
        const hidden = Boolean(it.detail_hidden);
        return <li key={it.id} className={`cn-row ${it.channel}${hidden ? " hidden" : ""}`}>
          <span className="cn-ic" aria-hidden="true"><Icon /></span>
          <div className="cn-rb">
            {hidden ? <p className="cn-hid"><Ico.lock />{hiddenLine(it, other)}</p> : <>
              <div className="cn-top"><b>{whatLine(it, other)}</b><time dateTime={it.occurred_at}>{dayText(it.occurred_at)}</time></div>
              {it.title && <p className="cn-title">{it.title}</p>}
              {it.gist && it.gist !== it.title && <Gist text={it.gist} />}
              {it.channel === "meeting" && !it.mine && <p className="cn-by">{tr("Recorded by {}", whoName(it.owner))}</p>}
            </>}
          </div>
        </li>; })}</ol>
      {data.items.length > FEW && <button type="button" className="linkish cn-all" aria-expanded={all} onClick={() => setAll(!all)}>{all ? tr("Show fewer") : tr("Show all {}", data.items.length)}</button>}
    </>}
  </section>;
}
