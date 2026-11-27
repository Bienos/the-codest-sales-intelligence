import { useEffect, useId, useState } from "react";
import { ApiError, api, put } from "../api";
import { tr } from "../locale";
import "../styles/replies.css";

/** The signature (footer) under a reply sent from this app. Lemlist adds none to an email sent through its inbox API, so each person keeps
 *  one here (plain text, up to 600 characters); the server appends it once under the words. Until it is saved, the server suggests one: the
 *  sign-off of our own campaign emails, else name, The Codest and the website. Shown in the composer (with "Edit signature") and on
 *  My connections. */

export type Signature = { text: string; saved: boolean; source: "saved" | "campaign" | "default"; from: string | null; max: number; lemlist_adds: boolean;
  updated_at: string | null };

/* One signature per signed-in person: what was last saved is shared by every box on the page. A suggestion depends on the person answered
   (the mailbox that answers them), so it is kept per person until something is saved. */
let saved: Signature | null = null;
const suggested = new Map<string, Promise<Signature | null>>();
const listeners = new Set<(s: Signature) => void>();
function announce(s: Signature) {
  saved = s;
  listeners.forEach((f) => f(s));
}

/** The signature under replies to this person. `initial`: what the thread already carried (no second request). */
export function useSignature(personId?: string | null, initial?: Signature | null, enabled = true): [Signature | null, (s: Signature) => void] {
  const [sig, setSig] = useState<Signature | null>(() => saved ?? initial ?? null);
  useEffect(() => {
    const on = (s: Signature) => setSig(s);
    listeners.add(on);
    return () => { listeners.delete(on); };
  }, []);
  useEffect(() => {
    if (saved) { setSig(saved); return; }
    if (initial) {
      setSig(initial);
      if (initial.saved) saved = initial;
      return;
    }
    if (!enabled) return;
    let live = true;
    const k = personId ?? "";
    if (!suggested.has(k)) {
      suggested.set(k, api<Signature>(`/api/me/email-signature${personId ? `?person=${encodeURIComponent(personId)}` : ""}`).catch(() => {
        suggested.delete(k);
        return null;
      }));
    }
    suggested.get(k)!.then((s) => {
      if (!live || !s) return;
      if (s.saved) saved = s;
      setSig(saved ?? s);
    });
    return () => { live = false; };
  }, [personId, initial, enabled]);
  return [sig, announce];
}

/** The small editor: plain text, a counter, Save and Cancel. Saving replaces the signature under every reply this person sends. */
export function SignatureEditor({ value, onDone, onSaved }: { value: Signature; onDone?: () => void; onSaved: (s: Signature) => void }) {
  const [text, setText] = useState(value.text);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const id = useId();
  const max = value.max || 600;
  const save = async () => {
    setBusy(true); setErr(null); setOk(false);
    try {
      const s = await put<Signature>("/api/me/email-signature", { text });
      setText(s.text);
      onSaved(s);
      setOk(true);
      onDone?.();
    } catch (e) {
      setErr(e instanceof ApiError && e.status < 500 && e.message ? tr(e.message) : tr("The signature could not be saved. Try again in a minute."));
    } finally {
      setBusy(false);
    }
  };
  return <div className="es-edit">
    <label htmlFor={id}>{tr("Your signature")}</label>
    <textarea id={id} value={text} rows={4} maxLength={max} spellCheck onChange={(e) => { setText(e.target.value); setOk(false); }}
      aria-describedby={`${id}-h`} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void save(); } }} />
    <p className="es-hint" id={`${id}-h`}>
      <span>{tr("Plain text, line breaks kept, no pictures. Leave it empty to send replies without one.")}</span>
      <span className={`es-count${text.length > max - 40 ? " is-near" : ""}`}>{text.length}/{max}</span>
    </p>
    <div className="es-acts">
      <button type="button" className="es-save" disabled={busy} onClick={() => void save()}>{busy ? tr("Saving…") : tr("Save signature")}</button>
      {onDone && <button type="button" className="linkish" onClick={onDone}>{tr("Cancel")}</button>}
      {ok && !onDone && <span className="es-ok" role="status">{tr("Saved.")}</span>}
    </div>
    {err && <p className="err es-err" role="alert">{err}</p>}
  </div>;
}

/** Where the signature came from, in one short line. */
export function signatureSource(s: Signature): string {
  if (s.lemlist_adds) return tr("Lemlist adds your signature to every reply.");
  if (s.saved) return s.text ? tr("Added under every reply you send from here.") : tr("No signature: replies go out with your words only.");
  return s.source === "campaign" ? tr("Suggested from the sign-off of your campaign emails. Save it to keep it.")
    : tr("Suggested: your name, The Codest and the website. Save it to keep it.");
}

/** My connections: the signature section. */
export function SignatureSection() {
  const [sig, setSaved] = useSignature(null);
  const hid = useId();
  return <section className="es-card" aria-labelledby={hid}>
    <header className="es-card-h">
      <span className="es-glyph" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square"><path d="M3 17c3-1 4-9 7-9 2 0-1 9 2 9 2 0 2-3 4-3s1 3 5 3" /><path d="M3 21h18" /></svg></span>
      <h2 id={hid}>{tr("Email signature")}</h2>
    </header>
    <p className="es-for">{tr("The footer under every reply you send from this app. Lemlist does not add one to these replies, so it is kept here.")}</p>
    {!sig ? <p className="es-hint" role="status">{tr("Loading")}…</p> : <>
      {!sig.saved && <p className="es-src">{signatureSource(sig)}</p>}
      <SignatureEditor value={sig} onSaved={setSaved} />
    </>}
  </section>;
}
