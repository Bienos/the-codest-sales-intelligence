import { useEffect, useState } from "react";
import { api, post } from "../api";
import { tr } from "../locale";
import "../styles/feedback.css";

/** Was this useful? One answer per person per item (Today and Buying signals share the item key). "No" asks why, in one tap.
 *  The answer never hides the item: it tells the Library which rules sellers find useful, and why not. */
export const FEEDBACK_REASONS: [string, string][] = [
  ["wrong_company", "Wrong company"], ["out_of_date", "Out of date"], ["not_our_work", "Not our kind of work"],
  ["ghost_job", "Job ad not real or already filled"], ["already_talking", "Already talking to them"], ["no_real_need", "No real need behind it"], ["other", "Other"],
];
export const reasonLabel = (k: string) => tr(FEEDBACK_REASONS.find(([key]) => key === k)?.[1] ?? k);
/** Keys the server can place: a platform signal or tender finding, an official tender, a lead-list row or a regulatory deadline. */
export const canRate = (key?: string | null) => /^(signal|tender|list|deadline):/.test(key ?? "");

type Answer = { useful: boolean | null; reason: string | null };

export function Feedback({ itemKey, shownOn }: { itemKey: string; shownOn: "today" | "signals" | "account" | "feed" }) {
  const [a, setA] = useState<Answer>({ useful: null, reason: null });
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setA({ useful: null, reason: null }); setAsking(false); setFailed(false);
    api(`/api/feedback/mine?keys=${encodeURIComponent(itemKey)}`).then((r) => { const x = r.items?.[itemKey]; if (x) setA({ useful: x.useful, reason: x.reason }); }).catch(() => undefined);
  }, [itemKey]);
  const send = async (useful: boolean, reason: string | null = null) => {
    setBusy(true); setFailed(false);
    try { await post("/api/feedback", { key: itemKey, useful, reason, shown_on: shownOn }); setA({ useful, reason }); setAsking(false); }
    catch { setFailed(true); } finally { setBusy(false); }
  };
  const undo = async () => {
    setBusy(true); setFailed(false);
    try { await api(`/api/feedback?key=${encodeURIComponent(itemKey)}`, { method: "DELETE" }); setA({ useful: null, reason: null }); }
    catch { setFailed(true); } finally { setBusy(false); }
  };
  return <div className="sfb" role="group" aria-label={tr("Was this useful?")}>
    {a.useful === null ? <>
      <span className="sfb-q">{tr("Was this useful?")}</span>
      <button type="button" className="sfb-btn" disabled={busy} onClick={() => void send(true)}>{tr("Yes")}</button>
      <button type="button" className="sfb-btn" disabled={busy} aria-expanded={asking} onClick={() => setAsking((v) => !v)}>{tr("No")}</button>
    </> : <>
      <span className="sfb-q">{a.useful ? tr("You found this useful.") : tr("Not useful: {}.", reasonLabel(a.reason ?? "other"))}</span>
      <button type="button" className="sfb-undo" disabled={busy} onClick={() => void undo()}>{tr("Undo")}</button>
    </>}
    {asking && a.useful === null && <div className="sfb-why" role="group" aria-label={tr("Why not?")}>
      <span className="sfb-q">{tr("Why not?")}</span>
      {FEEDBACK_REASONS.map(([k, label]) => <button key={k} type="button" className="sfb-chip" disabled={busy} onClick={() => void send(false, k)}>{tr(label)}</button>)}
    </div>}
    {failed && <span className="sfb-err" role="alert">{tr("Could not save. Try again.")}</span>}
  </div>;
}
