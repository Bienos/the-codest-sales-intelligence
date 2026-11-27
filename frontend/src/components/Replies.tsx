import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api, post } from "../api";
import { counted, getLanguage, tr } from "../locale";
import "../styles/replies.css";

/** What someone wrote back to our Lemlist campaign, and a box to answer in the same email thread.
 *  Used on Today (one reply) and on the person page (the whole thread). The server decides everything that matters: whether sending is
 *  switched on, whether this person may be written to, which mailbox answers. This file only shows it in plain words. */

export type TextState = "full" | "preview" | "pending" | "missing";
export type ReplyInfo = {
  activity_id: string; at: string | null; campaign: string | null; subject: string | null; text: string | null; text_state: TextState;
  lemlist_url: string | null; sender: { name: string | null; email: string } | null; blocked: string | null;
  answered: { status: string; at: string; by: string } | null;
};
type Config = { sending_on: boolean; lemlist_connected: boolean; may_send: boolean };
type Who = { name: string | null; email?: string | null } | null;
type Engagement = { opens: number; last_open: string | null; clicks: number; last_click: string | null; bounces: number; last_bounce: string | null };
/** One email of the thread. `via`: a campaign email, an answer sent in the Lemlist inbox, or an answer sent from this app. `step` counts from 1. */
type ThreadItem = {
  id: string; kind: "sent" | "reply" | "answer"; via?: "campaign" | "lemlist" | "app"; at: string | null; campaign?: string | null; step?: number | null;
  subject?: string | null; text: string | null; text_state: TextState; from?: Who; to?: Who; engagement?: Engagement; newest?: boolean;
  status?: string; error?: string | null; by?: string; by_name?: string | null;
};
type Summary = { sent: number; first_sent: string | null; last_sent: string | null; opens: number; clicks: number; bounced: boolean;
  replies: number; last_reply: string | null; answers: number; last_answer: string | null };
type Thread = Config & { person: { id: string; name: string; do_not_contact: boolean }; items: ThreadItem[]; summary?: Summary; reply: ReplyInfo | null;
  refresh?: { refreshed: boolean; error?: string } };

/** The server's reasons (ri.outbound.replies.BLOCKED), in the words shown here. */
const BLOCKED: Record<string, string> = {
  do_not_contact: "This person is on the do-not-contact list, so nothing can be sent to them.",
  no_activity_id: "Lemlist has not given us the id of this email, so a reply cannot be threaded from here. Answer in Lemlist.",
  no_mailbox: "Lemlist has not told us which mailbox sent the campaign, so a reply cannot be sent from here yet. Answer in Lemlist.",
  no_contact: "Lemlist has not told us who this contact is, so a reply cannot be sent from here yet. Answer in Lemlist.",
};

const loc = () => (getLanguage() === "pl" ? "pl-PL" : "en-GB");
function when(v?: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  return isNaN(d.getTime()) ? "" : d.toLocaleString(loc(), { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
/** "26 Nov" (with the year when it is not this year). */
function day(v?: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(loc(), { day: "numeric", month: "short", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) });
}
const sameDay = (a?: string | null, b?: string | null) => Boolean(a && b) && new Date(a!).toDateString() === new Date(b!).toDateString();
const firstName = (name: string) => name.trim().split(/\s+/)[0] || name;
/** One line of an email for a list row: the first words, without line breaks. */
export function firstWords(text: string, max = 90): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).replace(/\s+\S*$/, "")}…`;
}
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`);

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

/** The words of an email: the first four lines, and a button for the rest. When Lemlist has not sent the words, says so plainly. */
export function ReplyText({ text, state, lemlistUrl, lines = 4 }: { text: string | null; state: TextState; lemlistUrl?: string | null; lines?: number }) {
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
    <div className="rp-quote"><p ref={ref} className={`rp-text${open ? " open" : ""}`} style={{ "--rp-lines": lines } as React.CSSProperties}>{text}</p></div>
    {(over || open) && <button type="button" className="linkish rp-more" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? tr("Show less") : tr("Show the whole email")}</button>}
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

type Note = { kind: "ok" | "err"; text: string } | null;

/** A plain-text answer that Lemlist sends in the same thread, from the mailbox that sent the campaign. One key per draft: a double
 *  click (or a retry after a lost answer) never sends twice. The box is always shown; when sending is not possible it says why. */
export function ReplyBox({ personId, personName, reply, config, onSent, autoFocus = false }: { personId: string; personName: string; reply: ReplyInfo;
  config: Config | null; onSent?: (t: Thread) => void; autoFocus?: boolean }) {
  const [text, setText] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const id = useId();
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!autoFocus || !box.current) return;
    box.current.focus({ preventScroll: true });
    box.current.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [autoFocus]);
  const reason = whyNot(config, reply);
  const canSend = Boolean(config) && !reason && !busy && text.trim().length > 0;
  const send = async () => {
    if (!canSend) return;
    setBusy(true); setNote(null);
    try {
      const r = await post<{ status: string; repeat: boolean; thread?: Thread }>(`/api/people/${personId}/email-thread/reply`, { activity_id: reply.activity_id, text, key });
      setText(""); setKey(newKey());
      setNote({ kind: "ok", text: r.repeat ? tr("This reply was already sent. It was not sent again.") : tr("Sent. Lemlist sends it in the same email thread.") });
      if (r.thread) onSent?.(r.thread);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "";
      if (e instanceof ApiError && e.status === 502) setKey(newKey());       // no clear answer from Lemlist: the person checks there, then may send again
      setNote({ kind: "err", text: msg && !/^(Internal Server Error|Bad Gateway)$/i.test(msg) ? tr(msg) : tr("The reply could not be sent. Nothing was sent. Try again in a minute.") });
    } finally {
      setBusy(false);
    }
  };
  const from = reply.sender ? (reply.sender.name ? `${reply.sender.name} (${reply.sender.email})` : reply.sender.email) : null;
  return <div className="rp-box">
    <label htmlFor={id}>{tr("Your reply to {}", firstName(personName))}</label>
    <textarea id={id} ref={box} value={text} rows={5} maxLength={5000} onChange={(e) => { setText(e.target.value); if (note?.kind === "ok") setNote(null); }}
      placeholder={tr("Write in plain text. Lemlist keeps the subject and the earlier emails.")} aria-describedby={`${id}-h`} />
    <p className="rp-hint" id={`${id}-h`}>
      {reason ?? (from ? tr("Sent from {}, in the same email thread.", from) : tr("Sent from the mailbox that sent the campaign, in the same email thread."))}
    </p>
    <div className="rp-box-acts">
      <button type="button" className="primary" disabled={!canSend} aria-disabled={!canSend} onClick={() => void send()}>{busy ? tr("Sending…") : tr("Send reply")}</button>
      {reason && reply.lemlist_url && <a className="rp-alt" href={reply.lemlist_url} target="_blank" rel="noopener noreferrer">{tr("Answer in Lemlist")} ↗</a>}
    </div>
    {note && <p className={note.kind === "ok" ? "okmsg rp-note" : "err rp-note"} role={note.kind === "ok" ? "status" : "alert"}>{note.text}</p>}
  </div>;
}

const FEW = 8;         // a longer thread shows its latest SHOWN emails and a button for the earlier ones
const SHOWN = 6;
const times = (n: number) => (n === 1 ? tr("once") : counted(n, ["time", "times"], ["raz", "razy", "razy"]));

/** "3 emails sent from 21 Nov to 26 Nov, opened 4 times, replied on 26 Nov." from the server's counts. */
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

const who = (w?: Who) => (w ? (w.name && w.email ? `${w.name} (${w.email})` : w.name || w.email || "") : "");

/** Opens, clicks and a bounce under one of our campaign emails. */
function EngagementLine({ g }: { g?: Engagement }) {
  if (!g) return null;
  const chips: { k: string; text: string; warn?: boolean }[] = [];
  if (g.bounces > 0) chips.push({ k: "b", text: tr("Bounced on {}", day(g.last_bounce)), warn: true });
  if (g.opens > 0) chips.push({ k: "o", text: g.opens === 1 ? tr("Opened on {}", day(g.last_open)) : tr("Opened {}, last on {}", times(g.opens), day(g.last_open)) });
  if (g.clicks > 0) chips.push({ k: "c", text: g.clicks === 1 ? tr("Clicked a link on {}", day(g.last_click)) : tr("Clicked links {}, last on {}", times(g.clicks), day(g.last_click)) });
  if (!chips.length) return <p className="rp-eng-none">{tr("No opens recorded")}</p>;
  return <ul className="rp-eng" aria-label={tr("What happened to this email")}>{chips.map((c) => <li key={c.k} className={c.warn ? "is-warn" : undefined}>{c.text}</li>)}</ul>;
}

/** Who wrote it, in a few words: "We wrote", "Ola wrote", "We answered from here". */
function direction(i: ThreadItem, first: string): string {
  if (i.kind === "reply") return tr("{} wrote", first);
  if (i.kind === "answer") return i.status === "sent" ? tr("We answered from here") : i.status === "failed" ? tr("Our answer, not sent") : tr("Our answer, not confirmed");
  return i.via === "lemlist" ? tr("We answered in Lemlist") : tr("We wrote");
}

/** One email of the thread, on its own card. Our campaign emails without words are one slim line. */
function EmailCard({ i, first, lemlistUrl }: { i: ThreadItem; first: string; lemlistUrl?: string | null }) {
  const from = who(i.from);
  const to = who(i.to);
  if (i.kind === "sent" && !i.text) {
    const d = when(i.at);
    const line = i.step
      ? (from ? tr("Email {} of the sequence sent on {} from {}", i.step, d, from) : tr("Email {} of the sequence sent on {}", i.step, d))
      : (from ? tr("Campaign email sent on {} from {}", d, from) : tr("Campaign email sent on {}", d));
    return <li className="rp-item rp-slim">
      <p className="rp-slim-l">{line}{i.campaign && <span className="rp-slim-c"> · {tr("Campaign “{}”", i.campaign)}</span>}</p>
      <EngagementLine g={i.engagement} />
    </li>;
  }
  const meta = [i.campaign && tr("Campaign “{}”", i.campaign), i.step && tr("Email {} of the sequence", i.step)].filter(Boolean).join(" · ");
  const cls = i.kind === "reply" ? "rp-theirs" : i.kind === "answer" ? `rp-ours rp-app${i.status !== "sent" ? " is-off" : ""}` : "rp-ours";
  return <li className={`rp-item rp-card ${cls}${i.newest ? " rp-newest" : ""}`}>
    <div className="rp-card-h">
      <b className="rp-dir">{direction(i, first)}</b>
      {i.newest && <span className="rp-tag">{tr("Newest reply")}</span>}
      {i.at && <time dateTime={i.at}>{when(i.at)}</time>}
    </div>
    {(from || to) && <p className="rp-from">{from && to ? tr("From {} to {}", from, to) : from ? tr("From {}", from) : tr("To {}", to)}</p>}
    {i.subject && <p className="rp-subj">{i.subject}</p>}
    {meta && <p className="rp-meta">{meta}</p>}
    <ReplyText text={i.text} state={i.text_state} lemlistUrl={i.kind === "reply" ? lemlistUrl : null} lines={i.kind === "sent" ? 4 : 12} />
    {i.kind === "answer" && i.error && <p className="rp-none">{tr(i.error)}</p>}
    {i.kind === "sent" && i.via === "campaign" && <EngagementLine g={i.engagement} />}
  </li>;
}

/** The person page (and, `embedded`, the Today reply): every email between this person and us, each on its own card, oldest first, with a
 *  one-line summary on top and the reply box at the bottom. Everything comes from our database; Lemlist is asked again (read-only) only when
 *  some words are missing. Renders nothing when we hold no Lemlist email with them. */
export function EmailThread({ personId, name, embedded = false }: { personId: string; name: string; embedded?: boolean }) {
  const [t, setT] = useState<Thread | null>(null);
  const [failed, setFailed] = useState(false);
  const [all, setAll] = useState(false);
  const hid = useId();
  useEffect(() => {
    let live = true;
    setT(null); setFailed(false); setAll(false);
    api<Thread>(`/api/people/${personId}/email-thread`).then((x) => {
      if (!live) return;
      setT(x);
      const missing = x.items.some((i) => (i.kind === "reply" && i.text_state !== "full") || (i.kind === "sent" && !i.text));
      if (missing && x.items.length) post<Thread>(`/api/people/${personId}/email-thread/refresh`).then((y) => { if (live && y?.items) setT(y); }).catch(() => undefined);
    }).catch((e) => { if (live) setFailed(e instanceof ApiError && e.status >= 500); });
    return () => { live = false; };
  }, [personId]);
  const first = firstName(name);
  if (failed) return embedded ? <p className="rp-none">{tr("The emails could not be loaded. Reload the page to try again.")}</p>
    : <section className="ac-panel rp-thread" id="emails" aria-labelledby={hid}><h2 id={hid}>{tr("Emails with {}", first)}</h2>
      <p className="rp-none">{tr("The emails could not be loaded. Reload the page to try again.")}</p></section>;
  if (!t) return embedded ? <p className="rp-none" role="status">{tr("Loading the emails…")}</p> : null;
  if (t.items.length === 0) return embedded ? <p className="rp-none">{tr("No emails with {} are stored yet.", first)}</p> : null;
  const long = t.items.length > FEW;
  const items = long && !all ? t.items.slice(-SHOWN) : t.items;
  const sum = t.summary ? summaryLine(t.summary) : "";
  const body = <>
    {sum && <p className="rp-sum">{sum}</p>}
    {long && <button type="button" className="linkish rp-earlier" aria-expanded={all} onClick={() => setAll(!all)}>
      {all ? tr("Show only the latest") : tr("Show {} earlier emails", t.items.length - SHOWN)}</button>}
    <ol className="rp-list" aria-label={tr("Emails with {}, oldest first", first)}>
      {items.map((i) => <EmailCard key={i.id} i={i} first={first} lemlistUrl={t.reply?.lemlist_url} />)}
    </ol>
    {t.refresh?.error && <p className="rp-none">{tr(t.refresh.error)}</p>}
  </>;
  if (embedded) return <div className="rp-thread rp-embedded">{body}</div>;
  return <section className="ac-panel rp-thread" id="emails" aria-labelledby={hid}>
    <h2 id={hid}>{tr("Emails with {}", first)}</h2>
    <p className="ac-purpose">{tr("Every email between {} and us, oldest first, each on its own. Answer at the bottom: it goes out in the same email thread.", first)}</p>
    {body}
    {t.reply && <ReplyBox personId={personId} personName={name} reply={t.reply} config={t} onSent={setT} />}
  </section>;
}
