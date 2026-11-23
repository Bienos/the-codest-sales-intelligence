import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Err, humanize } from "../components/ui";
import Library from "./Library";

type Tab = "signals" | "sources" | "urls";

function Strength({ n }: { n: number }) {
  return <span className="strength" role="img" aria-label={`Strength ${n} of 3`}>{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;
}

function Signals({ q }: { q: string }) {
  const [group, setGroup] = useState(""); const [status, setStatus] = useState("");
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/library/signals?q=${encodeURIComponent(q)}&group=${encodeURIComponent(group)}&status=${status}`).then(setD).catch(setErr); }, [q, group, status]);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <select aria-label="Signal group" value={group} onChange={(e) => setGroup(e.target.value)}><option value="">All groups</option>{d.groups.map((g: string) => <option key={g}>{g}</option>)}</select>
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="active">Active (machine-checked)</option><option value="defined_not_activated">Defined, not yet switched on</option></select>
        <span className="small muted">{d.items.length} signal{d.items.length === 1 ? "" : "s"}</span>
      </div>
      <p className="small muted">These are the 64 original signals, with the original wording kept. <b>Active</b> means the platform can detect it from verified events. <b>Defined</b> means it is written down and waiting for a source or a gold-set test. Nothing is switched on without evidence it works.</p>
      <div className="sigs">{d.items.map((s: any) => (
        <details key={s.rule_id} className="sigrow">
          <summary>
            <span className="sigrow-id">{s.rule_id}</span>
            <span className="sigrow-t">{s.signal}</span>
            <span className="sigrow-m"><Strength n={s.strength} /><Badge kind={s.status === "active" ? "ok" : ""}>{s.status === "active" ? "Active" : "Defined"}</Badge></span>
          </summary>
          <dl className="lib-facts" style={{ gridTemplateColumns: "150px 1fr" }}>
            <dt>Group</dt><dd>{s.grp}</dd>
            <dt>Fresh for</dt><dd>{s.fresh_for_days} days{s.window_note ? `. ${s.window_note}` : ""}</dd>
            <dt>How to spot it</dt><dd>{s.how_to_spot ?? "Not recorded"}</dd>
            <dt>Who to contact</dt><dd>{s.who_to_contact ?? "Not recorded"}</dd>
            <dt>Check it is real</dt><dd>{s.check_it_is_real ?? "Not recorded"}</dd>
            {s.pairs_best_with && <><dt>Pairs best with</dt><dd>{s.pairs_best_with}</dd></>}
            {s.stop_effect && <><dt>If it stops</dt><dd>{s.stop_effect}</dd></>}
            <dt>Fed by</dt><dd>{s.fed_by?.length ? s.fed_by.map((f: string) => <span key={f} className="pill" style={{ marginRight: 4 }}>{f}</span>) : <span className="muted">no source wired yet</span>}</dd>
          </dl>
        </details>))}
      </div>
    </>
  );
}

function Urls({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { const t = setTimeout(() => api(`/api/library/urls?q=${encodeURIComponent(q)}&limit=80`).then(setD).catch(setErr), q ? 250 : 0); return () => clearTimeout(t); }, [q]);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  return (
    <>
      <div className="section-title" style={{ marginTop: 0 }}>Pages and feeds the platform watches</div>
      <div className="card table-wrap"><table><thead><tr><th>Source</th><th>Tier</th><th>Status</th><th>Address</th></tr></thead><tbody>
        {d.feeds.filter((f: any) => !q || `${f.display_name} ${f.url}`.toLowerCase().includes(q.toLowerCase())).map((f: any) => (
          <tr key={f.source_id}><td>{f.display_name}<div className="small muted">{humanize(f.collection_method)}</div></td><td>{f.source_tier}</td><td><Badge kind={f.status === "active" ? "ok" : "warn"}>{f.status === "active" ? "Live" : "Not yet tested"}</Badge></td>
            <td className="small"><a href={f.url} target="_blank" rel="noreferrer noopener">{f.url.replace(/^https?:\/\//, "").slice(0, 70)} ↗</a>{f.basis && <div className="muted">{f.basis}</div>}</td></tr>))}
      </tbody></table></div>
      <div className="section-title">Domains your sheets cite most</div>
      <div className="chips">{d.domains.map((x: any) => <button key={x.domain} className="chip" onClick={() => setQ(x.domain)}>{x.domain} <b>{x.count}</b></button>)}</div>
      <div className="section-title">Every URL cited in your sheets</div>
      <p className="small muted">LinkedIn addresses are left out on purpose. Click through to read the original before you act.</p>
      <div className="card table-wrap"><table><thead><tr><th>Address</th><th>Account</th><th>Column</th></tr></thead><tbody>
        {d.items.map((u: any) => <tr key={u.url}><td className="small"><a href={u.url} target="_blank" rel="noreferrer noopener">{u.url.replace(/^https?:\/\//, "").slice(0, 80)} ↗</a></td><td>{u.organization}</td><td className="small muted">{u.field}</td></tr>)}
        {d.items.length === 0 && <tr><td colSpan={3} className="muted">No cited URLs match.</td></tr>}</tbody></table></div>
    </>
  );
}

export default function LibraryHub() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "signals";
  const q = sp.get("q") ?? "";
  const set = (next: Record<string, string>) => setSp({ tab, ...(q ? { q } : {}), ...next });
  return (
    <>
      <div className="page-head"><div className="eyebrow">Reference</div><h2>Library</h2></div>
      <div className="row spread">
        <div className="tabs" style={{ margin: 0, border: 0 }} role="tablist">
          {([["signals", "Signals"], ["sources", "Sources"], ["urls", "URLs and feeds"]] as [Tab, string][]).map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => set({ tab: k })}>{l}</button>)}
        </div>
        <input aria-label="Filter by keyword" placeholder="Filter by keyword" value={q} onChange={(e) => setSp({ tab, ...(e.target.value ? { q: e.target.value } : {}) })} style={{ width: 260 }} />
      </div>
      <div style={{ marginTop: 16 }}>
        {tab === "signals" && <Signals q={q} />}
        {tab === "sources" && <Library render={() => null} query={q} canSetup={false} />}
        {tab === "urls" && <Urls q={q} setQ={(v) => set({ q: v })} />}
      </div>
    </>
  );
}
