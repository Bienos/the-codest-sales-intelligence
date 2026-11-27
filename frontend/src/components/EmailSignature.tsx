import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, post, put } from "../api";
import { tr } from "../locale";
import "../styles/replies.css";

/** The signature (footer) under a reply sent from this app. Lemlist adds none to an email sent through its inbox API, so each person keeps
 *  one here (plain text, up to 600 characters); the server appends it once under the words. Until it is saved, the server suggests one: the
 *  sign-off of our own campaign emails, else name, The Codest and the website. Shown in the composer (with "Edit signature") and on
 *  My connections. */

export type Signature = { text: string; html?: string | null; saved: boolean; source: "saved" | "campaign" | "default" | "gmail" | "pasted"; from: string | null;
  max: number; lemlist_adds: boolean; updated_at: string | null; font_max_px?: number };
type Cleaned = { html: string; text: string; dropped_images: number };

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

/** The signature as it will look under the words: Arial, 12 px at most, like a normal Gmail email. `html` was cleaned to a short list of safe tags
 *  by the server (ri.outbound.signature), so it can be shown as it is; a click on a link opens it in a new tab instead of leaving the app. */
export function SignaturePreview({ html, text, className = "" }: { html?: string | null; text: string; className?: string }) {
  if (!html) return text ? <p className={`rp-sig-t ${className}`}>{text}</p> : null;
  return <div className={`es-rich ${className}`} aria-label={tr("Preview of the signature as it will be sent")}
    onClick={(e) => {
      const a = (e.target as HTMLElement).closest("a");
      if (a && a.getAttribute("href")) { e.preventDefault(); window.open(a.getAttribute("href")!, "_blank", "noopener,noreferrer"); }
    }}
    dangerouslySetInnerHTML={{ __html: html }} />;
}

/** The editor. Plain text in a box, or a formatted signature (logo, links, small print) pasted from Gmail or imported from it: the server cleans
 *  what is pasted and the preview shows the cleaned result before anything is saved. Saving replaces the signature under every reply. */
export function SignatureEditor({ value, onDone, onSaved }: { value: Signature; onDone?: () => void; onSaved: (s: Signature) => void }) {
  const [text, setText] = useState(value.text);
  const [rich, setRich] = useState<boolean>(Boolean(value.html));            // showing a formatted signature, not the plain-text box
  const [pending, setPending] = useState<{ raw: string; cleaned: Cleaned } | null>(null);   // pasted, cleaned, not saved yet
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [needGmail, setNeedGmail] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const id = useId();
  const max = value.max || 2000;
  const shown = pending ? { html: pending.cleaned.html, text: pending.cleaned.text } : rich && value.html ? { html: value.html, text: value.text } : null;
  const fail = (e: unknown, fallback: string) => setErr(e instanceof ApiError && e.status < 500 && e.message ? tr(e.message) : tr(fallback));

  const save = async () => {
    setBusy(true); setErr(null); setOk(false); setInfo(null);
    try {
      const s = await put<Signature>("/api/me/email-signature", pending ? { text: "", html: pending.raw } : { text });
      setText(s.text); setPending(null); setRich(Boolean(s.html));
      onSaved(s);
      setOk(true);
      onDone?.();
    } catch (e) {
      fail(e, "The signature could not be saved. Try again in a minute.");
    } finally {
      setBusy(false);
    }
  };
  const onPaste = async (e: React.ClipboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    setErr(null); setInfo(null); setOk(false); setNeedGmail(false);
    const raw = e.clipboardData.getData("text/html");
    const plain = e.clipboardData.getData("text/plain");
    if (!raw.trim()) {
      if (!plain.trim()) { setErr(tr("Nothing to paste: copy your signature in Gmail first.")); return; }
      setText(plain.slice(0, max)); setRich(false); setPending(null);
      setInfo(tr("That was plain text, so it is in the box. To keep your logo and links, copy the signature itself in Gmail."));
      return;
    }
    try {
      const cleaned = await post<Cleaned>("/api/me/email-signature/preview", { html: raw });
      if (!cleaned.text) { setErr(tr("The pasted signature had no words in it. Copy it again from Gmail.")); return; }
      setPending({ raw, cleaned }); setRich(true);
      setInfo(cleaned.dropped_images ? tr("Some pictures could not be kept: they are not hosted online. Not saved yet: press Save signature.") : tr("Not saved yet: press Save signature."));
    } catch (er) {
      fail(er, "The pasted signature could not be read. Try again in a minute.");
    }
  };
  const importGmail = async () => {
    setImporting(true); setErr(null); setInfo(null); setOk(false); setNeedGmail(false);
    try {
      const r = await post<Signature & { imported?: boolean; empty?: boolean; error?: string; address?: string; dropped_images?: number }>("/api/me/email-signature/import-gmail");
      if (r.imported) {
        setText(r.text); setPending(null); setRich(Boolean(r.html));
        onSaved(r);
        setInfo(tr("Imported from {}.", r.address ?? "Gmail") + (r.dropped_images ? ` ${tr("Some pictures could not be kept: they are not hosted online.")}` : ""));
      } else if (r.empty) setInfo(tr("Your Gmail has no signature set."));
      else setErr(tr(r.error ?? "The signature could not be read from Gmail."));
    } catch (e) {
      if (e instanceof ApiError && /^Connect your work Gmail first/.test(e.message)) setNeedGmail(true);
      fail(e, "The signature could not be read from Gmail. Try again in a minute.");
    } finally {
      setImporting(false);
    }
  };

  return <div className="es-edit">
    <label htmlFor={shown ? undefined : id}>{tr("Your signature")}</label>
    {shown ? <>
      <SignaturePreview html={shown.html} text={shown.text} />
      <p className="es-hint"><span>{tr("Sent in Arial, never larger than 12 px. Pictures must be hosted online.")}</span></p>
    </> : <>
      <textarea id={id} value={text} rows={4} maxLength={max} spellCheck onChange={(e) => { setText(e.target.value); setOk(false); }}
        aria-describedby={`${id}-h`} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void save(); } }} />
      <p className="es-hint" id={`${id}-h`}>
        <span>{tr("Plain text, line breaks kept. Leave it empty to send replies without one.")}</span>
        <span className={`es-count${text.length > max - 100 ? " is-near" : ""}`}>{text.length}/{max}</span>
      </p>
    </>}
    <div className="es-paste" contentEditable suppressContentEditableWarning role="textbox" aria-multiline="true" tabIndex={0} spellCheck={false}
      aria-label={tr("Paste your Gmail signature here")} data-placeholder={tr("To keep your logo, links and small print: copy your signature in Gmail, click here and paste it (Ctrl+V, or ⌘V on a Mac).")}
      onPaste={(e) => void onPaste(e)} onBeforeInput={(e) => e.preventDefault()} onDrop={(e) => e.preventDefault()} />
    <div className="es-acts">
      {(pending || !shown) && <button type="button" className="es-save" disabled={busy} onClick={() => void save()}>{busy ? tr("Saving…") : tr("Save signature")}</button>}
      <button type="button" className="linkish" disabled={importing} onClick={() => void importGmail()}>{importing ? tr("Importing…") : tr("Import from Gmail")}</button>
      {shown && <button type="button" className="linkish" onClick={() => { setRich(false); setPending(null); }}>{tr("Use plain text instead")}</button>}
      {onDone && <button type="button" className="linkish" onClick={onDone}>{tr("Cancel")}</button>}
      {ok && !onDone && <span className="es-ok" role="status">{tr("Saved.")}</span>}
    </div>
    {info && <p className="es-hint es-info" role="status">{info}</p>}
    {err && <p className="err es-err" role="alert">{err}{needGmail && <> <Link to="/connections">{tr("Open My connections")}</Link></>}</p>}
  </div>;
}

/** Where the signature came from, in one short line. */
export function signatureSource(s: Signature): string {
  if (s.lemlist_adds) return tr("Lemlist adds your signature to every reply.");
  if (s.saved) return s.text ? tr("Added under every reply you send from here.") : tr("No signature: replies go out with your words only.");
  if (s.saved && s.source === "gmail") return tr("Imported from your Gmail.");
  if (s.saved && s.source === "pasted") return tr("Pasted from your mail program.");
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
