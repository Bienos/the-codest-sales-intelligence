import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Err } from "../components/ui";

const EXAMPLES = ["core banking", "tender", "CTO appointed", "instant payments", "procurement", "Temenos", "senior-only", "DORA"];

function Hit({ it }: { it: any }) {
  const body = (
    <>
      <b className="hit-title">{it.title}</b>
      {it.meta && <span className="small muted"> · {it.meta}</span>}
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
      <div className="page-head"><div className="eyebrow">Find anything</div><h2>Search</h2></div>
      <form onSubmit={(e) => { e.preventDefault(); setSp(text.trim() ? { q: text.trim() } : {}); }} className="searchbar" role="search">
        <input autoFocus aria-label="Search everything" placeholder="A company, a keyword, a signal, a source or a URL" value={text} onChange={(e) => setText(e.target.value)} style={{ maxWidth: 560 }} />
        <button className="primary" type="submit">Search</button>
      </form>
      <p className="small muted" style={{ marginTop: 0 }}>Searches your accounts and what the sheets say about them, the 64 signals, every source and feed, verified events, pages read, and the knowledge base. All words must match.</p>
      <Err e={err} />
      {!q && <div className="card"><div className="eyebrow">Try</div><div className="chips">{EXAMPLES.map((x) => <button key={x} className="chip" onClick={() => setSp({ q: x })}>{x}</button>)}</div></div>}
      {d && d.total === 0 && <div className="empty"><b>No matches for “{d.query}”</b>Try fewer or shorter words.</div>}
      {d?.groups.map((g: any) => (
        <section key={g.key}><div className="section-title">{g.label} <Badge>{g.items.length}</Badge></div>
          <ul className="hits">{g.items.map((it: any, i: number) => <Hit key={i} it={it} />)}</ul></section>))}
    </>
  );
}
