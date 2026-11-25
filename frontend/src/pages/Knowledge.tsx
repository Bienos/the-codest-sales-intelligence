import { tr } from "../locale";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, post } from "../api";
import { Err, Skeleton } from "../components/ui";

/** The two internal documents are complete web pages of their own, so they get the whole content area instead of a small window inside a padded page.
 *  They still open through short-lived signed links in a sandboxed frame, and "Open full page" shows the same document in its own browser tab. */
export default function Knowledge() {
  const { doc } = useParams();
  const nav = useNavigate();
  const [docs, setDocs] = useState<any[]>([]);
  const [src, setSrc] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const load = () => { setErr(null); api("/api/kb").then((d) => setDocs(d.items)).catch(setErr); };
  useEffect(load, []);
  const current = docs.find((item) => item.id === doc)?.id ?? docs[0]?.id;
  useEffect(() => {
    if (!current || (docs.length && !docs.find((d) => d.id === current)?.available)) return;
    setSrc(""); setErr(null);
    let live = true;
    const get = () => post(`/api/kb/${current}/link`).then((r) => live && setSrc(r.url)).catch((error) => { if (live) setErr(error); });
    get();
    const t = setInterval(() => { if (!document.hidden) get(); }, 240000);          // links last five minutes; refresh quietly while the tab stays open
    return () => { live = false; clearInterval(t); };
  }, [current, docs]);
  const meta = docs.find((d) => d.id === current);
  const missing = docs.length > 0 && !meta?.available;
  return (
    <div className="kb-reader">
      <header className="kb-bar">
        <div className="kb-bar-l">
          <h1 className="kb-title">{tr("Knowledge base")}</h1>
          <nav className="kb-tabs" aria-label={tr("Documents")}>
            {docs.map((d) => <button key={d.id} type="button" aria-current={d.id === current ? "page" : undefined} onClick={() => nav(`/knowledge/${d.id}`)}>{d.title}</button>)}
          </nav>
        </div>
        <div className="kb-bar-r">
          <span className="small muted">{tr("Internal only. Do not share with prospects.")}</span>
          {src && <a className="btn primary" href={src} target="_blank" rel="noopener noreferrer">{tr("Open full page")}</a>}
        </div>
      </header>
      {meta && <p className="kb-blurb small muted">{meta.blurb} <a href={meta.source} target="_blank" rel="noreferrer noopener">{tr("Original document")}</a>{" · " + tr("an administrator refreshes this copy when the original changes")}</p>}
      {missing ? <div className="page"><div className="empty"><b>{tr("This document is not loaded yet")}</b>{tr("An administrator can upload it in Administration, Knowledge.")}</div></div>
        : src ? <iframe className="kb-frame2" title={meta?.title ?? tr("Knowledge base")} src={src} sandbox="allow-scripts allow-popups allow-modals allow-downloads" referrerPolicy="no-referrer" />
        : <div className="page"><Skeleton rows={4} /></div>}
      <div className="page"><Err e={err} onRetry={load} /></div>
      {!docs.length && !err && <div className="page"><Skeleton label="Loading knowledge base" /></div>}
    </div>
  );
}
