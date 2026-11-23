import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, post } from "../api";
import { Err, Skeleton, Tabs } from "../components/ui";

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
    <>
      <div className="page-head"><div className="eyebrow">Internal only</div><h1>Knowledge base</h1></div>
      <Tabs items={docs.map((d) => ({ key: d.id, label: d.title }))} value={current ?? ""} onChange={(k) => nav(`/knowledge/${k}`)} label="Documents">

      {meta && <p className="small muted mt-0">{meta.blurb} Internal only: do not share with prospects. This is a copy of the <a href={meta.source} target="_blank" rel="noreferrer noopener">original document</a>. Ask an administrator to refresh it when the original changes.</p>}
      {missing ? <div className="empty"><b>This document is not loaded yet</b>An administrator can upload it in Administration, Knowledge.</div> : src ? <iframe className="kb-frame" title={meta?.title ?? "Knowledge base"} src={src} sandbox="allow-scripts allow-popups allow-modals allow-downloads" referrerPolicy="no-referrer" /> : <Skeleton />}
      </Tabs>
      <Err e={err} onRetry={load} />
      {!docs.length && !err && <Skeleton label="Loading knowledge base" />}
    </>
  );
}
