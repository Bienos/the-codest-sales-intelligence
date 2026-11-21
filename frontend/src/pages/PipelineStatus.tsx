import { useEffect, useState } from "react";
import { api, fmtDateTime, upload } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const STATE: Record<string, [string, string]> = { ok: ["ok", "Running"], waiting: ["warn", "Waiting for you"], stale: ["bad", "Not checking in"], never: ["bad", "Never checked in"], off: ["", "Off"] };

export function Pipeline() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const load = () => api("/api/admin/pipeline").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  return (
    <>
      <p className="small muted" style={{ marginTop: 0 }}>What runs by itself, what waits for a person, and what is switched off. This page reads live heartbeats, not documentation.</p>
      <div className="pipe-grid">
        <ol className="pipe" aria-label="Pipeline stages">
          {d.stages.map((s: any, i: number) => {
            const [kind, label] = STATE[s.state] ?? ["", s.state];
            return (
              <li key={s.key} className={`pipe-step ${s.state}`}>
                <div className="pipe-n">{String(i + 1).padStart(2, "0")}</div>
                <div className="pipe-body">
                  <div className="row spread"><b className="pipe-t">{s.label}</b><span className="row"><Badge>{humanize(s.mode)}</Badge><Badge kind={kind}>{label}</Badge></span></div>
                  <p className="small" style={{ margin: "6px 0 0" }}>{s.note}</p>
                  {s.last && <div className="small muted">Last activity {fmtDateTime(s.last)}</div>}
                  {s.detail?.length > 0 && <ul className="tight small">{s.detail.map((f: any, k: number) => <li key={k}><b>{f.display_name}</b> {f.error_class} ({f.consecutive_failures} failures)</li>)}</ul>}
                  {s.counts && Object.keys(s.counts).length > 0 && <div className="chips" style={{ marginTop: 6 }}>{Object.entries(s.counts).map(([k, v]: any) => <Badge key={k}>{humanize(k)} {v}</Badge>)}</div>}
                </div>
              </li>);
          })}
        </ol>
        <aside>
          <div className="card"><div className="eyebrow">Do next</div>
            {d.next_steps.length === 0 ? <p style={{ margin: 0 }}>Nothing is blocking the automatic flow.</p> : <ol className="tight" style={{ margin: 0 }}>{d.next_steps.map((n: string, i: number) => <li key={i}>{n}</li>)}</ol>}</div>
          <div className="card"><div className="eyebrow">Queue</div>
            <div className="chips">{Object.entries(d.queue).map(([k, v]: any) => <Badge key={k} kind={k === "permanent_failed" && v ? "bad" : ""}>{humanize(k)} {v}</Badge>)}{Object.keys(d.queue).length === 0 && <span className="small muted">Empty</span>}</div>
            {d.oldest_waiting_s !== null && <p className="small muted" style={{ marginBottom: 0 }}>Oldest waiting job: {Math.round(d.oldest_waiting_s / 60)} min</p>}</div>
        </aside>
      </div>
    </>
  );
}

export function KnowledgeAdmin() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const load = () => api("/api/kb").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const send = async (id: string, f?: File) => {
    if (!f) return; setErr(null); setMsg("");
    try { const r = await upload(`/api/admin/kb/${id}`, f); setMsg(`Loaded ${(r.bytes / 1024).toFixed(0)} KB. It is searchable now.`); load(); } catch (e) { setErr(e); }
  };
  if (!d) return <p className="muted">Loading</p>;
  return (
    <>
      <p className="small muted" style={{ marginTop: 0 }}>Internal documents are stored in the database, behind login, and shown through short-lived signed links in a sandboxed frame. They are never public files and never in the code repository. Upload the .html file for each.</p>
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />
      {d.items.map((k: any) => (
        <div key={k.id} className="card">
          <div className="row spread"><div><b>{k.title}</b><div className="small muted">{k.blurb}</div></div><Badge kind={k.available ? "ok" : "warn"}>{k.meta ? "Uploaded" : k.available ? "From repository file" : "Not loaded"}</Badge></div>
          {k.meta && <p className="small muted" style={{ margin: "8px 0 0" }}>{(k.meta.size_bytes / 1024).toFixed(0)} KB · uploaded by {k.meta.uploaded_by} on {fmtDateTime(k.meta.uploaded_at)}</p>}
          <input type="file" accept=".html,text/html" aria-label={`Upload ${k.title}`} style={{ marginTop: 10 }} onChange={(e) => { void send(k.id, e.target.files?.[0]); e.target.value = ""; }} />
        </div>))}
    </>
  );
}
