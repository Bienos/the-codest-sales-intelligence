import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, post } from "../api";
import { Err } from "../components/ui";

export default function Knowledge() {
  const { doc } = useParams();
  const nav = useNavigate();
  const [docs, setDocs] = useState<any[]>([]);
  const [src, setSrc] = useState("");
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api("/api/kb").then((d) => setDocs(d.items)).catch(setErr); }, []);
  const current = doc ?? docs[0]?.id;
  useEffect(() => {
    if (!current || (docs.length && !docs.find((d) => d.id === current)?.available)) return;
    setSrc("");
    let live = true;
    const get = () => post(`/api/kb/${current}/link`).then((r) => live && setSrc(r.url)).catch(setErr);
    get();
    const t = setInterval(get, 240000);          // links last five minutes; refresh quietly while the tab stays open
    return () => { live = false; clearInterval(t); };
  }, [current, docs]);
  const meta = docs.find((d) => d.id === current);
  const missing = docs.length > 0 && !meta?.available;
  return (
    <>
      <div className="page-head"><div className="eyebrow">Internal only</div><h2>Knowledge base</h2></div>
      <div className="tabs" role="tablist">{docs.map((d) => <button key={d.id} role="tab" aria-selected={d.id === current} onClick={() => nav(`/knowledge/${d.id}`)}>{d.title}</button>)}</div>
      <Err e={err} />
      {meta && <p className="small muted" style={{ marginTop: 0 }}>{meta.blurb} Not for prospects. A snapshot of the artifact at <a href={meta.source} target="_blank" rel="noreferrer noopener">{meta.source.replace("https://", "")}</a>; ask for a refresh when it changes.</p>}
      {missing ? <div className="empty"><b>This document is not loaded yet</b>An administrator can upload it in Administration, Knowledge.</div> : src ? <iframe className="kb-frame" title={meta?.title ?? "Knowledge base"} src={src} sandbox="allow-scripts allow-popups allow-modals allow-downloads" referrerPolicy="no-referrer" /> : <p className="muted">Loading</p>}
    </>
  );
}
