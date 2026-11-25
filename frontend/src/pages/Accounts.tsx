import { counted, number, tr } from "../locale";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, post } from "../api";
import { CHECKING, ListLeadLine } from "./Prospects";
import { REGIONS, countryName } from "./targetMarket";
import { Badge, Err, Skeleton, groupName, safeHref } from "../components/ui";
import { CompanyAvatar, ListRank, ago } from "../components/Findings";
import { signalName } from "../components/Signals";
import "../styles/accounts.css";

const del = (p: string) => api(p, { method: "DELETE" });

/* One list of companies. Every company from the lead list, the workbooks and research is in exactly one of three views:
 *   target  - target accounts: the lead list's eligible companies, plus companies whose facts fit;
 *   filling - still being filled in: they may fit, but a fact is missing, and Exa is looking it up (about 20 a day);
 *   other   - not a fit: on the list or in the workbooks, but outside the target profile, with the reason on the row.
 * A workbook lead is shown on its company row (the lead list's strongest signal), so there is no second list of leads. */

type View = "target" | "filling" | "other";
const SCOPE: Record<View, string> = { target: "target", filling: "review", other: "other" };
const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "target", label: "Target accounts", hint: "Ready to work" },
  { key: "filling", label: "Still being filled in", hint: "Exa is looking up facts" },
  { key: "other", label: "Not a fit", hint: "Outside what you sell to" },
];
const VIEW_LINE: Record<View, string> = {
  target: "The companies you can sell to now: your lead list's eligible companies, plus companies in financial services that are big enough and not state-owned.",
  filling: "These may be target accounts, but a fact is missing. Exa looks it up for you, and a company moves to Target accounts as soon as its facts fit.",
  other: "On your lead list or in your workbooks, but outside what you sell to. The reason is on each row.",
};

/** The target profile in plain words: what each fact is for. */
const RULES: [string, string][] = [
  ["In financial services", "Banks, insurers, payments, lending, wealth and fintech companies. Companies that sell software to them are not targets."],
  ["Big enough", "More than 500 staff in Europe, or more than 2,000 elsewhere."],
  ["Not owned by the state", "Privately held, listed and mutual companies are fine. State-owned companies and public bodies buy through public tenders instead: you find those on Open tenders."],
  ["Your lead list comes first", "A company your lead list marks eligible is always a target account. For the others, each fact needs a web page that says so, and Exa finds it."],
];

function AddCompany({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [f, setF] = useState({ website: "", name: params.get("name") ?? "", links: "", notes: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { const r = await post("/api/accounts/add", { website: f.website, name: f.name || null, notes: f.notes || null, links: f.links.split("\n").map((x) => x.trim()).filter(Boolean) }); nav(`/accounts/${r.id}?profile=1`); }
    catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <form className="card acc-add" onSubmit={submit}>
      <div className="row spread"><b>{tr("Add a company")}</b><button type="button" className="ghost" onClick={onClose}>{tr("Close")}</button></div>
      <p className="small muted mt-2 mb-3">{tr("Start with the company website. You can check its sector and headcount on the next screen.")}</p>
      <div className="addgrid">
        <label className="small">{tr("Website (required)")}<input id="add-website" required placeholder="example.com" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
        <label className="small">{tr("Company name (optional)")}<input id="add-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      </div>
      <label className="small block mt-3">{tr("Supporting links, one per line (optional)")}<textarea id="add-links" rows={3} value={f.links} onChange={(e) => setF({ ...f, links: e.target.value })} placeholder="https://…" /></label>
      <label className="small block mt-3">{tr("Why this company? (optional)")}<textarea id="add-notes" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <Err e={err} />
      <button className="primary" disabled={busy || f.website.trim().length < 3}>{tr("Add and research")}</button>
    </form>
  );
}

const SIZE = 40;
const SORTS: [string, string][] = [["score", "Strongest signals"], ["priority", "Lead list priority"], ["list_score", "List score"], ["recent", "Newest signal"], ["size", "Largest first"], ["name", "Company name"]];
/** On/off filters, kept in the address so a filtered list survives opening a company and coming back. [address key, API parameter, API value, label] */
const TOGGLES: [string, string, string, string][] = [
  ["email", "has_email", "true", "With email"],
  ["signal", "has_signal", "true", "Checked signal"],
  ["recent", "signal_days", "30", "New in 30 days"],
  ["hot", "band", "hot", "Hot"],
  ["watched", "watched", "true", "Watching"],
  ["shortlist", "shortlist", "true", "Shortlist"],
];
const ORIGIN: Record<string, string> = { added: "Added by the team", discovered: "Found by research" };
const PRIORITY = /^P[123]$/;
const MISSING: Record<string, string> = { ownership: "Who owns it", employees: "Headcount", country: "Country", sector: "Sector", website: "Website" };
const WHY_NOT: Record<string, string> = {
  list_out_of_scope: "Lead list: out of scope",
  state_owned: "State-owned",
  not_finance: "Not financial services",
  too_small: "500 staff or fewer",
  too_small_outside_europe: "Outside Europe, 2,000 staff or fewer",
  sector_unknown: "Sector not known",
  removed: "Removed by your team",
  other: "Outside the target profile",
};

const Ico = {
  search: <svg className="acc-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M16 16l5 5" /></svg>,
  globe: <svg className="acc-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18" /></svg>,
  building: <svg className="acc-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 21V5h10v16M14 10h6v11M4 21h16M8 9h2M8 13h2M8 17h2" /></svg>,
  sort: <svg className="acc-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4" /></svg>,
  chev: <svg className="acc-chev" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M2.5 4.5L6 8l3.5-3.5" /></svg>,
};

/** Map an old address (the Leads and review tabs) to the one list. */
function viewOf(raw: string | null): View {
  return raw === "filling" || raw === "review" ? "filling" : raw === "other" ? "other" : "target";
}

export function Accounts({ me }: { me: { permissions: string[] } }) {
  const [params, setParams] = useSearchParams();
  const research = me.permissions.includes("research.request");
  const view = viewOf(params.get("view"));
  const [movedFromLeads, setMovedFromLeads] = useState(() => params.get("view") === "leads");
  const adding = params.get("add") === "1";
  const [counts, setCounts] = useState<Record<string, number> | null>(null);       // kept across views, so the switch never blinks
  // old links (/accounts?view=leads, ?view=review) land on the one list
  useEffect(() => {
    const raw = params.get("view");
    if (raw !== "leads" && raw !== "review") return;
    const next = new URLSearchParams(params);
    if (raw === "review") next.set("view", "filling"); else next.delete("view");
    setParams(next, { replace: true });
  }, [params, setParams]);
  const update = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in values)) next.delete("page");
    setParams(next, { replace: true });
  };

  return (
    <>
      <div className="page-head acc-headline">
        <div>
          <h1>{tr("Accounts")}</h1>
          <p className="sub">{tr("Every company from your lead list and research, in one list. Open a company to see its people, signals and research.")}</p>
        </div>
        {research && <button className={adding ? "" : "primary"} onClick={() => update({ add: adding ? "" : "1" })}>{adding ? tr("Cancel") : tr("Add a company")}</button>}
      </div>
      {movedFromLeads && <div className="acc-moved" role="status">
        <p>{tr("Leads now sit on their company. Each row shows the strongest signal from your lead list. Every checked signal is on")} <Link to="/signals">{tr("Buying signals")}</Link>.</p>
        <button type="button" className="linkish" onClick={() => setMovedFromLeads(false)}>{tr("Got it")}</button>
      </div>}
      {adding && research && <AddCompany onClose={() => update({ add: "", name: "" })} />}
      <CompanyDirectory key={view} view={view} research={research} params={params} update={update} counts={counts} setCounts={setCounts} />
    </>
  );
}

/** How Exa is getting on with the missing facts. */
function FillInNote({ compact = false }: { compact?: boolean }) {
  const [s, setS] = useState<any>(null);
  useEffect(() => {
    const request = new AbortController();
    api("/api/company-facts/status", { signal: request.signal }).then(setS).catch(() => { /* the note is optional */ });
    return () => request.abort();
  }, []);
  if (!s) return compact ? null : <div className="acc-fill is-loading" aria-hidden="true" />;
  const looked = Math.max(0, s.queue - s.due);
  const pct = s.queue ? Math.round((looked / s.queue) * 100) : 100;
  if (compact) {
    if (s.state === "off") return <span className="acc-tile-sub">{tr("Exa fill-in is off")}</span>;
    return <span className="acc-tile-sub">{s.state === "paused" ? tr("Exa paused") : tr("Exa: {} this week", number(s.checked_week))}</span>;
  }
  const line = s.state === "off"
    ? tr("Automatic fill-in is off here: Exa is not set up. You can still add a company's details by hand on its page.")
    : s.state === "paused"
      ? tr("Paused: the last 30% of this month's Exa budget is kept for your own research. It starts again next month.")
      : s.queue === 0 ? tr("Nothing is waiting for Exa.")
        : s.websites > 0
          ? tr("Exa is filling in facts for {} companies: the {} here, and {} target accounts without a website. {} done this week; about {} a day.",
              number(s.queue), number(s.filling), number(s.websites), number(s.checked_week), number(s.per_day))
          : tr("Exa is filling in facts for {} companies; {} done this week, about {} a day.", number(s.queue), number(s.checked_week), number(s.per_day));
  return <div className={`acc-fill is-${s.state}`} role="status">
    <span className="acc-fill-mark" aria-hidden="true"><i /><i /><i /></span>
    <p>{line}</p>
    {s.state !== "off" && s.queue > 0 && <span className="acc-fill-meter" role="img" aria-label={tr("{} of {} companies looked at", number(looked), number(s.queue))}>
      <i style={{ inlineSize: `${pct}%` }} /></span>}
  </div>;
}

function CompanyDirectory({ view, research, params, update, counts, setCounts }: {
  view: View; research: boolean; params: URLSearchParams; update: (values: Record<string, string>) => void;
  counts: Record<string, number> | null; setCounts: (counts: Record<string, number> | null) => void;
}) {
  const q = params.get("q") ?? "";
  const requestedRegion = params.get("region") ?? "";
  const region = REGIONS.some(([key]) => key === requestedRegion) ? requestedRegion : "";
  const segment = params.get("segment") ?? "";
  const priority = PRIORITY.test(params.get("priority") ?? "") ? params.get("priority")! : "";
  const sort = SORTS.some(([key]) => key === params.get("sort")) ? params.get("sort")! : "score";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const on = Object.fromEntries(TOGGLES.map(([key]) => [key, params.get(key) === "1"]));
  const filters: Record<string, string> = { scope: SCOPE[view], by_region: "false", sort, q, region, segment, priority };
  TOGGLES.forEach(([key, name, value]) => { if (on[key]) filters[name] = value; });
  const filterQuery = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "")).toString();
  const query = `${filterQuery}&size=${SIZE}&page=${page}`;
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [facets, setFacets] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [selection, setSelection] = useState<{ query: string; ids: string[] }>({ query: filterQuery, ids: [] });
  const selected = selection.query === filterQuery ? selection.ids : [];
  const [msg, setMsg] = useState("");
  const top = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    const timer = setTimeout(() => {
      api(`/api/leads?${query}`, { signal: request.signal })
        .then((data) => { if (!request.signal.aborted) { setResult({ query, data }); setFacets(data.facets); setCounts(data.counts); } })
        .catch((error) => { if (!request.signal.aborted) setErr(error); });
    }, q ? 250 : 0);
    return () => { clearTimeout(timer); request.abort(); };
  }, [query, q, retry]);
  // while a lead-list source is being checked, look again every few seconds
  const checking = Boolean(d?.items.some((r: any) => CHECKING.includes(r.list_lead?.check)));
  useEffect(() => {
    if (!checking) return;
    const timer = setInterval(() => { if (!document.hidden) setRetry((n) => n + 1); }, 6000);
    return () => clearInterval(timer);
  }, [checking]);

  const switchView = (key: View) => update({ view: key === "target" ? "" : key });
  const toggle = (id: string) => setSelection({ query: filterQuery, ids: selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id] });
  const toggleWatch = async (r: any) => {
    try {
      await (r.watched ? del(`/api/accounts/${r.id}/watch`) : post(`/api/accounts/${r.id}/watch`));
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const decide = async (r: any, decision: "qualify" | "reject") => {
    try {
      await post(`/api/leads/${r.id}/qualify`, { decision });
      setSelection({ query: filterQuery, ids: selected.filter((id) => id !== r.id) });
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const exportCsv = async () => {
    try {
      const t = sessionStorage.getItem("ri_token");
      const response = await fetch(`/api/leads.csv?${filterQuery}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!response.ok) throw new Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "accounts.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) { setErr(e); }
  };
  const scanSelected = async () => {
    setErr(null);
    setMsg("");
    try {
      const r = await post("/api/leads/scan", { ids: selected, with_search: false });
      setMsg(tr("Checking {} {} for news. Findings will appear in All findings.", r.queued, r.queued === 1 ? tr("company") : tr("companies")));
      setSelection({ query: filterQuery, ids: [] });
    } catch (e) { setErr(e); }
  };
  const goTo = (next: number) => { update({ page: next > 1 ? String(next) : "" }); top.current?.scrollIntoView({ block: "start" }); };
  const clear = () => update({ q: "", region: "", segment: "", priority: "", ...Object.fromEntries(TOGGLES.map(([key]) => [key, ""])) });
  const filtered = Boolean(q || region || segment || priority || TOGGLES.some(([key]) => on[key]));

  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  const visiblePage = d?.page ?? page;
  const segments = Object.entries((facets?.segment ?? {}) as Record<string, number>).filter(([key]) => key !== "unknown").sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const chipCounts: Record<string, number | undefined> = { email: facets?.has_email, signal: facets?.has_signal };
  const selectable = research && view === "target";
  const nouns: Record<View, [[string, string], [string, string, string]]> = {
    target: [["target account", "target accounts"], ["konto docelowe", "konta docelowe", "kont docelowych"]],
    filling: [["company being filled in", "companies being filled in"], ["firma w uzupełnianiu", "firmy w uzupełnianiu", "firm w uzupełnianiu"]],
    other: [["company that does not fit", "companies that do not fit"], ["firma spoza profilu", "firmy spoza profilu", "firm spoza profilu"]],
  };
  const elsewhere = VIEWS.filter((v) => v.key !== view && (counts?.[SCOPE[v.key]] ?? 0) > 0);
  const col4 = view === "target" ? "Contacts" : view === "filling" ? "Missing facts" : "Why not";
  return (
    <section aria-label={tr("Companies")}>
      <div className="acc-switch" role="group" aria-label={tr("Which companies")}>
        {VIEWS.map((v) => <button key={v.key} type="button" className={`acc-tile is-${v.key}`} aria-pressed={view === v.key} onClick={() => switchView(v.key)}>
          <span className="acc-tile-n">{counts ? number(counts[SCOPE[v.key]] ?? 0) : "–"}</span>
          <span className="acc-tile-l">{tr(v.label)}</span>
          {v.key === "filling" ? <FillInNote compact /> : <span className="acc-tile-sub">{tr(v.hint)}</span>}
        </button>)}
      </div>
      <p className="acc-viewline">{tr(VIEW_LINE[view])}</p>
      <details className="acc-how">
        <summary>{tr("What makes a target account?")}</summary>
        <dl className="acc-how-body">
          {RULES.map(([title, text]) => <div key={title}><dt>{tr(title)}</dt><dd>{tr(text)}</dd></div>)}
        </dl>
      </details>
      {view === "filling" && <FillInNote />}

      <div className="acc-tools-panel" role="search" aria-label={tr("Filter companies")}>
        <div className="acc-bar">
          <label className="acc-ctl acc-q">{Ico.search}<span className="sr">{tr("Search companies")}</span>
            <input type="search" maxLength={120} placeholder={tr("Search company, website or signal")} value={q} onChange={(e) => update({ q: e.target.value })} />
          </label>
          <label className="acc-ctl">{Ico.globe}<span className="sr">{tr("Region")}</span>
            <select value={region} onChange={(e) => update({ region: e.target.value })}>
              <option value="">{tr("All regions")}</option>
              {REGIONS.map(([key, name]) => <option key={key} value={key}>{tr(name)}</option>)}
            </select>{Ico.chev}
          </label>
          <label className="acc-ctl">{Ico.building}<span className="sr">{tr("Industry")}</span>
            <select value={segment} onChange={(e) => update({ segment: e.target.value })}>
              <option value="">{tr("All industries")}</option>
              {segment && !segments.some(([key]) => key === segment) && <option value={segment}>{segment}</option>}
              {segments.map(([key, n]) => <option key={key} value={key}>{tr(key)} ({number(n)})</option>)}
            </select>{Ico.chev}
          </label>
          <label className="acc-ctl">{Ico.sort}<span className="sr">{tr("Sort by")}</span>
            <select value={sort} onChange={(e) => update({ sort: e.target.value === "score" ? "" : e.target.value })}>
              {SORTS.map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
            </select>{Ico.chev}
          </label>
        </div>
        <div className="acc-chips">
          <div className="acc-seg" role="group" aria-label={tr("Lead list priority")}>
            <span className="acc-seg-l" aria-hidden="true">{tr("Priority")}</span>
            <button type="button" aria-pressed={!priority} onClick={() => update({ priority: "" })}>{tr("All")}</button>
            {["P1", "P2", "P3"].map((p) => <button key={p} type="button" aria-pressed={priority === p} aria-label={tr("Priority {}", p)} onClick={() => update({ priority: priority === p ? "" : p })}>
              {p}{facets?.priority?.[p] != null && <b>{number(facets.priority[p])}</b>}</button>)}
          </div>
          <span className="acc-chips-l" aria-hidden="true">{tr("Show only")}</span>
          {TOGGLES.map(([key, , , label]) => <button key={key} type="button" className="acc-chip" aria-pressed={on[key]} onClick={() => update({ [key]: on[key] ? "" : "1" })}>
            {tr(label)}{chipCounts[key] != null && <b>{number(chipCounts[key]!)}</b>}</button>)}
        </div>
      </div>

      <div className="acc-resultbar" ref={top}>
        <p className="acc-count" role="status">{d ? counted(d.total, nouns[view][0], nouns[view][1]) : tr("Loading companies")}
          {filtered && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}</p>
        <div className="acc-tools">
          {selected.length > 0 && <span className="acc-selected">{tr("{} selected", number(selected.length))}
            <button type="button" className="linkish" onClick={() => setSelection({ query: filterQuery, ids: [] })}>{tr("Clear selection")}</button></span>}
          {selected.length > 0 && <button onClick={scanSelected}>{tr("Check news for {} companies", number(selected.length))}</button>}
          <button disabled={!d?.total} onClick={exportCsv}>{tr("Export CSV")}</button>
        </div>
      </div>
      {msg && <p className="okmsg" role="status">{msg}</p>}
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!d && !err && <Skeleton rows={6} label={tr("Loading companies")} />}
      {d && d.items.length === 0 && <div className="acc-empty">
        <b>{filtered ? tr("Nothing here matches") : view === "filling" ? tr("Nothing to fill in") : view === "other" ? tr("Nothing here") : tr("No target accounts yet")}</b>
        <p>{filtered ? tr("Try fewer filters or another word.")
          : view === "filling" ? tr("Every company on your list has the facts it needs.")
            : view === "other" ? tr("Every company on your list fits what you sell to.")
              : tr("Import your lead list in Administration, or add a company.")}</p>
        {(filtered || elsewhere.length > 0) && <div className="acc-empty-acts">
          {elsewhere.map((v) => <button key={v.key} type="button" onClick={() => switchView(v.key)}>
            {tr("{} in {}", number(counts![SCOPE[v.key]]), tr(v.label))}</button>)}
          {filtered && <button type="button" className="ghost" onClick={clear}>{tr("Clear filters")}</button>}
        </div>}
      </div>}
      {d && d.items.length > 0 && <>
        <div className={`acc-head${selectable ? "" : " no-pick"} is-${view}`} aria-hidden="true">
          <span className="acc-h-id">{tr("Company")}</span><span className="acc-h-sig">{tr("Latest signal")}</span>
          <span className="acc-h-rank">{tr("Lead list")}</span><span className="acc-h-ppl">{tr(col4)}</span>
          <span className="acc-h-end">{tr("Watch")}</span>
        </div>
        <ol className={`acc-list${selectable ? "" : " no-pick"} is-${view}`} aria-label={tr("Companies, page {} of {}", visiblePage, pages)}>
          {d.items.map((r: any) => <AccountRow key={r.id} r={r} view={view} research={research} selectable={selectable}
            selected={selected.includes(r.id)} full={!selected.includes(r.id) && selected.length >= 25}
            onSelect={() => toggle(r.id)} onWatch={() => toggleWatch(r)} onDecide={(decision) => decide(r, decision)} onChanged={() => setRetry((n) => n + 1)} />)}
        </ol>
        {pages > 1 && <nav className="acc-pages" aria-label={tr("Pages")}>
          <button disabled={visiblePage <= 1} onClick={() => goTo(visiblePage - 1)}>{tr("Previous")}</button>
          <span>{tr("Page {} of {}", visiblePage, pages)}<span className="muted"> · {tr("{} to {} of {}", number((visiblePage - 1) * d.size + 1), number(Math.min(visiblePage * d.size, d.total)), number(d.total))}</span></span>
          <button disabled={visiblePage >= pages} onClick={() => goTo(visiblePage + 1)}>{tr("Next")}</button>
        </nav>}
      </>}
    </section>
  );
}

function SignalCell({ r, research, onChanged }: { r: any; research: boolean; onChanged: () => void }) {
  const s = r.buying_signal;
  if (r.stopped) return <><p className="acc-sig-k"><Badge kind="warn">{tr("On hold")}</Badge></p>
    <p className="acc-sig-t acc-sig-2">{tr("A reason to stop or wait was found. Open the company before contacting them.")}</p></>;
  if (s) {
    const href = safeHref(s.source_url);
    return <>
      <p className="acc-sig-k"><i className="acc-mk on" aria-hidden="true" /><b className="acc-sig-h">{signalName(s.rule_id ?? "", s.label)}</b></p>
      <p className="acc-sig-t" title={s.summary}><span className="sr">{tr("Checked signal:")} </span>{s.summary}</p>
      <p className="acc-sig-meta">
        <span className="acc-state ok">{tr("Checked")}</span>
        <span className="acc-sig-when">{ago(s.happened_at ?? s.published_at)}</span>
        {href && <a href={href} target="_blank" rel="noreferrer noopener" aria-label={tr("Original source for {}", r.name)}>{tr("Source")} ↗</a>}
      </p>
    </>;
  }
  if (r.signal_count > 0) return <>
    <p className="acc-sig-k"><i className="acc-mk on" aria-hidden="true" /><b className="acc-sig-h">{counted(r.signal_count, ["checked signal", "checked signals"], ["sprawdzony sygnał", "sprawdzone sygnały", "sprawdzonych sygnałów"])}</b></p>
    {r.groups.length > 0 && <p className="acc-sig-t">{r.groups.map(groupName).join(", ")}</p>}
    {r.last_signal_at && <p className="acc-sig-meta"><span className="acc-sig-when">{tr("Latest {}", fmtDate(r.last_signal_at))}</span></p>}
  </>;
  if (r.list_lead) return <ListLeadLine lead={r.list_lead} orgId={r.id} orgName={r.name} canCheck={research} onChecked={onChanged} />;
  return <p className="acc-sig-k acc-quiet"><i className="acc-mk off" aria-hidden="true" />{tr("No signal yet")}</p>;
}

/** Headcount, short enough for one line of the row. */
const staff = (n: number, estimate?: boolean) => estimate ? tr("About {} staff", number(n)) : tr("{} staff", number(n));

/** What a company being filled in still lacks, and where Exa is with it. */
function MissingCell({ r }: { r: any }) {
  const last = r.facts_last ?? {};
  const found = (last.written ?? []).map((f: string) => tr(MISSING[f] ?? f).toLowerCase());
  const exa = !r.facts_checked_at ? tr("Exa: in the queue")
    : r.facts_status === "found" ? tr("Exa found {} {}", found.join(", "), ago(r.facts_checked_at))
      : r.facts_status === "failed" ? tr("Exa could not read the pages {}", ago(r.facts_checked_at))
        : tr("Exa found nothing {}", ago(r.facts_checked_at));
  return <div className="acc-miss">
    <span className="acc-miss-list"><span className="sr">{tr("Missing:")} </span>{(r.missing ?? []).map((m: string) => <i key={m}>{tr(MISSING[m] ?? m)}</i>)}</span>
    <span className="acc-miss-exa">{exa}</span>
  </div>;
}

function ContactsCell({ r }: { r: any }) {
  if (!r.people) return <span className="acc-quiet">{tr("No contacts yet")}</span>;
  return <>
    <span className="acc-ppl-n"><b>{number(r.people)}</b> {counted(r.people, ["person", "people"], ["osoba", "osoby", "osób"]).replace(/^[\d\s., ]+/, "")}</span>
    <span className={`acc-mail${r.people_with_email ? " on" : ""}`}>{r.people_with_email ? tr("Email ✓ {}", number(r.people_with_email)) : tr("No email")}</span>
  </>;
}

function AccountRow({ r, view, research, selectable, selected, full, onSelect, onWatch, onDecide, onChanged }: {
  r: any; view: View; research: boolean; selectable: boolean; selected: boolean; full: boolean;
  onSelect: () => void; onWatch: () => void; onDecide: (decision: "qualify" | "reject") => void; onChanged: () => void;
}) {
  const to = `/accounts/${r.id}${view === "filling" && research ? "?profile=1" : ""}`;
  const facts = [r.segment, countryName(r.country_code), r.employees != null ? staff(r.employees, r.employees_uncertain) : null].filter(Boolean);
  return <li className={`acc-row${selected ? " is-sel" : ""}`}>
    {selectable && <label className="acc-pick"><input type="checkbox" aria-label={tr("Select {} for a news check", r.name)} checked={selected} disabled={full} onChange={onSelect} /></label>}
    <span className="acc-av"><CompanyAvatar name={r.name} /></span>
    <div className="acc-id">
      <span className="acc-name-row"><Link className="acc-name" to={to} title={r.name}>{r.name}</Link>
        {r.shortlist && <span className="acc-tag">{tr("Shortlist")}</span>}
        {ORIGIN[r.origin] && <span className="acc-tag">{tr(ORIGIN[r.origin])}</span>}</span>
      <span className="acc-facts" title={facts.join(" · ")}>{facts.length ? facts.map((f, i) => <span key={i}>{f}</span>) : <span>{tr("Details not known yet")}</span>}</span>
      {r.canonical_domain && <span className="acc-domain">{r.canonical_domain}</span>}
    </div>
    <div className="acc-sig">
      <SignalCell r={r} research={research} onChanged={onChanged} />
      {r.candidate_status === "pending_qualification" && research && <div className="acc-decide">
        <button type="button" onClick={() => onDecide("qualify")}>{tr("Confirm account")}</button>
        <button type="button" onClick={() => onDecide("reject")}>{tr("Remove from list")}</button></div>}
    </div>
    <div className="acc-nums">
      <div className="acc-rank"><ListRank priority={r.list_priority} score={r.list_score} /></div>
      <div className="acc-ppl">
        {view === "target" ? <ContactsCell r={r} /> : view === "filling" ? <MissingCell r={r} />
          : <span className="acc-why">{tr(WHY_NOT[r.not_fit] ?? WHY_NOT.other)}</span>}
      </div>
    </div>
    <div className="acc-end">
      <button className="star hit44" aria-pressed={r.watched} aria-label={r.watched ? tr("Stop watching {}", r.name) : tr("Watch {}", r.name)} onClick={onWatch}>{r.watched ? "★" : "☆"}</button>
    </div>
  </li>;
}

