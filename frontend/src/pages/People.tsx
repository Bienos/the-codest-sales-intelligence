import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, post } from "../api";
import { number, tr } from "../locale";
import { Badge, Empty, Err, PageHeader, Skeleton } from "../components/ui";
import { SHORT, SignalTag, signalName } from "../components/Signals";
import { EmailMark, initials } from "./AccountDetail";

/** The small mark in the people list: the person's LinkedIn photo when Exa found one (dropping back to initials if the picture no longer loads), else their initials. */
function ListMono({ name, photo }: { name: string; photo?: string | null }) {
  const [failed, setFailed] = useState<string | null>(null);
  const shown = Boolean(photo) && failed !== photo;
  return <span className={`pl-mono${shown ? " has-photo" : ""}`} aria-hidden="true">
    {shown ? <img src={photo!} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(photo!)} /> : initials(name)}
  </span>;
}
import { REGIONS } from "./targetMarket";

type Person = {
  id: string; name: string; photo_url?: string | null; title: string | null; email: "verified" | "unverified" | "none"; suppressed: boolean; suppression_reason: string | null;
  company: { id: string; name: string; country: string | null; region: string; segment: string | null; band: string; score: number | null; stopped: boolean };
  owner: { crm_owner_id: number | null; open_deals: number; mine: boolean };
  sources: string[]; engagement: string | null; engagement_label: string; last_activity_at: string | null; campaigns: number;
  signal: { rule_id: string; name: string; summary: string; date: string | null; tier: string; source_url: string | null } | null;
};
type Result = { items: Person[]; total: number; page: number; size: number; counts: any; sources: Record<string, string>; engagement_labels: Record<string, string> };
type SyncState = Record<string, { configured: boolean; how: string; last_sync?: string | null; last_change_seen?: string | null; last_run?: { status: string; at: string; error?: string } | null }> & { can_sync: boolean };

const ENG_KIND: Record<string, string> = { interested: "ok", replied: "ok", not_interested: "warn", bounced: "bad", unsubscribed: "bad", opened: "info", clicked: "info", sent: "info", pushed: "info" };
const FILTERS = ["q", "email", "engagement", "region", "signal", "rule", "sort", "suppressed"] as const;

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
  return <section className="mb-4" aria-label={tr("Where people come from")}>
    <div className="syncbar">{tile("sheets", "Workbooks")}{tile("apollo", "Apollo")}{tile("lemlist", "Lemlist")}</div>
    {msg && <div className="okmsg" role="status">{msg}</div>}
  </section>;
}

export default function People() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [data, setData] = useState<Result | null>(null);
  const [err, setErr] = useState<unknown>(null);
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
  return <>
    <PageHeader eyebrow="Contacts" title="People" sub="Everyone you can reach, from your workbooks, Apollo and Lemlist, in one list with their company's current buying signal. Email addresses stay hidden; they are only used through an approved campaign."
      right={<Link className="btn primary" to="/campaigns/new">{tr("New signal campaign")}</Link>} />
    <SyncBar />
    {c && <div className="today-summary mt-4" aria-label={tr("Overview")}>
      <a href="?signal=yes" onClick={(e) => { e.preventDefault(); set("signal", "yes"); }}><span>{tr("At a company with a live signal")}</span><b>{number(c.with_signal)} <small>/ {number(c.total)}</small></b></a>
      <a href="?email=any" onClick={(e) => { e.preventDefault(); set("email", "any"); }}><span>{tr("With email")}</span><b>{number(c.with_email ?? c.verified_email)}</b></a>
      <a href="?engagement=replied" onClick={(e) => { e.preventDefault(); set("engagement", "replied"); }}><span>{tr("Replied or interested")}</span><b>{number(c.engaged)}</b></a>
      <a href="?suppressed=true" onClick={(e) => { e.preventDefault(); set("suppressed", "true"); }}><span>{tr("Opted out or bounced")}</span><b>{number(c.suppressed)}</b></a>
    </div>}
    <form className="feedbar" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
      <input type="search" aria-label={tr("Search people")} placeholder={tr("Name, job title or company")} maxLength={120} value={search} onChange={(e) => setSearch(e.target.value)} />
      <button type="submit">{tr("Search")}</button>
      <select aria-label={tr("Buying signal")} value={params.get("rule") ?? (params.get("signal") === "yes" ? "_any" : "")} onChange={(e) => { const n = new URLSearchParams(params); n.delete("page"); n.delete("signal"); n.delete("rule");
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
    </form>
    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading people")} /> : data.items.length === 0 ? <Empty title="Nobody matches these filters">{tr("Clear a filter, or import contacts: upload the workbooks, connect Lemlist, or use Find decision makers on an account.")}</Empty> : <>
      <p className="small muted">{tr("{} people", number(data.total))}{params.toString() && <> · <a href="/people" onClick={(e) => { e.preventDefault(); setSearch(""); setParams(new URLSearchParams()); }}>{tr("Clear filters")}</a></>}</p>
      <div className="card flush table-wrap" tabIndex={0} role="region" aria-label={tr("People")}><table className="cards people-table"><thead><tr>
        <th>{tr("Person")}</th><th>{tr("Company")}</th><th>{tr("Buying signal")}</th><th>{tr("Email")}</th><th>{tr("Outreach")}</th></tr></thead><tbody>
        {data.items.map((p) => <tr key={p.id} className={p.suppressed ? "muted-row" : ""}>
          <td><div className="pl-person"><ListMono name={p.name} photo={p.photo_url} /><div><b><Link to={`/people/${p.id}`}>{p.name}</Link></b><div className="small muted">{p.title || tr("Role not recorded")}</div>{p.suppressed && <Badge kind="bad">{tr("Do not contact")}</Badge>}</div></div></td>
          <td data-label={tr("Company")}><Link to={`/accounts/${p.company.id}`}>{p.company.name}</Link>
            <div className="small muted">{[p.company.country, p.company.segment].filter(Boolean).join(" · ")}</div></td>
          <td data-label={tr("Buying signal")}>{p.signal ? <><SignalTag s={{ rule_id: p.signal.rule_id, name: p.signal.name, strength: 0 }} />
            <div className="small mt-1 sig-sum" title={p.signal.summary}>{p.signal.summary}</div><div className="small muted">{p.signal.date ? fmtDate(p.signal.date) : ""}</div></> : <span className="small muted">{tr("No live signal")}</span>}</td>
          <td data-label={tr("Email")}><EmailMark has={p.email !== "none"} /></td>
          <td data-label={tr("Outreach")}><Badge kind={ENG_KIND[p.engagement ?? ""] ?? ""}>{tr(p.engagement_label)}</Badge>
            {p.last_activity_at && <div className="small muted">{fmtDate(p.last_activity_at)}</div>}
            {p.campaigns > 0 && <div className="small muted">{tr("In {} signal campaign(s)", p.campaigns)}</div>}</td>
        </tr>)}
      </tbody></table></div>
      {pages > 1 && <div className="row mt-2" aria-label={tr("Pages")}>
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("Page {} of {}", page, pages)}</span>
        <button type="button" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </div>}
    </>}
  </>;
}
