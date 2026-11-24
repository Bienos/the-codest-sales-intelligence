import { counted, tr } from "../locale";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage, Tabs } from "../components/ui";
import Library from "./Library";

type Tab = "signals" | "sources" | "urls";

function Strength({ n }: { n: number }) {
  return <span className="strength" role="img" aria-label={tr("Strength {} of 3", n)}>{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;
}

function Signals({ q }: { q: string }) {
  const [group, setGroup] = useState(""); const [status, setStatus] = useState("");
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/library/signals?q=${encodeURIComponent(q)}&group=${encodeURIComponent(group)}&status=${status}`).then(setD).catch(setErr); }, [q, group, status]);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  return (
    <>
      <div className="row mb-3">
        <select aria-label={tr("Signal group")} value={group} onChange={(e) => setGroup(e.target.value)}><option value="">{tr("All groups")}</option>{d.groups.map((g: string) => <option key={g} value={g}>{tr(g)}</option>)}</select>
        <select aria-label={tr("Status")} value={status} onChange={(e) => setStatus(e.target.value)}><option value="">{tr("Any status")}</option><option value="active">{tr("On: detected automatically")}</option><option value="defined_not_activated">{tr("Not yet on")}</option></select>
        <span className="small muted">{counted(d.items.length, ["signal", "signals"], ["sygnał", "sygnały", "sygnałów"])}</span>
      </div>
      <p className="small muted">{tr("The 64 buying signals we look for, in their original wording.") + " "}<b>{tr("On")}</b>{" " + tr("means we detect it automatically from checked findings.") + " "}<b>{tr("Not yet on")}</b>{" " + tr("means it is written down and waiting for a source or a test. Nothing is switched on until it has been shown to work.") + " "}<b>{tr("Strength")}</b>{" " + tr("is how strongly the signal suggests outside engineering work, from 1 to 3.")}</p>
      <div className="sigs"><div className="small muted">{tr("Signal · Strength · Status")}</div>{d.items.map((s: any) => (
        <details key={s.rule_id} className="sigrow">
          <summary>
            <span className="sigrow-id">{s.rule_id}</span>
            <span className="sigrow-t">{s.signal}</span>
            <span className="sigrow-m"><Strength n={s.strength} /><Badge kind={s.status === "active" ? "ok" : ""}>{s.status === "active" ? tr("On") : tr("Not yet on")}</Badge></span>
          </summary>
          <dl className="lib-facts wide">
            <dt>{tr("Group")}</dt><dd>{s.grp}</dd>
            <dt>{tr("Stays relevant for")}</dt><dd>{s.fresh_for_days}{" " + tr("days")}{s.window_note ? `. ${s.window_note}` : ""}</dd>
            <dt>{tr("How to spot it")}</dt><dd>{s.how_to_spot ?? tr("Not recorded")}</dd>
            <dt>{tr("Who to contact")}</dt><dd>{s.who_to_contact ?? tr("Not recorded")}</dd>
            <dt>{tr("Check it is genuine")}</dt><dd>{s.check_it_is_real ?? tr("Not recorded")}</dd>
            {s.pairs_best_with && <><dt>{tr("Pairs best with")}</dt><dd>{s.pairs_best_with}</dd></>}
            {s.stop_effect && <><dt>{tr("What cancels it")}</dt><dd>{s.stop_effect}</dd></>}
            <dt>{tr("Comes from")}</dt><dd>{s.fed_by?.length ? s.fed_by.map((f: string) => <span key={f} className="pill">{f}</span>) : <span className="muted">{tr("No source connected yet")}</span>}</dd>
          </dl>
        </details>))}
      </div>
    </>
  );
}

function Urls({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { const t = setTimeout(() => api(`/api/library/urls?q=${encodeURIComponent(q)}&limit=80`).then(setD).catch(setErr), q ? 250 : 0); return () => clearTimeout(t); }, [q]);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  return (
    <>
      <div className="section-title mt-0">{tr("Pages and feeds we watch")}</div>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Source · Tier · Status")}><table><thead><tr><th>{tr("Source")}</th><th>{tr("Tier")}</th><th>{tr("Status")}</th><th>{tr("Address")}</th></tr></thead><tbody>
        {d.feeds.filter((f: any) => !q || `${f.display_name} ${f.url}`.toLowerCase().includes(q.toLowerCase())).map((f: any) => (
          <tr key={f.source_id}><td>{f.display_name}<div className="small muted">{humanize(f.collection_method)}</div></td><td>{f.source_tier}</td><td><Badge kind={f.status === "active" ? "ok" : "warn"}>{f.status === "active" ? tr("Live") : tr("Not set up yet")}</Badge></td>
            <td className="small"><a href={f.url} target="_blank" rel="noreferrer noopener">{f.url.replace(/^https?:\/\//, "").slice(0, 70)} ↗</a>{f.basis && <div className="muted">{f.basis}</div>}</td></tr>))}
      </tbody></table></div>
      <div className="section-title">{tr("Websites your workbooks cite most")}</div>
      <div className="chips">{d.domains.map((x: any) => <button key={x.domain} className="chip" onClick={() => setQ(x.domain)}>{x.domain} <b>{x.count}</b></button>)}</div>
      <div className="section-title">{tr("Every link in your workbooks")}</div>
      <p className="small muted">{tr("LinkedIn links are left out on purpose. Open the original and read it before you act.")}</p>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Link · Account · Workbook column")}><table><thead><tr><th>Link</th><th>{tr("Account")}</th><th>{tr("Workbook column")}</th></tr></thead><tbody>
        {d.items.map((u: any) => <tr key={u.url}><td className="small"><a href={u.url} target="_blank" rel="noreferrer noopener">{u.url.replace(/^https?:\/\//, "").slice(0, 80)} ↗</a></td><td>{u.organization}</td><td className="small muted">{u.field}</td></tr>)}
        {d.items.length === 0 && <tr><td colSpan={3} className="muted">{tr("No cited URLs match.")}</td></tr>}</tbody></table></div>
    </>
  );
}

export default function LibraryHub() {
  const [sp, setSp] = useSearchParams();
  const requested = sp.get("tab");
  const tab: Tab = requested === "signals" || requested === "urls" ? requested : "sources";
  const q = sp.get("q") ?? "";
  const set = (next: Record<string, string>) => setSp({ tab, ...(q ? { q } : {}), ...next });
  return (
    <>
      <div className="page-head"><div className="eyebrow">{tr("Reference")}</div><h1>{tr("Library")}</h1></div>
      <Tabs label={tr("Library sections")} items={[["signals", "Signals"], ["sources", "Sources"], ["urls", "Pages and feeds"]].map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => set({ tab: k })}>
        <input aria-label={tr("Filter by keyword")} placeholder={tr("Filter by keyword")} value={q} onChange={(e) => setSp({ tab, ...(e.target.value ? { q: e.target.value } : {}) })} className="field-md" />
      <div className="mt-4">
        {tab === "signals" && <Signals q={q} />}
        {tab === "sources" && <Library render={() => null} query={q} canSetup={false} />}
        {tab === "urls" && <Urls q={q} setQ={(v) => set({ q: v })} />}
      </div>
      </Tabs>
    </>
  );
}
