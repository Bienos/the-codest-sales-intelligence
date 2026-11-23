import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Err, LABELS, TIER, humanize } from "../components/ui";

const GROUP_LABEL: Record<string, string> = { accounts: "Accounts", events: "Findings", signals: "Signals", sources: "Sources and feeds", documents: "Pages we have read", verifications: "Pages being checked", kb: "Knowledge base" };
const STATUS_WORD: Record<string, string> = { pending_connector_validation: "not set up yet", active: "live", paused: "paused" };
/** The search API joins codes into its meta line; turn them into words. */
const plainMeta = (m: string) => m.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (t) => (LABELS.event as Record<string, string>)[t] ?? STATUS_WORD[t] ?? humanize(t).toLowerCase()).replace(/\bS[1-5]\b/g, (t) => TIER[t] ?? t);
const EXAMPLES = ["core banking", "tender", "CTO appointed", "instant payments", "procurement", "Temenos", "senior-only", "DORA"];

function Hit({ it }: { it: any }) {
  const body = (
    <>
      <b className="hit-title">{it.title.replace(/^([A-Z]{3}-\d{2})\s+/, "")}</b>
      {it.meta && <span className="small muted"> · {plainMeta(it.meta)}</span>}
      {it.field && <span className="small muted"> · in “{it.field}”</span>}
      {it.snippet && <div className="small hit-snip">{it.snippet}</div>}
    </>
  );
  return <li className="hit">{it.link ? <Link to={it.link}>{body}</Link> : it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener">{body} ↗</a> : body}</li>;
}

export default function Search() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const [text, setText] = useState(q);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setText(q); }, [q]);
  useEffect(() => {
    if (q.trim().length < 2) { setD(null); return; }
    setErr(null);
    api(`/api/search?q=${encodeURIComponent(q)}`).then(setD).catch(setErr);
  }, [q]);
  return (
    <>
      <div className="page-head"><div className="eyebrow">Find anything</div><h1>Search</h1></div>
      <form onSubmit={(e) => { e.preventDefault(); setSp(text.trim() ? { q: text.trim() } : {}); }} className="searchbar" role="search">
        <input autoFocus aria-label="Search everything" placeholder="A company, a keyword, a signal, a source or a URL" value={text} onChange={(e) => setText(e.target.value)} style={{ maxWidth: 560 }} />
        <button className="primary" type="submit">Search</button>
      </form>
      <p className="small muted" style={{ marginTop: 0 }}>Searches accounts, signals, sources, findings and the knowledge base. Every word you type must match.</p>
      <Err e={err} />
      {!q && <div className="card"><div className="eyebrow">Try one of these</div><div className="chips">{EXAMPLES.map((x) => <button key={x} className="chip" onClick={() => setSp({ q: x })}>{x}</button>)}</div></div>}
      {d && d.total === 0 && <div className="empty"><b>No matches for “{d.query}”</b>Try fewer or shorter words, or check the spelling.</div>}
      {d?.groups.map((g: any) => (
        <section key={g.key}><div className="section-title">{GROUP_LABEL[g.key] ?? g.label} <Badge>{g.items.length}</Badge></div>
          <ul className="hits">{g.items.map((it: any, i: number) => <Hit key={i} it={it} />)}</ul></section>))}
    </>
  );
}
