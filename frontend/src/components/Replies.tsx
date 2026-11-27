import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api, post } from "../api";
import { getLanguage, tr } from "../locale";
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
type ThreadItem = {
  id: string; kind: "sent" | "reply" | "answer"; at: string | null; campaign?: string | null; subject?: string | null; text: string | null; text_state: TextState;
  from?: { name: string | null; email: string | null } | null; status?: string; error?: string | null; by?: string;
};
type Thread = Config & { person: { id: string; name: string; do_not_contact: boolean }; items: ThreadItem[]; reply: ReplyInfo | null;
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

const SHOWN = 6;

/** The person page: the emails between this person and us in Lemlist campaigns, oldest first, and the reply box under their newest reply.
 *  Renders nothing when we hold no Lemlist email with them. Asks Lemlist again (read-only) when a reply's words are missing. */
export function EmailThread({ personId, name }: { personId: string; name: string }) {
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
  if (failed) return <section className="ac-panel rp-thread" aria-labelledby={hid}><h2 id={hid}>{tr("Emails with {}", firstName(name))}</h2>
    <p className="rp-none">{tr("The emails could not be loaded. Reload the page to try again.")}</p></section>;
  if (!t || t.items.length === 0) return null;
  const first = firstName(name);
  const items = all ? t.items : t.items.slice(-SHOWN);
  const head = (i: ThreadItem) => i.kind === "reply" ? tr("{} wrote", first)
    : i.kind === "answer" ? (i.status === "sent" ? tr("Our answer, sent from here") : i.status === "failed" ? tr("Our answer, not sent") : tr("Our answer, not confirmed"))
      : tr("Our campaign email");
  return <section className="ac-panel rp-thread" aria-labelledby={hid}>
    <h2 id={hid}>{tr("Emails with {}", first)}</h2>
    <p className="ac-purpose">{tr("Our campaign emails to {} and what they wrote back. Answer at the bottom: it goes out in the same email thread.", first)}</p>
    {t.items.length > SHOWN && <button type="button" className="linkish rp-earlier" aria-expanded={all} onClick={() => setAll(!all)}>
      {all ? tr("Show only the latest") : tr("Show {} earlier emails", t.items.length - SHOWN)}</button>}
    <ol className="rp-list">{items.map((i) => <li key={i.id} className={`rp-msg rp-${i.kind}${i.kind === "answer" && i.status !== "sent" ? " is-off" : ""}`}>
      <div className="rp-msg-h">
        <b>{head(i)}</b>
        {i.at && <time dateTime={i.at}>{when(i.at)}</time>}
      </div>
      {(i.subject || i.campaign) && <p className="rp-msg-s">{[i.subject, i.campaign && tr("Campaign: {}", i.campaign)].filter(Boolean).join(" · ")}</p>}
      {i.kind === "sent" && !i.text ? <p className="rp-none">{tr("The words of this email are kept in Lemlist.")}</p>
        : <ReplyText text={i.text} state={i.text_state} lemlistUrl={i.kind === "reply" ? t.reply?.lemlist_url : null} />}
      {i.kind === "answer" && i.error && <p className="rp-none">{tr(i.error)}</p>}
    </li>)}</ol>
    {t.refresh?.error && <p className="rp-none">{tr(t.refresh.error)}</p>}
    {t.reply && <ReplyBox personId={personId} personName={name} reply={t.reply} config={t} onSent={setT} />}
  </section>;
}
