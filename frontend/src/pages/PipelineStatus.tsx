import { tr } from "../locale";
import { useEffect, useState } from "react";
import { api, fmtDateTime, upload } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";

const STATE: Record<string, [string, string]> = { ok: ["ok", "Running"], waiting: ["warn", "Waiting for you"], stale: ["bad", "Stopped reporting"], never: ["bad", "Never reported"], off: ["", "Switched off"] };

function Funnel({ f, accounts, candidates }: { f: any[]; accounts: number; candidates: number }) {
  const max = Math.max(...f.map((x) => x.n), 1);
  // the biggest relative drop between neighbouring stages is where things are stopping
  let worst = -1, ratio = 1;
  f.forEach((x, i) => { if (i > 0 && f[i - 1].n > 0) { const r = x.n / f[i - 1].n; if (r < ratio) { ratio = r; worst = i; } } });
  return (
    <div className="card">
      <div className="eyebrow">{tr("Where things drop out (last 7 days)")}</div>
      <ol className="funnel">
        {f.map((x, i) => (
          <li key={x.key} className={i === worst && ratio < 0.5 ? "drop" : ""}>
            <div className="fn-l"><b>{tr(x.label)}</b><span className="small muted">{tr(x.note)}</span></div>
            <div className="fn-bar"><i style={{ width: `${Math.max((x.n / max) * 100, x.n ? 2 : 0)}%` }} /></div>
            <div className="fn-n">{x.n.toLocaleString()}</div>
          </li>))}
      </ol>
      {worst > 0 && ratio < 0.5 && <p className="small mt-3 mb-0"><b>{tr("Biggest drop:")}</b> {f[worst - 1].label.toLowerCase()} → {f[worst].label.toLowerCase()}. {f[worst].key === "linked" && accounts === 0 ? tr("There are no accounts yet. Load the workbooks or add companies.") : f[worst].key === "linked" ? tr("{} {} waiting to be added to your accounts.", candidates, candidates === 1 ? "company is" : "companies are") : ""}</p>}
    </div>
  );
}

export function Pipeline() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const load = () => api("/api/admin/pipeline").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 15000); return () => clearInterval(t); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  return (
    <>
      <p className="small muted mt-0">{tr("What runs by itself, what waits for a person, and what is switched off. This page shows what is happening now, not what is planned.")}</p>
      {d.funnel && <Funnel f={d.funnel} accounts={d.accounts} candidates={d.candidates} />}
      <div className="pipe-grid">
        <ol className="pipe" aria-label={tr("Pipeline stages")}>
          {d.stages.filter((s: any) => s.key !== "pipedrive").map((s: any, i: number) => {
            const [kind, label] = STATE[s.state] ?? ["", s.state];
            return (
              <li key={s.key} className={`pipe-step ${s.state}`}>
                <div className="pipe-n">{String(i + 1).padStart(2, "0")}</div>
                <div className="pipe-body">
                  <div className="row spread"><b className="pipe-t">{tr(s.label)}</b><span className="row"><Badge>{({ automatic: tr("Runs by itself"), manual: tr("A person does this"), off: tr("Switched off") } as Record<string, string>)[s.mode] ?? humanize(s.mode)}</Badge><Badge kind={kind}>{tr(label)}</Badge></span></div>
                  <p className="small mt-2 mb-0">{tr(s.note)}</p>
                  {s.last && <div className="small muted">{tr("Last activity") + " "}{fmtDateTime(s.last)}</div>}
                  {s.detail?.length > 0 && <ul className="tight small">{s.detail.map((f: any, k: number) => <li key={k}><b>{f.display_name}</b>: {f.error_class === "server_error" ? tr("the site returned an error") : humanize(f.error_class).toLowerCase()} {f.consecutive_failures}{" " + tr("times in a row")}</li>)}</ul>}
                  {s.counts && Object.keys(s.counts).length > 0 && <div className="chips mt-2">{Object.entries(s.counts).map(([k, v]: any) => <Badge key={k}>{humanize(k)} {v}</Badge>)}</div>}
                </div>
              </li>);
          })}
        </ol>
        <aside>
          <div className="card"><div className="eyebrow">{tr("What to do next")}</div>
            {d.next_steps.length === 0 ? <p className="m-0">{tr("Nothing is blocking the automatic flow.")}</p> : <ol className="tight m-0">{d.next_steps.map((n: string, i: number) => <li key={i}>{tr(n)}</li>)}</ol>}</div>
          <div className="card"><div className="eyebrow">{tr("Jobs waiting")}</div>
            <div className="chips">{Object.entries(d.queue).map(([k, v]: any) => <Badge key={k} kind={k === "permanent_failed" && v ? "bad" : ""}>{({ queued: tr("Waiting"), running: tr("Running"), retrying: tr("Retrying"), permanent_failed: tr("Failed for good") } as Record<string, string>)[k] ?? humanize(k)} {v}</Badge>)}{Object.keys(d.queue).length === 0 && <span className="small muted">{tr("None waiting")}</span>}</div>
            {d.oldest_waiting_s !== null && <p className="small muted mb-0">{tr("Longest wait:") + " "}{Math.round(d.oldest_waiting_s / 60)} min</p>}</div>
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
    try { const r = await upload(`/api/admin/kb/${id}`, f); setMsg(tr("Uploaded ({} KB). It can be searched now.", (r.bytes / 1024).toFixed(0))); load(); } catch (e) { setErr(e); }
  };
  if (!d) return <Skeleton />;
  return (
    <>
      <p className="small muted mt-0">{tr("These internal documents are kept behind sign-in and shown through short-lived links. They are never public. Upload the .html file for each one.")}</p>
      {msg && <div className="okmsg" role="status">{tr(msg)}</div>}<Err e={err} />
      {d.items.map((k: any) => (
        <div key={k.id} className="card">
          <div className="row spread"><div><b>{k.title}</b><div className="small muted">{k.blurb}</div></div><Badge kind={k.available ? "ok" : "warn"}>{k.meta ? tr("Uploaded") : k.available ? tr("Built in") : tr("Not uploaded yet")}</Badge></div>
          {k.meta && <p className="small muted mt-2 mb-0">{(k.meta.size_bytes / 1024).toFixed(0)}{" " + tr("KB · uploaded by") + " "}{k.meta.uploaded_by}{" " + tr("on") + " "}{fmtDateTime(k.meta.uploaded_at)}</p>}
          <input className="mt-3" type="file" accept=".html,text/html" aria-label={tr("Upload {}", k.title)} onChange={(e) => { void send(k.id, e.target.files?.[0]); e.target.value = ""; }} />
        </div>))}
    </>
  );
}
