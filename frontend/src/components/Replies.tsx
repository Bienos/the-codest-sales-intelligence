import { ReactNode, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, post } from "../api";
import { counted, getLanguage, tr } from "../locale";
import { Signature, SignatureEditor, signatureSource, useSignature } from "./EmailSignature";
import "../styles/replies.css";

/** The emails between one person and us, read like a good mail client, and a composer that answers in the same Lemlist thread.
 *  Used on the person page (the whole thread) and on Today (their reply and the composer). The server decides everything that matters:
 *  whether sending is switched on, whether this person may be written to, which mailbox answers, and the signature appended. This file
 *  only shows it in plain words. */

export type TextState = "full" | "preview" | "pending" | "missing";
type Who = { name: string | null; email?: string | null } | null;
export type ReplyInfo = {
  activity_id: string; at: string | null; campaign: string | null; subject: string | null; text: string | null; text_state: TextState;
  lemlist_url: string | null; sender: { name: string | null; email: string } | null; blocked: string | null; to?: Who;
  answered: { status: string; at: string; by: string } | null;
};
type Config = { sending_on: boolean; lemlist_connected: boolean; may_send: boolean };
type Engagement = { opens: number; last_open: string | null; clicks: number; last_click: string | null; bounces: number; last_bounce: string | null };
/** One email of the thread. `via`: a campaign email, an answer sent in the Lemlist inbox, or an answer sent from this app. `step` counts from 1. */
type ThreadItem = {
  id: string; kind: "sent" | "reply" | "answer"; via?: "campaign" | "lemlist" | "app"; at: string | null; campaign?: string | null; step?: number | null;
  steps?: number | null; subject?: string | null; text: string | null; signature?: string | null; text_state: TextState; from?: Who; to?: Who;
  engagement?: Engagement; newest?: boolean; status?: string; error?: string | null; by?: string; by_name?: string | null;
};
type Summary = { sent: number; first_sent: string | null; last_sent: string | null; opens: number; clicks: number; bounced: boolean;
  replies: number; last_reply: string | null; answers: number; last_answer: string | null };
export type Thread = Config & { person: { id: string; name: string; do_not_contact: boolean; email?: string | null; photo_url?: string | null };
  items: ThreadItem[]; summary?: Summary; reply: ReplyInfo | null; refresh?: { refreshed: boolean; error?: string }; signature?: Signature };

/** The server's reasons (ri.outbound.replies.BLOCKED), in the words shown here. */
const BLOCKED: Record<string, string> = {
  do_not_contact: "This person is on the do-not-contact list, so nothing can be sent to them.",
  no_activity_id: "Lemlist has not given us the id of this email, so a reply cannot be threaded from here. Answer in Lemlist.",
  no_mailbox: "Lemlist has not told us which mailbox sent the campaign, so a reply cannot be sent from here yet. Answer in Lemlist.",
  no_contact: "Lemlist has not told us who this contact is, so a reply cannot be sent from here yet. Answer in Lemlist.",
};

/* ------------------------------------------------------------------ dates: relative when recent, the full date in the tooltip */
const loc = () => (getLanguage() === "pl" ? "pl-PL" : "en-GB");
const asDate = (v?: string | null): Date | null => { if (!v) return null; const d = new Date(v); return isNaN(d.getTime()) ? null : d; };
const clock = (d: Date) => d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
/** "Just now", "12 min ago", "2 h ago", "Yesterday 14:05", "Mon 14:05", "21 Nov", "21 Nov 2025". */
export function relTime(v?: string | null, now = new Date()): string {
  const d = asDate(v);
  if (!d) return "";
  const mins = Math.floor((now.getTime() - d.getTime()) / 60_000);
  const days = Math.round((midnight(now) - midnight(d)) / 86_400_000);
  if (mins < 1 && mins > -2) return tr("Just now");
  if (days === 0 && mins > 0) return mins < 60 ? tr("{} min ago", mins) : tr("{} h ago", Math.floor(mins / 60));
  if (days === 1) return tr("Yesterday {}", clock(d));
  if (days > 1 && days < 7) return `${d.toLocaleDateString(loc(), { weekday: "short" })} ${clock(d)}`;
  return d.toLocaleDateString(loc(), { day: "numeric", month: "short", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}
const fullTime = (v?: string | null) => asDate(v)?.toLocaleString(loc(), { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }) ?? "";
/** "26 Nov" (with the year when it is not this year). */
function day(v?: string | null): string {
  const d = asDate(v);
  return d ? d.toLocaleDateString(loc(), { day: "numeric", month: "short", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) }) : "";
}
const sameDay = (a?: string | null, b?: string | null) => Boolean(a && b) && new Date(a!).toDateString() === new Date(b!).toDateString();
function When({ at, className = "rp-when" }: { at?: string | null; className?: string }) {
  if (!at) return null;
  return <time className={className} dateTime={at} title={fullTime(at)}>{relTime(at)}</time>;
}

const firstName = (name: string) => name.trim().split(/\s+/)[0] || name;
/** One line of an email for a list row: the first words, without line breaks. */
export function firstWords(text: string, max = 90): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).replace(/\s+\S*$/, "")}…`;
}
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`);
const initials = (name?: string | null) => {
  const w = String(name ?? "").replace(/[<(].*$/, "").split(/[\s@._-]+/).filter((x) => /^\p{L}/u.test(x));
  return (w.length >= 2 ? `${w[0][0]}${w[w.length - 1][0]}` : (w[0] ?? "?").slice(0, 2)).toUpperCase();
};
/** "Re: Claims platform": Lemlist answers in the same thread, so the subject is the thread's own. */
const reSubject = (s?: string | null) => {
  const rest = String(s ?? "").replace(/^\s*((re|aw|sv|vs|odp|antw|wg|fw|fwd|r)\s*(\[\d+\])?\s*:\s*)+/i, "").trim();
  return rest ? tr("Re: {}", rest) : null;
};

/* ------------------------------------------------------------------ icons (square line work, like the rest of the app) */
const Svg = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <svg className={`rp-ico ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true">{children}</svg>
);
const I = {
  eye: <Svg><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z" /><circle cx="12" cy="12" r="2.6" /></Svg>,
  click: <Svg><path d="M7 3l11 10-5 1 3 6-2.5 1-3-6-3.5 3.5z" /></Svg>,
  warn: <Svg><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 17.6v.4" /></Svg>,
  mail: <Svg><rect x="3" y="5" width="18" height="14" /><path d="M3 6l9 7 9-7" /></Svg>,
  reply: <Svg><path d="M10 6L4 12l6 6" /><path d="M4 12h10a6 6 0 016 6v1" /></Svg>,
  check: <Svg><path d="M4.5 12.5l5 5 10-11" /></Svg>,
  lock: <Svg><rect x="5" y="11" width="14" height="10" /><path d="M8 11V8a4 4 0 018 0v3" /></Svg>,
  pen: <Svg><path d="M4 20l1-4L16 5l3 3L8 19z" /><path d="M14 7l3 3" /></Svg>,
  out: <Svg><path d="M10 5H5v14h14v-5M13 4h7v7M20 4l-9 9" /></Svg>,
  chevron: <Svg><path d="M6 9l6 6 6-6" /></Svg>,
  send: <Svg><path d="M3 11l18-8-6 18-3-7z" /><path d="M12 14l9-11" /></Svg>,
};

/** Square initials (ours: navy; theirs: their photo, else purple-tinted initials). */
function Face({ name, ours, photo, size = "md" }: { name?: string | null; ours?: boolean; photo?: string | null; size?: "sm" | "md" }) {
  const [failed, setFailed] = useState<string | null>(null);
  const shown = Boolean(photo) && failed !== photo;
  return <span className={`rp-face rp-face-${size}${ours ? " is-ours" : ""}${shown ? " has-photo" : ""}`} aria-hidden="true">
    {shown ? <img src={photo!} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(photo!)} /> : initials(name)}
  </span>;
}

let configOnce: Promise<Config | null> | null = null;
/** May this person send, is sending switched on, is Lemlist connected. Asked once per page load, and only when `wanted`. */
export function useReplyConfig(wanted = true): Config | null {
  const [c, setC] = useState<Config | null>(null);
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    configOnce ??= api<Config>("/api/replies/config").catch((e) => {
      if (!(e instanceof ApiError)) configOnce = null;               // offline: ask again next time; a refusal stays a refusal
      return null;
    });
    configOnce.then((x) => { if (live) setC(x); });
    return () => { live = false; };
  }, [wanted]);
  return c;
}

/** The words of an email: the first `lines` lines with a fade and a button for the rest. When Lemlist has not sent the words, says so plainly. */
export function ReplyText({ text, state, lemlistUrl, lines = 4, more = "Show the whole email" }: { text: string | null; state: TextState; lemlistUrl?: string | null;
  lines?: number; more?: string }) {
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
  const link = lemlistUrl ? <a href={lemlistUrl} target="_blank" rel="noopener noreferrer">{tr("Open in Lemlist")} ↗</a> : null;
  if (!text) return <p className="rp-none">
    {state === "pending" ? tr("Lemlist has not sent us the words of this email yet. They usually arrive within the hour.")
      : tr("Lemlist did not give us the words of this email, so they cannot be shown here.")}
    {link && <> {link}</>}
  </p>;
  return <div className="rp-words">
    <p ref={ref} className={`rp-text${open ? " open" : ""}${over && !open ? " is-cut" : ""}`} style={{ "--rp-lines": lines } as React.CSSProperties}>{text}</p>
    {(over || open) && <button type="button" className="rp-more" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? tr("Show less") : tr(more)}<span className={open ? "is-up" : undefined}>{I.chevron}</span></button>}
    {state === "preview" && <p className="rp-none">{tr("Only the first line came through from Lemlist.")}{link && <> {link}</>}</p>}
  </div>;
}

/** Why Send is not available, in plain words, or null when it is. */
function whyNot(c: Config | null, reply: ReplyInfo): string | null {
  if (!c) return null;
  if (!c.may_send) return tr("Your role cannot send emails from here.");
  if (!c.sending_on) return tr("Sending is switched off by the administrator.");
  if (!c.lemlist_connected) return tr("Lemlist is not connected on the server, so replies cannot be sent from here.");
  if (reply.blocked) return tr(BLOCKED[reply.blocked] ?? "This reply cannot be sent from here. Answer in Lemlist.");
  return null;
}

/* ------------------------------------------------------------------ drafts, kept per person in this browser */
type Draft = { text: string; key: string; activity: string };
const DRAFT = (pid: string) => `ri_reply_draft:v1:${pid}`;
function readDraft(pid: string, activity: string): Draft {
  try {
    const d = JSON.parse(localStorage.getItem(DRAFT(pid)) ?? "null") as Draft | null;
    if (d && typeof d.text === "string" && typeof d.key === "string") return d.activity === activity ? d : { text: d.text, key: newKey(), activity };
  } catch { /* private window or blocked storage: start empty */ }
  return { text: "", key: newKey(), activity };
}
function writeDraft(pid: string, d: Draft | null) {
  try {
    if (d && d.text.trim()) localStorage.setItem(DRAFT(pid), JSON.stringify(d));
    else localStorage.removeItem(DRAFT(pid));
  } catch { /* nothing to do: the draft lives on in the box */ }
}
const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

type Sent = { at: string | null; from: string | null; repeat: boolean; words: string };
type Note = { kind: "ok" | "err"; text: string } | null;

/** The composer. Compact until focused (one line, "Reply to Ola…"), then From, To and Subject above an auto-growing box, the signature
 *  under it, Send and Open in Lemlist. Ctrl/Cmd+Enter sends. One key per draft, kept with the draft in this browser: a double click, a retry
 *  after a lost answer or a reload never sends twice. When sending is not possible, one line says why and there is no box. */
export function ReplyComposer({ personId, personName, reply, config, signature, toEmail, onSent, openSignal = 0 }: { personId: string; personName: string;
  reply: ReplyInfo; config: Config | null; signature?: Signature | null; toEmail?: string | null; onSent?: (t: Thread) => void; openSignal?: number }) {
  const [draft, setDraft] = useState<Draft>(() => readDraft(personId, reply.activity_id));
  const [open, setOpen] = useState(() => draft.text.trim().length > 0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [sent, setSent] = useState<Sent | null>(null);
  const [editSig, setEditSig] = useState(false);
  const [sig, setSig] = useSignature(personId, signature ?? null, open);
  const id = useId();
  const box = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => { setDraft(readDraft(personId, reply.activity_id)); }, [personId, reply.activity_id]);
  useEffect(() => { writeDraft(personId, draft); }, [personId, draft]);
  // "Reply" elsewhere on the page: open the box, then (once it is open and full height) bring it into view and put the cursor in it
  const pending = useRef(false);
  useEffect(() => {
    if (!openSignal) return;
    pending.current = true;
    setOpen(true); setSent(null);
  }, [openSignal]);
  useEffect(() => {
    if (!open || !pending.current) return;
    pending.current = false;
    requestAnimationFrame(() => {
      box.current?.focus({ preventScroll: true });
      root.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    });
  }, [open, openSignal]);
  useLayoutEffect(() => {                                            // the box grows with the words, up to about half the screen
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    if (open) el.style.height = `${Math.min(el.scrollHeight + 2, Math.max(220, Math.round(window.innerHeight * 0.55)))}px`;
  }, [draft.text, open]);

  const first = firstName(personName);
  const reason = whyNot(config, reply);
  if (reason) return <div className="rp-compose is-blocked" role="note">
    <span className="rp-blocked-i">{I.lock}</span>
    <p>{reason}</p>
    {reply.lemlist_url && <a className="rp-alt" href={reply.lemlist_url} target="_blank" rel="noopener noreferrer">{tr("Open in Lemlist")}{I.out}</a>}
  </div>;

  const text = draft.text;
  const setText = (t: string) => { setDraft((d) => ({ ...d, text: t })); if (note) setNote(null); };
  const send = async () => {
    if (busy) return;
    if (!text.trim()) { setNote({ kind: "err", text: tr("Write a few words first.") }); box.current?.focus(); return; }
    if (!config) return;
    setBusy(true); setNote(null);
    try {
      const r = await post<{ status: string; repeat: boolean; sent_at?: string | null; from?: string | null; thread?: Thread }>(
        `/api/people/${personId}/email-thread/reply`, { activity_id: reply.activity_id, text, key: draft.key });
      setSent({ at: r.sent_at ?? new Date().toISOString(), from: r.from ?? reply.sender?.email ?? null, repeat: r.repeat, words: text });
      setDraft({ text: "", key: newKey(), activity: reply.activity_id });
      writeDraft(personId, null);
      setOpen(false); setEditSig(false);
      if (r.thread) onSent?.(r.thread);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "";
      if (e instanceof ApiError && e.status === 502) setDraft((d) => ({ ...d, key: newKey() }));   // no clear answer: the person checks Lemlist, then may send again
      setNote({ kind: "err", text: msg && !/^(Internal Server Error|Bad Gateway)$/i.test(msg) ? tr(msg) : tr("The reply could not be sent. Nothing was sent. Try again in a minute.") });
    } finally {
      setBusy(false);
    }
  };
  const discard = () => { setDraft({ text: "", key: newKey(), activity: reply.activity_id }); writeDraft(personId, null); setOpen(false); setNote(null); setEditSig(false); };
  const from = reply.sender;
  const toAddr = reply.to?.email ?? toEmail ?? null;
  const subject = reSubject(reply.subject);
  const kbd = isMac() ? "⌘ Enter" : "Ctrl Enter";

  return <div ref={root} className={`rp-compose${open ? " is-open" : ""}`}>
    {sent && !open && <p className="rp-sent" role="status">
      <span className="rp-sent-i">{I.check}</span>
      <span>{sent.repeat ? tr("Already sent {}. It was not sent again.", sent.at ? clock(new Date(sent.at)) : "")
        : sent.from ? tr("Sent {} from {}", sent.at ? clock(new Date(sent.at)) : "", sent.from) : tr("Sent {}", sent.at ? clock(new Date(sent.at)) : "")}
        <small> · {tr("it will appear in Lemlist")}</small></span>
    </p>}
    {open && <dl className="rp-hdr">
      <div><dt>{tr("From")}</dt><dd>{from ? <>{from.name && <b>{from.name}</b>} <span className="rp-addr">{from.email}</span></> : tr("The mailbox that sent the campaign")}</dd></div>
      <div><dt>{tr("To")}</dt><dd><b>{personName}</b>{toAddr && <> <span className="rp-addr">{toAddr}</span></>}</dd></div>
      <div><dt>{tr("Subject")}</dt><dd><span className="rp-subj-v">{subject ?? tr("The thread's own subject")}</span>
        <small className="rp-subj-h">{tr("Lemlist keeps the thread and its subject.")}</small></dd></div>
    </dl>}
    <div className="rp-field">
      {!open && <Face name={from?.name ?? from?.email} ours size="sm" />}
      <label className="rp-sr" htmlFor={id}>{tr("Your reply to {}", first)}</label>
      <textarea id={id} ref={box} value={text} rows={open ? 5 : 1} maxLength={5000} disabled={busy}
        placeholder={open ? tr("Write your reply in plain text…") : tr("Reply to {}…", first)}
        onFocus={() => { if (!open) { setOpen(true); setSent(null); } }}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); }
          else if (e.key === "Escape" && !text.trim()) { setOpen(false); (e.target as HTMLTextAreaElement).blur(); }
        }} />
    </div>
    {open && <>
      <div className="rp-sig">
        {editSig && sig ? <SignatureEditor value={sig} onSaved={setSig} onDone={() => setEditSig(false)} /> : <>
          <div className="rp-sig-h">
            <span>{tr("Signature")}</span>
            {sig && !sig.lemlist_adds && <button type="button" className="rp-sig-edit" onClick={() => setEditSig(true)}>{I.pen}{tr("Edit signature")}</button>}
          </div>
          {!sig ? <p className="rp-sig-src">{tr("Loading")}…</p> : <>
            {sig.text && <p className="rp-sig-t">{sig.text}</p>}
            {(!sig.saved || !sig.text || sig.lemlist_adds) && <p className="rp-sig-src">{signatureSource(sig)}</p>}
          </>}
        </>}
      </div>
      <div className="rp-acts">
        <button type="button" className="primary rp-send" aria-busy={busy} disabled={busy || !config} onClick={() => void send()}>
          {I.send}{busy ? tr("Sending…") : tr("Send")}</button>
        {reply.lemlist_url && <a className="rp-alt" href={reply.lemlist_url} target="_blank" rel="noopener noreferrer">{tr("Open in Lemlist")}{I.out}</a>}
        <span className="rp-kbd" aria-hidden="true">{kbd}</span>
        <button type="button" className="rp-discard" onClick={discard}>{text.trim() ? tr("Discard draft") : tr("Close")}</button>
      </div>
      {text.trim() && <p className="rp-saved-h">{tr("Your draft is kept in this browser until you send it.")}</p>}
    </>}
    {note && <p className={note.kind === "ok" ? "okmsg rp-note" : "err rp-note"} role={note.kind === "ok" ? "status" : "alert"}>{note.text}</p>}
  </div>;
}

/** Today: their reply as a compact card: who, where they work, when, the subject and their words (six lines, a fade, "Read all"). */
export function ReplyCard({ reply, name, title, company, photo }: { reply: ReplyInfo; name: string; title?: string | null; company?: string | null; photo?: string | null }) {
  const role = [title, company].filter(Boolean).join(" · ");
  return <div className="rp-card">
    <div className="rp-card-h">
      <Face name={name} photo={photo} />
      <span className="rp-card-who"><b>{name}</b>{role && <small>{role}</small>}</span>
      <When at={reply.at} />
    </div>
    {reply.subject && <p className="rp-card-subj">{reply.subject}{reply.campaign && <small> · {tr("Campaign “{}”", reply.campaign)}</small>}</p>}
    <ReplyText text={reply.text} state={reply.text_state} lemlistUrl={reply.lemlist_url} lines={6} more="Read all" />
  </div>;
}

/* ------------------------------------------------------------------ the thread */
const times = (n: number) => (n === 1 ? tr("once") : counted(n, ["time", "times"], ["raz", "razy", "razy"]));

/** "3 emails sent from 21 Nov to 26 Nov, opened 4 times, replied on 26 Nov." from the server's counts (for screen readers and the tooltip). */
function summaryLine(s: Summary): string {
  const parts: string[] = [];
  if (s.sent > 0) {
    const n = counted(s.sent, ["email", "emails"], ["e-mail", "e-maile", "e-maili"]);
    parts.push(s.sent === 1 || sameDay(s.first_sent, s.last_sent) ? tr("{} sent on {}", n, day(s.last_sent)) : tr("{} sent from {} to {}", n, day(s.first_sent), day(s.last_sent)));
  }
  if (s.opens > 0) parts.push(tr("opened {}", times(s.opens)));
  if (s.clicks > 0) parts.push(tr("clicked {}", times(s.clicks)));
  if (s.bounced) parts.push(tr("bounced"));
  if (s.replies > 0 && s.last_reply) parts.push(s.replies === 1 ? tr("replied on {}", day(s.last_reply)) : tr("replied {}, last on {}", times(s.replies), day(s.last_reply)));
  else if (s.sent > 0) parts.push(tr("no reply yet"));
  if (s.answers > 0 && s.last_answer) parts.push(tr("we answered on {}", day(s.last_answer)));
  const line = parts.join(", ");
  return line ? `${line.charAt(0).toUpperCase()}${line.slice(1)}.` : "";
}

/** The summary as a compact strip of small facts. */
function Strip({ s }: { s: Summary }) {
  const span = s.sent > 1 && !sameDay(s.first_sent, s.last_sent) ? `${day(s.first_sent)} – ${day(s.last_sent)}` : day(s.last_sent);
  const facts: { k: string; icon: ReactNode; text: ReactNode; tone?: string }[] = [];
  if (s.sent > 0) facts.push({ k: "s", icon: I.mail, text: <><b>{counted(s.sent, ["email", "emails"], ["e-mail", "e-maile", "e-maili"])}</b> {tr("sent")}<small>{span}</small></> });
  if (s.opens > 0) facts.push({ k: "o", icon: I.eye, text: <><b>{counted(s.opens, ["open", "opens"], ["otwarcie", "otwarcia", "otwarć"])}</b></> });
  if (s.clicks > 0) facts.push({ k: "c", icon: I.click, text: <><b>{counted(s.clicks, ["click", "clicks"], ["kliknięcie", "kliknięcia", "kliknięć"])}</b></> });
  if (s.bounced) facts.push({ k: "b", icon: I.warn, text: <b>{tr("Bounced")}</b>, tone: "warn" });
  if (s.replies > 0 && s.last_reply) facts.push({ k: "r", icon: I.reply, text: <><b>{s.replies === 1 ? tr("Replied") : tr("Replied {}", times(s.replies))}</b><small>{day(s.last_reply)}</small></>, tone: "them" });
  else if (s.sent > 0) facts.push({ k: "r", icon: I.reply, text: <span className="rp-quiet">{tr("No reply yet")}</span> });
  if (s.answers > 0 && s.last_answer) facts.push({ k: "a", icon: I.check, text: <><b>{tr("We answered")}</b><small>{day(s.last_answer)}</small></> });
  if (!facts.length) return null;
  const line = summaryLine(s);
  return <ul className="rp-strip" aria-label={line} title={line}>{facts.map((f) => <li key={f.k} className={f.tone ? `is-${f.tone}` : undefined}>{f.icon}<span>{f.text}</span></li>)}</ul>;
}

const who = (w?: Who) => w?.name || w?.email || "";
const isOurs = (i: ThreadItem) => i.kind !== "reply";

/** The small marks on a campaign email: sequence step, opens, clicks, bounce. Each with a tooltip in words. */
function Marks({ i, words = false }: { i: ThreadItem; words?: boolean }) {
  const g = i.engagement;
  const marks: { k: string; icon?: ReactNode; text: string; tip: string; tone?: string }[] = [];
  const st = status(i);
  if (st && !words) marks.push(i.kind === "answer" && i.status !== "sent" ? { k: "v", icon: I.warn, text: st, tip: st, tone: "warn" } : { k: "v", icon: I.reply, text: "", tip: st });
  if (i.step) marks.push({ k: "s", text: i.steps && i.steps >= i.step ? tr("Step {} of {}", i.step, i.steps) : tr("Step {}", i.step),
    tip: i.campaign ? tr("Email {} of the sequence in the campaign “{}”", i.step, i.campaign) : tr("Email {} of the sequence", i.step), tone: "step" });
  if (g?.bounces) marks.push({ k: "b", icon: I.warn, text: words ? tr("Bounced") : "", tip: tr("Bounced on {}", day(g.last_bounce)), tone: "warn" });
  if (g?.opens) marks.push({ k: "o", icon: I.eye, text: words ? (g.opens === 1 ? tr("Opened") : tr("Opened {}", times(g.opens))) : String(g.opens),
    tip: g.opens === 1 ? tr("Opened on {}", day(g.last_open)) : tr("Opened {}, last on {}", times(g.opens), day(g.last_open)) });
  if (g?.clicks) marks.push({ k: "c", icon: I.click, text: words ? (g.clicks === 1 ? tr("Clicked a link") : tr("Clicked links {}", times(g.clicks))) : (g.clicks > 1 ? String(g.clicks) : ""),
    tip: g.clicks === 1 ? tr("Clicked a link on {}", day(g.last_click)) : tr("Clicked links {}, last on {}", times(g.clicks), day(g.last_click)) });
  if (words && i.kind === "sent" && i.via === "campaign" && g && !g.opens && !g.clicks && !g.bounces) marks.push({ k: "n", text: tr("No opens recorded"), tip: tr("No opens recorded"), tone: "none" });
  if (!marks.length) return null;
  return <span className={`rp-marks${words ? " is-words" : ""}`}>{marks.map((m) => <span key={m.k} className={`rp-mark${m.tone ? ` is-${m.tone}` : ""}`} title={m.tip}>
    {m.icon}{m.text && <span>{m.text}</span>}<span className="rp-sr">{m.tip}</span></span>)}</span>;
}

/** Who sent it, in a few words, for the row. */
function sender(i: ThreadItem, name: string): string {
  if (i.kind === "reply") return name;
  return who(i.from) || (i.kind === "answer" ? i.by_name || i.by || tr("We") : tr("We"));
}
function status(i: ThreadItem): string | null {
  if (i.kind === "answer") return i.status === "sent" ? tr("Sent from here") : i.status === "failed" ? tr("Not sent") : i.status === "sending" ? tr("Sending") : tr("Not confirmed");
  if (i.kind === "sent" && i.via === "lemlist") return tr("Answered in Lemlist");
  return null;
}

/** One email: a single line when closed (who, first words, marks, when); the whole email when open. */
function Message({ i, name, photo, toName, open, onToggle, lemlistUrl }: { i: ThreadItem; name: string; photo?: string | null; toName: string; open: boolean;
  onToggle: () => void; lemlistUrl?: string | null }) {
  const bid = useId();
  const ours = isOurs(i);
  const by = sender(i, name);
  const st = status(i);
  const off = i.kind === "answer" && i.status !== "sent";
  const snip = i.text ? firstWords(i.text, 140) : i.text_state === "pending" ? tr("The words have not arrived from Lemlist yet.") : tr("The words of this email are not stored.");
  const addr = ours ? i.from?.email : null;
  const to = ours ? toName : who(i.to);
  return <li className={`rp-msg ${ours ? "is-ours" : "is-theirs"}${open ? " is-open" : ""}${i.newest ? " is-newest" : ""}${off ? " is-off" : ""}`}>
    <button type="button" className="rp-row" aria-expanded={open} aria-controls={bid} onClick={onToggle}>
      <Face name={by} ours={ours} photo={ours ? null : photo} />
      <span className="rp-row-who">
        <b>{by}</b>
        {open && addr && <span className="rp-addr rp-wide">{addr}</span>}
        {i.newest && <span className="rp-new rp-wide">{tr("Newest reply")}</span>}
      </span>
      <span className="rp-row-snip">{open ? <>
        {i.newest && <span className="rp-new rp-narrow">{tr("Newest reply")}</span>}
        {addr && <span className="rp-narrow">{addr} · </span>}{to ? tr("to {}", to) : ""}
        {st && <span className={`rp-state${off ? " is-off" : ""}`}>{st}</span>}
      </> : snip}</span>
      {!open && <Marks i={i} />}
      <When at={i.at} />
    </button>
    {open && <div className="rp-body" id={bid}>
      {(i.subject || i.campaign || i.step) && <p className="rp-meta">
        {i.step && <span className="rp-mark is-step" title={i.campaign ? tr("Email {} of the sequence in the campaign “{}”", i.step, i.campaign) : undefined}>
          {i.steps && i.steps >= i.step ? tr("Step {} of {}", i.step, i.steps) : tr("Step {}", i.step)}</span>}
        {i.subject && <b className="rp-subj">{i.subject}</b>}
        {i.campaign && <span>{tr("Campaign “{}”", i.campaign)}</span>}
      </p>}
      <ReplyText text={i.text} state={i.text_state} lemlistUrl={i.kind === "reply" ? lemlistUrl : null} lines={i.kind === "reply" ? 40 : 18} />
      {i.signature && <p className="rp-sig-t is-sent">{i.signature}</p>}
      {i.kind === "answer" && i.error && <p className="rp-none">{tr(i.error)}</p>}
      {i.kind === "sent" && i.via === "campaign" && <Marks i={i} words />}
    </div>}
  </li>;
}

/** Which emails start open: the newest reply, the email of ours it answers (the latest one with words), and anything newer than the reply. */
function initiallyOpen(items: ThreadItem[]): Set<string> {
  const open = new Set<string>();
  const n = items.findIndex((i) => i.newest);
  if (n < 0) {
    if (items.length) open.add(items[items.length - 1].id);
    return open;
  }
  open.add(items[n].id);
  const before = items.slice(0, n).filter(isOurs);
  const answered = [...before].reverse().find((i) => i.text) ?? before[before.length - 1];
  if (answered) open.add(answered.id);
  items.slice(n + 1).forEach((i) => open.add(i.id));
  return open;
}

const FOLD = 9;             // a longer thread shows the first email, a fold, and the latest KEEP
const KEEP = 6;

/** The person page (and, `embedded`, inside another panel): every email between this person and us, oldest first, as a mail client shows a
 *  conversation, with the summary strip on top and the composer at the bottom. Everything comes from our database; Lemlist is asked again
 *  (read-only) only when some words are missing. Renders nothing when we hold no Lemlist email with them. */
export function EmailThread({ personId, name, embedded = false }: { personId: string; name: string; embedded?: boolean }) {
  const [t, setT] = useState<Thread | null>(null);
  const [failed, setFailed] = useState(false);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const hid = useId();
  const sec = useRef<HTMLElement>(null);
  useEffect(() => {
    let live = true;
    setT(null); setFailed(false); setAll(false);
    api<Thread>(`/api/people/${personId}/email-thread`).then((x) => {
      if (!live) return;
      setT(x); setOpen(initiallyOpen(x.items));
      const missing = x.items.some((i) => (i.kind === "reply" && i.text_state !== "full") || (i.kind === "sent" && !i.text));
      if (missing && x.items.length) post<Thread>(`/api/people/${personId}/email-thread/refresh`).then((y) => { if (live && y?.items) setT(y); }).catch(() => undefined);
    }).catch((e) => { if (live) setFailed(e instanceof ApiError && e.status >= 500); });
    return () => { live = false; };
  }, [personId]);
  // "Open conversation" from Today lands here
  useEffect(() => {
    if (t && window.location.hash === "#emails") requestAnimationFrame(() => sec.current?.scrollIntoView({ block: "start" }));
  }, [Boolean(t)]);
  const first = firstName(name);
  const sent = (x: Thread) => {
    setT(x);
    setOpen((o) => { const n = new Set(o); const last = [...x.items].reverse().find((i) => i.kind === "answer"); if (last) n.add(last.id); return n; });
  };
  const shown = useMemo(() => {
    if (!t) return [];
    if (all || t.items.length <= FOLD) return t.items.map((i) => ({ i, fold: 0 }));
    return [{ i: t.items[0], fold: 0 }, { i: null, fold: t.items.length - 1 - KEEP }, ...t.items.slice(-KEEP).map((i) => ({ i, fold: 0 }))];
  }, [t, all]);
  if (failed) return embedded ? <p className="rp-none">{tr("The emails could not be loaded. Reload the page to try again.")}</p>
    : <section className="ac-panel rp-thread" id="emails" aria-labelledby={hid}><h2 id={hid}>{tr("Emails with {}", first)}</h2>
      <p className="rp-none">{tr("The emails could not be loaded. Reload the page to try again.")}</p></section>;
  if (!t) return embedded ? <p className="rp-none" role="status">{tr("Loading the emails…")}</p> : null;
  if (t.items.length === 0) return embedded ? <p className="rp-none">{tr("No emails with {} are stored yet.", first)}</p> : null;
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const body = <>
    {t.summary && <Strip s={t.summary} />}
    <ol className="rp-list" aria-label={tr("Emails with {}, oldest first", first)}>
      {shown.map(({ i, fold }) => i ? <Message key={i.id} i={i} name={name} photo={t.person.photo_url} toName={t.person.name} open={open.has(i.id)} onToggle={() => toggle(i.id)}
        lemlistUrl={t.reply?.lemlist_url} />
        : <li key="fold" className="rp-fold"><button type="button" onClick={() => setAll(true)}><span>{counted(fold, ["earlier email", "earlier emails"], ["wcześniejszy e-mail", "wcześniejsze e-maile", "wcześniejszych e-maili"])}</span></button></li>)}
    </ol>
    {t.refresh?.error && <p className="rp-none">{tr(t.refresh.error)}</p>}
  </>;
  if (embedded) return <div className="rp-thread rp-embedded">{body}</div>;
  return <section ref={sec} className="ac-panel rp-thread" id="emails" aria-labelledby={hid}>
    <div className="rp-thread-h">
      <h2 id={hid}>{tr("Emails with {}", first)}</h2>
      {t.reply?.lemlist_url && <a className="rp-alt" href={t.reply.lemlist_url} target="_blank" rel="noopener noreferrer">{tr("Open in Lemlist")}{I.out}</a>}
    </div>
    <p className="ac-purpose">{tr("Every email between {} and us, oldest first. Click a line to open it. Your answer goes out in the same thread.", first)}</p>
    {body}
    {t.reply && <ReplyComposer personId={personId} personName={name} reply={t.reply} config={t} signature={t.signature} toEmail={t.person.email} onSent={sent} />}
  </section>;
}
