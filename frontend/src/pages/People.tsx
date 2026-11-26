import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, post } from "../api";
import { number, tr } from "../locale";
import { Badge, Empty, Err, PageHeader, Skeleton, safeHref } from "../components/ui";
import { SHORT, signalName } from "../components/Signals";
import { Avatar, COMMITTEE, EmailMark, Ic, Monogram } from "./AccountDetail";
import { REGIONS, countryName } from "./targetMarket";
import "../styles/people.css";

/** People: everyone we can reach, as cards. Each card says who they are, where they sit in the buying decision, how to reach them,
 *  when we last spoke, and why it is worth talking to them now (the company's live signal, or the service that fits it best). */

type Fit = { service: string | null; name: string; level: string | null; why: string | null };
type Person = {
  id: string; name: string; photo_url?: string | null; title: string | null; email: "verified" | "unverified" | "none"; suppressed: boolean; suppression_reason: string | null;
  committee?: string | null; seniority?: string | null; linkedin_url?: string | null; fit?: Fit | null; has_linkedin?: boolean;
  company: { id: string; name: string; country: string | null; region: string; segment: string | null; band: string; score: number | null; stopped: boolean; logo_url?: string | null };
  owner: { crm_owner_id: number | null; open_deals: number; mine: boolean };
  sources: string[]; engagement: string | null; engagement_label: string; last_activity_at: string | null; campaigns: number;
  signal: { rule_id: string; name: string; summary: string; date: string | null; tier: string; source_url: string | null } | null;
};
type Result = { items: Person[]; total: number; page: number; size: number; counts: any; sources: Record<string, string>; engagement_labels: Record<string, string> };
type SyncState = Record<string, { configured: boolean; how: string; last_sync?: string | null; last_change_seen?: string | null; last_run?: { status: string; at: string; error?: string } | null }> & { can_sync: boolean };

const ENG_KIND: Record<string, string> = { interested: "ok", replied: "ok", not_interested: "warn", bounced: "bad", unsubscribed: "bad", opened: "info", clicked: "info", sent: "info", pushed: "info" };
const FILTERS = ["q", "email", "engagement", "region", "signal", "rule", "sort", "suppressed"] as const;
/** Filters other than the search box and the sort order: the ones a person has to be told are switched on. */
const ACTIVE = ["email", "engagement", "region", "signal", "rule", "suppressed"] as const;

/** True when one line only repeats the other (the fit engine often quotes the very news shown above it). */
const core = (t: string) => t.toLowerCase().replace(/\s*\([^)]*\)\s*$/, "").replace(/[^\p{L}\p{N} ]/gu, "").replace(/\s+/g, " ").trim().slice(0, 48);
const repeats = (a: string, b: string) => { const x = core(a); return x.length > 12 && core(b).includes(x); };

function SyncBar() {
  const [s, setS] = useState<SyncState | null>(null);
  const [msg, setMsg] = useState<string>("");
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api<SyncState>("/api/people/sync-status").then(setS).catch(setErr); }, []);
  if (err) return <Err e={err} />;
  if (!s) return null;
  const sync = async (system: string) => {
    setErr(null); setMsg("");
    try { await post(`/api/people/sync/${system}`); setMsg(tr("Sync started. New people and replies appear here within a few minutes.")); } catch (e) { setErr(e); }
  };
  const keys: [string, string][] = [["sheets", "Workbooks"], ["apollo", "Apollo"], ["lemlist", "Lemlist"]];
  const on = keys.filter(([k]) => s[k]?.configured).length;
  const tile = (key: string, label: string) => {
    const x = s[key];
    const last = x.last_sync || x.last_run?.at || x.last_change_seen;
    return <div key={key} className={`syncsrc ${x.configured ? "on" : "off"}`}>
      <div className="row spread"><b>{label}</b><Badge kind={x.configured ? "ok" : "warn"}>{x.configured ? tr("Connected") : tr("Not connected")}</Badge></div>
      <div className="small muted">{x.configured ? (last ? tr("Last sync {}", fmtDate(last)) : tr("Not synced yet")) : tr(x.how)}</div>
      {x.last_run?.status === "permanent_failed" && <div className="small">{tr("Last sync failed. An administrator can see why in Administration, Pipeline.")}</div>}
      {s.can_sync && x.configured && key === "lemlist" && <button type="button" className="mt-1" onClick={() => sync(key)}>{tr("Sync now")}</button>}
    </div>;
  };
  return <details className="plc-src" aria-label={tr("Where people come from")}>
    <summary><span>{tr("Where people come from")}</span><small>{tr("{} of {} connected", on, keys.length)}</small></summary>
    <div className="syncbar">{keys.map(([k, l]) => tile(k, l))}</div>
    {msg && <div className="okmsg" role="status">{msg}</div>}
  </details>;
}

/** One person. The name is the one link that opens the page (it covers the whole card); the company and LinkedIn links sit above it. */
function Card({ p }: { p: Person }) {
  const g = p.committee && COMMITTEE[p.committee] ? p.committee : null;
  const s = p.signal, fit = p.fit && p.fit.name ? p.fit : null;
  const place = [countryName(p.company.country), p.company.segment].filter(Boolean).join(" · ");
  const li = p.linkedin_url ? safeHref(p.linkedin_url) : undefined;
  const search = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${p.name} ${p.company.name}`)}`;
  const last = p.engagement
    ? <><i className={`plc-dot ${ENG_KIND[p.engagement] ?? ""}`} aria-hidden="true" />{tr(p.engagement_label)}{p.last_activity_at ? <span className="plc-when"> · {fmtDate(p.last_activity_at)}</span> : null}</>
    : <>{tr("Not contacted yet")}</>;
  // why talk to them: the company's live signal first; with no signal, the service that fits best and the reason we hold for it
  const fitWhy = fit?.why && !(s && repeats(fit.why, s.summary)) ? fit.why : null;
  return <li className={`plc${p.suppressed ? " is-off" : ""}`}>
    <div className="plc-p">
      <Avatar name={p.name} committee={g} size="md" photo={p.photo_url} />
      <div className="plc-id">
        <h2><Link className="plc-name" to={`/people/${p.id}`}>{p.name}</Link></h2>
        <p className="plc-role">{p.title || tr("Role not recorded")}</p>
        <p className="plc-tags">
          {g && <span className="plc-grp" style={{ "--g": COMMITTEE[g].color } as React.CSSProperties}><i aria-hidden="true" />{tr(COMMITTEE[g].one)}</span>}
          {p.suppressed ? <span className="plc-dnc">{tr("Do not contact")}</span> : <EmailMark has={p.email !== "none"} />}
        </p>
      </div>
    </div>
    <div className="plc-c">
      <Link className="plc-co" to={`/accounts/${p.company.id}`}><Monogram name={p.company.name} size="xs" logo={p.company.logo_url} />{p.company.name}</Link>
      {place && <span className="plc-place">{place}</span>}
    </div>
    <div className="plc-w">
      {s ? <>
        <p className="plc-sig"><span>{signalName(s.rule_id, s.name)}</span>{s.date && <time dateTime={s.date}>{fmtDate(s.date)}</time>}</p>
        <p className="plc-sum">{s.summary}</p>
        {fit && <p className="plc-fit">{tr("Best fit")}: <b>{tr(fit.name)}</b></p>}
      </> : fit ? <>
        <p className="plc-fit is-lead">{tr("Best fit")}: <b>{tr(fit.name)}</b>{fit.level === "strong" && <small>{tr("Strong fit")}</small>}</p>
        {fitWhy && <p className="plc-sum">{fitWhy}</p>}
      </> : <p className="plc-none">{tr("No live signal at {} yet.", p.company.name)}</p>}
    </div>
    <div className="plc-f">
      <a className="plc-li" href={li ?? search} target="_blank" rel="noreferrer noopener" aria-label={li ? tr("LinkedIn profile of {}", p.name) : tr("Find {} on LinkedIn", p.name)}>
        <Ic.linkedin />{li ? tr("LinkedIn") : tr("Find on LinkedIn")}<span aria-hidden="true"> ↗</span></a>
      <p className="plc-last"><span className="plc-k">{tr("Last contact")}</span><span>{last}</span>{p.campaigns > 0 && <small>{tr("In {} signal campaign(s)", p.campaigns)}</small>}</p>
    </div>
  </li>;
}

export default function People() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [data, setData] = useState<Result | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const query = new URLSearchParams({ page: String(page), size: "50" });
  FILTERS.forEach((k) => { const v = params.get(k); if (v) query.set(k, v); });
  // every address we hold is usable, so the old "verified" and "unverified" links both mean "has an email"
  const emailParam = params.get("email") === "verified" || params.get("email") === "unverified" ? "any" : params.get("email") ?? "";
  if (emailParam) query.set("email", emailParam);
  const qs = query.toString();
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setData(null);
    api<Result>(`/api/people?${qs}`, { signal: request.signal }).then(setData).catch((e) => { if (!request.signal.aborted) setErr(e); });
    return () => request.abort();
  }, [qs]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setParams(n); };
  const c = data?.counts;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const active = ACTIVE.filter((k) => params.get(k)).length;
  const stat = (label: string, value: React.ReactNode, k: string, v: string, on: boolean) => <a href={`?${k}=${v}`} aria-current={on ? "true" : undefined}
    onClick={(e) => { e.preventDefault(); set(k, on ? "" : v); }}><span>{label}</span><b>{value}</b></a>;
  return <>
    <PageHeader eyebrow="Contacts" title="People" sub="Everyone you can reach, with the buying signal at their company. Email addresses stay hidden and are only used in approved campaigns."
      right={<Link className="btn primary plc-new" to="/campaigns/new">{tr("New signal campaign")}</Link>} />
    <SyncBar />
    {c && <div className="plc-stats" aria-label={tr("Overview")}>
      {stat(tr("At a company with a live signal"), <>{number(c.with_signal)} <small>/ {number(c.total)}</small></>, "signal", "yes", params.get("signal") === "yes")}
      {stat(tr("With email"), number(c.with_email ?? c.verified_email), "email", "any", emailParam === "any")}
      {stat(tr("Replied or interested"), number(c.engaged), "engagement", "replied", params.get("engagement") === "replied")}
      {stat(tr("Opted out or bounced"), number(c.suppressed), "suppressed", "true", params.get("suppressed") === "true")}
    </div>}
    <form className="plc-bar" role="search" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
      <div className="plc-q">
        <input type="search" aria-label={tr("Search people")} placeholder={tr("Name, job title or company")} maxLength={120} value={search} onChange={(e) => setSearch(e.target.value)} />
        <button type="submit">{tr("Search")}</button>
      </div>
      <button type="button" className="plc-toggle" aria-expanded={open} aria-controls="plc-filters" onClick={() => setOpen((v) => !v)}>
        {tr("Filters and order")}{active > 0 && <b>{active}</b>}</button>
      <div className={`plc-filters${open || active > 0 ? " is-open" : ""}`} id="plc-filters">
        <select aria-label={tr("Buying signal")} value={params.get("rule") ?? (params.get("signal") === "yes" ? "_any" : params.get("signal") === "no" ? "_none" : "")} onChange={(e) => { const n = new URLSearchParams(params); n.delete("page"); n.delete("signal"); n.delete("rule");
          if (e.target.value === "_any") n.set("signal", "yes"); else if (e.target.value === "_none") n.set("signal", "no"); else if (e.target.value) n.set("rule", e.target.value); setParams(n); }}>
          <option value="">{tr("Any company")}</option><option value="_any">{tr("Company has a live signal")}</option><option value="_none">{tr("No live signal")}</option>
          {Object.keys(SHORT).map((id) => <option key={id} value={id}>{signalName(id)}</option>)}
        </select>
        <select aria-label={tr("Email")} value={emailParam} onChange={(e) => set("email", e.target.value)}>
          <option value="">{tr("With or without email")}</option><option value="any">{tr("Has email")}</option><option value="none">{tr("No email")}</option>
        </select>
        <select aria-label={tr("Outreach")} value={params.get("engagement") ?? ""} onChange={(e) => set("engagement", e.target.value)}>
          <option value="">{tr("Any outreach")}</option>{data && Object.entries(data.engagement_labels).map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
        </select>
        <select aria-label={tr("Region")} value={params.get("region") ?? ""} onChange={(e) => set("region", e.target.value)}>
          <option value="">{tr("All regions")}</option>{REGIONS.filter(([id]) => id !== "international").map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
        </select>
        <select aria-label={tr("Sort")} value={params.get("sort") ?? "signal"} onChange={(e) => set("sort", e.target.value === "signal" ? "" : e.target.value)}>
          <option value="signal">{tr("Strongest signal first")}</option><option value="activity">{tr("Latest outreach activity")}</option><option value="company">{tr("Company")}</option><option value="name">{tr("Name")}</option>
        </select>
      </div>
    </form>
    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading people")} /> : data.items.length === 0 ? <>
      <Empty title="Nobody matches these filters">{tr("Clear a filter, or import contacts: upload the workbooks, connect Lemlist, or use Find decision makers on an account.")}</Empty>
      {params.toString() && <p className="plc-count"><button type="button" className="ghost plc-clear" onClick={() => { setSearch(""); setParams(new URLSearchParams()); }}>{tr("Clear filters")}</button></p>}
    </> : <>
      <p className="plc-count" role="status">{tr("{} people", number(data.total))}{params.toString() && <> · <a href="/people" onClick={(e) => { e.preventDefault(); setSearch(""); setParams(new URLSearchParams()); }}>{tr("Clear filters")}</a></>}</p>
      <div className="plc-head" aria-hidden="true"><span>{tr("Person")}</span><span>{tr("Company")}</span><span>{tr("Why talk to them")}</span><span>{tr("Reach them")}</span></div>
      <ul className="plc-list" aria-label={tr("People")}>{data.items.map((p) => <Card key={p.id} p={p} />)}</ul>
      {pages > 1 && <nav className="plc-pager" aria-label={tr("Pages")}>
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("Page {} of {}", page, pages)}</span>
        <button type="button" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </nav>}
    </>}
  </>;
}
