import { tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Err, LABELS, Skeleton, TIER, humanize } from "../components/ui";
import { CompanyAvatar } from "../components/Findings";

const GROUP_LABEL: Record<string, string> = { accounts: "Accounts", events: "Findings", signals: "Signals", sources: "Sources and feeds", documents: "Pages we have read", verifications: "Pages being checked", kb: "Knowledge base" };
const STATUS_WORD: Record<string, string> = { pending_connector_validation: "not set up yet", active: "live", paused: "paused" };
/** The search API joins codes into its meta line; turn them into words. */
const plainMeta = (m: string) => m.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (t) => (LABELS.event as Record<string, string>)[t] ?? STATUS_WORD[t] ?? humanize(t).toLowerCase()).replace(/\bS[1-5]\b/g, (t) => TIER[t] ?? t);
const EXAMPLES = ["core banking", "tender", "CTO appointed", "instant payments", "procurement", "Temenos", "senior-only", "DORA"];

/** `company` is true for a company hit: the company's logo (or its initials) leads the line, as everywhere a company is named. */
function Hit({ it, company = false }: { it: any; company?: boolean }) {
  const text = (
    <>
      <b className="hit-title">{it.title.replace(/^([A-Z]{3}-\d{2})\s+/, "")}</b>
      {it.meta && <span className="small muted"> · {plainMeta(it.meta)}</span>}
      {it.field && <span className="small muted">{" " + tr("· in “")}{it.field}”</span>}
      {it.snippet && <div className="small hit-snip">{it.snippet}</div>}
    </>
  );
  const body = company ? <span className="hit-co"><CompanyAvatar name={it.title} small logo={it.logo_url} /><span className="hit-co-t">{text}</span></span> : text;
  // the research explorer is no longer offered; finding hits open the searchable findings list instead
  const link = typeof it.link === "string" ? it.link.replace(/^\/explorer(?=[?#]|$)/, "/feed") : it.link;
  return <li className="hit">{link ? <Link to={link}>{body}</Link> : it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener">{body} ↗</a> : body}</li>;
}

export default function Search() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const [text, setText] = useState(q);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => { setText(q); }, [q]);
  useEffect(() => {
    setD(null); setErr(null);
    if (q.trim().length < 2) return;
    const request = new AbortController();
    api(`/api/search?target=true&q=${encodeURIComponent(q)}`, { signal: request.signal })
      .then(setD).catch((error) => { if (!request.signal.aborted) setErr(error); });
    return () => request.abort();
  }, [q, retry]);
  return (
    <>
      <div className="page-head"><div className="eyebrow">{tr("Find anything")}</div><h1>{tr("Search")}</h1></div>
      <form onSubmit={(e) => { e.preventDefault(); setSp(text.trim() ? { q: text.trim() } : {}); }} className="searchbar" role="search" aria-label={tr("Search page")}>
        <input autoFocus aria-label={tr("Search everything")} placeholder={tr("A company, a keyword, a signal, a source or a URL")} value={text} onChange={(e) => setText(e.target.value)} />
        <button className="primary" type="submit">{tr("Search")}</button>
      </form>
      <p className="small muted mt-0">{tr("Searches accounts, signals, sources, findings and the knowledge base. Every word you type must match.")}</p>
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {q.trim().length >= 2 && !d && !err && <Skeleton label="Searching" />}
      {!q && <div className="card"><div className="eyebrow">{tr("Try one of these")}</div><div className="chips">{EXAMPLES.map((x) => <button key={x} className="chip" onClick={() => setSp({ q: x })}>{x}</button>)}</div></div>}
      {d && d.total === 0 && <div className="empty"><b>{tr("No matches for “")}{d.query}”</b>{tr("Try fewer or shorter words, or check the spelling.")}</div>}
      {d?.groups.map((g: any) => (
        <section key={g.key}><div className="section-title">{tr(GROUP_LABEL[g.key] ?? g.label)} <Badge>{g.items.length}</Badge></div>
          <ul className="hits">{g.items.map((it: any, i: number) => <Hit key={i} it={it} company={g.key === "accounts"} />)}</ul></section>))}
    </>
  );
}
