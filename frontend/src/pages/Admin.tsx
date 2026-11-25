import { regionName } from "./targetMarket";
import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api, fmtDateTime, post, put, upload, patch } from "../api";
import { Badge, Err, humanize, lab, Skeleton } from "../components/ui";
import Library from "./Library";
import { KnowledgeAdmin, Pipeline } from "./PipelineStatus";
import Usage from "./Usage";
import { Discovery, Verification } from "./Pipeline";


function Load({ path, children }: { path: string; children: (d: any, reload: () => void) => any }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const reload = () => { setErr(null); return api(path).then(setD).catch(setErr); };
  useEffect(() => { reload(); }, [path]);
  if (err) return <Err e={err} onRetry={reload} />;
  return d ? children(d, reload) : <Skeleton />;
}

function SourceControls({ s, can, reload }: { s: any; can: boolean; reload: () => void }) {
  const [url, setUrl] = useState<string>(s.url ?? "");
  const [licence, setLicence] = useState(tr("Public newsroom page. robots.txt allows access. Reviewed by an administrator."));
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const wrap = async (f: () => Promise<any>, done: (r: any) => string) => { setBusy(true); setErr(null); setMsg(""); try { setMsg(done(await f())); reload(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const v = s.validation;
  return (
    <div className="src-ctl">
      {s.url_basis && s.url && <p className="small muted mt-0 mb-2">{tr("Why this address:") + " "}{s.url_basis}</p>}
      <div className="row">
        <input aria-label={tr("Page URL for {}", s.display_name)} placeholder={`https://… on ${s.canonical_domain}`} value={url} onChange={(e) => setUrl(e.target.value)} className="grow field-md" disabled={!can} />
        <button disabled={!can || busy || url.length < 12} onClick={() => wrap(() => put(`/api/admin/sources/${s.source_id}/config`, { url }), () => "Address saved. Test it next.")}>{tr("Save URL")}</button>
        <button disabled={!can || busy || !s.url} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/validate`), (r) => (r.ok ? "Test passed." : `Test failed: ${r.detail}`))}>{tr("Test from server")}</button>
      </div>
      {v && <p className="small mt-2 mb-0"><Badge kind={v.ok ? "ok" : "bad"}>{v.ok ? tr("Test passed") : tr("Test failed")}</Badge> {v.detail} <span className="muted">· {fmtDateTime(v.at)}</span></p>}
      {can && v?.ok && s.status !== "active" && (
        <div className="mt-3">
          <label className="small">{tr("Permission note: why we may read this page")}<br /><textarea value={licence} onChange={(e) => setLicence(e.target.value)} /></label>
          <label className="small row mt-2 mb-2"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />{" " + tr("I have checked that monitoring this page is permitted")}</label>
          <button className="primary" disabled={busy || !ok || licence.length < 10} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/activate`, { licence_note: licence, confirm_permitted: ok }), () => "Source switched on. It will start within five minutes.")}>{tr("Switch source on")}</button>
        </div>)}
      {can && s.status === "active" && <button className="mt-3" disabled={busy} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/pause`, { reason: "paused by administrator" }), () => "Source paused.")}>{tr("Pause")}</button>}
      {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
    </div>
  );
}


const STATE_KIND: Record<string, "ok" | "warn" | "bad" | "info"> = { live: "ok", built_in: "ok", per_account: "info", needs_address: "warn", blocked_by_site: "bad", needs_credential: "warn", not_built: "info", deferred: "info", needs_retest: "warn", testing: "info" };

function ApolloCap({ r }: { r: any }) {
  const [v, setV] = useState(String(r.cap ?? 0)); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const save = async () => { setErr(null); try { await post("/api/admin/integrations/apollo/cap", { credits: Number(v) }); setMsg("Saved for this month."); } catch (e) { setErr(e); } };
  return <div className="mt-2"><span className="k">{tr("Credits this month")}</span>
    <div className="row"><input aria-label={tr("Monthly credit cap")} type="number" min={0} value={v} onChange={(e) => setV(e.target.value)} className="field-sm" /><button onClick={save}>{tr("Set cap")}</button><span className="small muted">{tr("Spent") + " "}{r.spent ?? 0}{tr(". Price per reveal:") + " "}{r.credits_per_reveal ?? tr("not set (RI_APOLLO_CREDITS_PER_REVEAL)")}{tr(". At zero, no reveal can run.")}</span></div>
    {msg && <div className="okmsg" role="status">{tr(msg)}</div>}<Err e={err} /></div>;
}

/** Pipedrive is not connected and is hidden from the interface for now; the API still lists it. */
const HIDDEN_INTEGRATION = /pipedrive/i;

function Integrations() {
  const [res, setRes] = useState<Record<string, { ok: boolean; detail: string }>>({}); const [busy, setBusy] = useState("");
  const test = async (k: string) => { setBusy(k); try { const r = await post(`/api/admin/integrations/${k}/test`); setRes((x) => ({ ...x, [k]: r })); } catch (e) { setRes((x) => ({ ...x, [k]: { ok: false, detail: e instanceof Error ? e.message : tr("Test failed") } })); } finally { setBusy(""); } };
  return (
    <Load path="/api/admin/integrations">{(d) => (<>
      <p className="sub mb-4">{tr("The accounts we connect to for outreach and contact data. Keys are stored on the server and never shown here. A passing test only proves the key works. Sending also needs its switch turned on and a colleague's approval.")}</p>
      <div className="conn-list">
        {d.items.filter((r: any) => !HIDDEN_INTEGRATION.test(`${r.key} ${r.name}`)).map((r: any) => <article key={r.key} className={`conn ${r.configured ? "live" : "needs_credential"}`}>
          <div className="conn-head"><div><div className="conn-name">{r.name}</div><div className="conn-meta">{r.what}</div></div>
            <div className="row"><Badge kind={r.configured ? "ok" : "warn"}>{r.configured ? tr("Connected") : tr("Not connected")}</Badge>{r.switch && <Badge kind={r.switch_on ? "ok" : ""}>{r.switch_on ? tr("Switched on") : tr("Switched off")}</Badge>}</div></div>
          {r.blocked_reason && <div className="err mt-2 mb-2" role="alert">{r.blocked_reason}</div>}
          <div className="conn-cols"><div><span className="k">{tr("How to connect it")}</span>{r.how}</div>{r.switch && <div><span className="k">{tr("Safety switch")}</span>{r.switch}: {r.switch_on ? tr("on") : tr("off. Nothing is sent or changed until it is turned on.")}</div>}</div>
          {r.key === "apollo" && <ApolloCap r={r} />}
          {r.testable && <div className="row mt-2"><button disabled={!r.configured || busy === r.key} onClick={() => test(r.key)}>{tr("Test the connection")}</button>
            {res[r.key] && <span className={res[r.key].ok ? "okmsg" : "small"} role="status">{res[r.key].detail}</span>}</div>}
        </article>)}
      </div></>)}</Load>
  );
}

const Coverage = () => <Load path="/api/admin/recall">{(d) => (<>
  <p className="sub">{tr("Are we finding what is out there? Each cell counts what we found in the last") + " "}{d.grid.days}{" " + tr("days. A red 0 is worth a look: a source may be broken, or the market may just be quiet.")}</p>
  {d.grid.empty.length > 0 && <div className="err" role="alert">{d.grid.empty.length}{" " + tr("of") + " "}{d.grid.cells.length}{" " + tr("market and topic combinations found nothing in") + " "}{d.grid.days}{" " + tr("days.")}</div>}
  <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Market")}><table><thead><tr><th>{tr("Market")}</th>{d.grid.kinds.map((k: string) => <th key={k}>{k}</th>)}</tr></thead><tbody>
    {d.grid.markets.map((m: string) => <tr key={m}><td><b>{m}</b></td>{d.grid.kinds.map((k: string) => { const c = d.grid.cells.find((x: any) => x.market === m && x.kind === k); return <td key={k}>{c.empty ? <Badge kind="bad">0</Badge> : c.count}</td>; })}</tr>)}</tbody></table></div>
  <div className="card"><b>{tr("Sources finding less than usual")}</b>
    {d.yield_drops.length === 0 ? <p className="small muted">{tr("None. We only judge a source after two weeks of history, and only if it normally finds at least 5 items a week.")}</p>
      : <ul className="tight small">{d.yield_drops.map((x: any) => <li key={x.source_id}><b>{x.source_id}</b>: {x.last_7_days}{" " + tr("this week, usually about") + " "}{x.usual_per_week}</li>)}</ul>}</div>
  <div className="card"><b>{tr("Pages sellers say we missed")}</b>
    <p className="small muted">{d.misses.total === 0 ? tr("None yet. Sellers can paste a link on the Market feed.") : `${d.misses.total} reported: we already had ${d.misses.caught}, and missed ${d.misses.missed}. Each missed page shows which source should have found it.`}</p>
    {d.misses.items.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Page · Result · Reported")}><table><thead><tr><th>{tr("Page")}</th><th>{tr("Did we have it?")}</th><th>{tr("Result")}</th><th>{tr("Reported")}</th></tr></thead><tbody>
      {d.misses.items.map((m: any) => <tr key={m.id}><td><a href={m.url} target="_blank" rel="noreferrer noopener">{m.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a>{m.note && <div className="small muted">{m.note}</div>}</td>
        <td>{m.we_already_had_it ? <Badge kind="ok">{tr("Yes (")}{m.from_source})</Badge> : <Badge kind="bad">{tr("No")}</Badge>}</td><td className="small">{humanize(m.status)}{m.events_created > 0 ? ` · ${m.events_created} finding(s)` : ""}</td><td className="small">{fmtDateTime(m.reported_at)}</td></tr>)}</tbody></table></div>}</div></>)}</Load>;

const Precision = () => <Load path="/api/admin/precision">{(d) => (<>
  <p className="sub">{tr("What sellers did with each signal in the last") + " "}{d.window_days}{" " + tr("days. This only reports: a person decides whether a signal changes. A dismissal is an opinion, so read the top reason first.")}</p>
  {d.flagged.length > 0 && <div className="err" role="alert">{tr("Dismissed more than 60% of the time, with enough decisions to judge:") + " "}<b>{d.flagged.join(", ")}</b></div>}
  <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Signal · Fired · Pursued")}><table><thead><tr><th>{tr("Signal")}</th><th>{tr("Fired")}</th><th>{tr("Pursued")}</th><th>{tr("Dismissed")}</th><th>{tr("Dismissed share")}</th><th>{tr("Main reason")}</th><th><span className="sr">{tr("Flag")}</span></th></tr></thead><tbody>
    {d.rules.map((r: any) => <tr key={r.rule_id}><td>{r.rule_id}</td><td>{r.fired}</td><td>{r.accepted}</td><td>{r.dismissed}</td><td>{r.dismissal_rate === null ? "n/a" : `${Math.round(r.dismissal_rate * 100)}%`}<div className="small muted">{r.enough_data ? tr("likely range {} to {}%", Math.round(r.interval_95[0] * 100), Math.round(r.interval_95[1] * 100)) : tr("Too few decisions to judge")}</div></td>
      <td className="small">{r.top_dismiss_reason ? lab("dismiss", r.top_dismiss_reason) : tr("None")}</td><td>{r.flag && <Badge kind="bad">{tr("Look at this")}</Badge>}</td></tr>)}
    {d.rules.length === 0 && <tr><td colSpan={7} className="muted">{tr("No signals fired in this period.")}</td></tr>}</tbody></table></div>
  <div className="card"><b>{tr("Outreach results")}</b><div className="chips mt-2">{Object.keys(d.outreach).length === 0 ? <span className="muted small">{tr("Nobody added to a sequence yet.")}</span> : Object.entries(d.outreach).map(([k, n]) => <Badge key={k}>{humanize(k)}: {n as number}</Badge>)}</div></div>
  <ul className="tight small muted">{d.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></>)}</Load>;

function Connections() {
  const [filter, setFilter] = useState("");
  return (
    <Load path="/api/admin/connections">{(d) => {
      const items = d.items.filter((r: any) => !HIDDEN_INTEGRATION.test(`${r.source_id} ${r.name}`));
      const counts = Object.keys(d.counts).map((k) => [k, items.filter((r: any) => r.state === k).length] as [string, number]).filter(([, n]) => n > 0);
      const rows = items.filter((r: any) => !filter || r.state === filter);
      return (<>
        <p className="sub mb-4">{tr("Every place we read from, what state it is in, what it needs to be switched on, and where to get that. “Runs per account” means it is found for each account when you press “Check for news” on that account.")}</p>
        <div className="chips" role="group" aria-label={tr("Filter by state")}>
          <button className="chip" aria-pressed={!filter} onClick={() => setFilter("")}>{tr("All")}<b>{items.length}</b></button>
          {counts.map(([k, n]) => <button key={k} className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{tr(d.labels[k])}<b>{n}</b></button>)}
        </div>
        <div className="conn-list">
          {rows.map((r: any) => <article key={r.source_id} className={`conn ${r.state}`}>
            <div className="conn-head">
              <div><div className="conn-name">{r.name}</div><div className="conn-meta">{humanize(r.family)}{r.countries?.length ? ` · ${r.countries.join(", ")}` : ""}</div></div>
              <Badge kind={STATE_KIND[r.state] ?? "info"}>{tr(r.state_label)}</Badge>
            </div>
            <div className="conn-cols">
              <div><span className="k">{tr("What to do next")}</span>{tr(r.what_to_do)}{r.last_error && <div className="small muted">{tr("Last problem:") + " "}{r.last_error}</div>}</div>
              <div><span className="k">{tr("Where to get it")}</span>{r.where_to_get ? tr(r.where_to_get) : <span className="muted">{tr("Nothing to obtain")}</span>}</div>
            </div>
            <div className="small muted">
              {r.documents ? <>{r.documents.toLocaleString()}{" " + tr("pages read · latest") + " "}{fmtDateTime(r.last_document_at)}</> : tr("Nothing read yet")}
              {r.last_success_at && <>{" " + tr("· last success") + " "}{fmtDateTime(r.last_success_at)}</>}
              {r.address && <div className="conn-addr">{r.address}</div>}
            </div>
          </article>)}
          {rows.length === 0 && <div className="empty"><b>{tr("Nothing in this state")}</b>{tr("Pick another filter above.")}</div>}
        </div></>);
    }}</Load>
  );
}

function ScanTop() {
  const [n, setN] = useState(10); const [search, setSearch] = useState(false); const [financeOnly, setFinanceOnly] = useState(true);
  const [showPilot, setShowPilot] = useState(true); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(null); setMsg(""); try { const r = await post("/api/admin/scan-accounts", { limit: n, with_search: search, finance_only: financeOnly }); setMsg(tr("Checking {} {}. {} Results appear in the Market feed as we check them.", r.queued, r.queued === 1 ? "account" : "accounts", r.note)); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <div className="card">
      <b>{tr("Check your priority accounts for news")}</b>
      <div className="small muted mt-1 mb-3">{tr("Reads company news, career pages, job boards and public supplier pages")}{search ? tr(" and runs one web search per account") : ""}{tr(". The finance pilot checks up to 25 companies daily within the existing usage limits.")}</div>
      <div className="row">
        <label className="small">{tr("How many accounts") + " "}<input type="number" min={1} max={300} value={n} onChange={(e) => setN(Number(e.target.value))} className="field-xs" /></label>
        <label className="small row"><input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} />{" " + tr("Include one web search per account (uses the Exa budget)")}</label>
        <label className="small row"><input type="checkbox" checked={financeOnly} onChange={(e) => setFinanceOnly(e.target.checked)} />{" " + tr("Banking and financial services only")}</label>
        <button className="primary" disabled={busy || n < 1} onClick={go}>{tr("Check these accounts")}</button>
      </div>
      <button className="mt-2" aria-expanded={showPilot} onClick={() => setShowPilot(!showPilot)}>{showPilot ? tr("Hide finance pilot") : tr("View finance pilot")}</button>
      {showPilot && <Load path="/api/admin/source-pilot">{(d, refresh) => (
        <div className="mt-2">
          <div className="row spread"><h2 className="h-sub">{tr("Finance monitoring pilot")}</h2><button onClick={refresh}>{tr("Refresh results")}</button></div>
          <p className="small muted">{tr("Shows checks of company pages and up to five verified findings per account from the last 30 days, with buying signals listed first.")}</p>
          {d.items.length === 0 && <p className="small muted">{tr("No companies match the finance pilot criteria.")}</p>}
          {d.items.length > 0 && <div className="card">
            <b>{tr("Pilot coverage by region")}</b>
            <ul className="tight small">{Array.from(new Set<string>(d.items.map((item: any) => item.region))).map(region => {
              const companies = d.items.filter((item: any) => item.region === region);
              const withoutWorkingSources = companies.filter((item: any) => !item.endpoints.some((source: any) => source.last_status === "ok")).length;
              return <li key={region}>{regionName(region)} · {tr("{} companies", companies.length)} · {tr("{} without a successful source check", withoutWorkingSources)}</li>;
            })}</ul>
          </div>}
          {d.items.map((account: any) => <details key={account.id} className="card">
            <summary>{account.name} · {account.confirmed ? tr("Target account") : tr("Ownership to check")} · {account.endpoints.length}{" " + tr("company sources ·") + " "}{account.recent.length === 5 ? "5+" : account.recent.length}{" " + tr("recent findings")}</summary>
            <p className="small"><Link to={`/accounts/${account.id}`}>{tr("Open company")}</Link></p>
            <p className="small muted">{account.scan ? <>{tr("Scheduled check ·") + " "}{account.scan.status === "completed" ? tr(({ no_website: "Company website missing", no_sources: "No sources found", all_sources_failed: "All source checks failed", partially_checked: "Some source checks failed" } as Record<string, string>)[account.scan.outcome] ?? (account.scan.result?.endpoints === 0 ? "No sources found" : account.scan.result?.scanned === 0 ? "All source checks failed" : "Scan completed")) : humanize(account.scan.status)} · {fmtDateTime(account.scan.updated_at)}</> : tr("Waiting for the first scheduled check.")}</p>
            {account.scan?.status === "completed" && account.scan?.result && <p className="small">
              {tr("New or changed materials: {}", account.scan.result.documents ?? 0)} · {tr("New findings in this scan: {}", account.scan.result.processed?.events_created ?? 0)}
              {account.scan.result.error && <> · {humanize(account.scan.result.error)}</>}
              {account.scan.result.processed?.deferred_provider === "provider_billing" && <span className="coverage-warning">{tr("New pages are waiting for verification because the provider is out of credit.")}</span>}
            </p>}
            {account.scan?.last_error && <p className="small">{tr("Last scan error")} · {account.scan.last_error}</p>}
            {account.endpoints.length > 0 && !account.endpoints.some((source: any) => source.kind === "procurement") && <p className="small muted">{tr("No supplier page found")}</p>}
            {account.endpoints.length === 0 ? <p className="small muted">{tr("No company sources configured.")}</p> :
              <ul className="tight small">{account.endpoints.map((source: any) => <li key={source.url}>
                <a href={source.url} target="_blank" rel="noreferrer noopener">{source.kind === "procurement" ? tr("Supplier page") : humanize(source.kind)}</a>
                {source.last_scanned_at ? <> · {source.last_status === "ok" ? tr("Checked") : tr("Check failed")} {fmtDateTime(source.last_scanned_at)} · {source.docs_seen}{" " + tr("items read")}</> : <>{" " + tr("· waiting for first check")}</>}
                {source.last_success_at && <> · {tr("Last successful check")} {fmtDateTime(source.last_success_at)}</>}{source.last_status !== "ok" && source.last_detail && <div className="muted">{source.last_detail}</div>}
              </li>)}</ul>}
            {account.recent.map((finding: any) => <div className="mt-2" key={finding.event_id}>
              <b>{finding.summary}</b> <span className="small muted">{fmtDateTime(finding.date)}</span>
              <div className="small">{finding.demand_tier ? ({ D1: tr("Explicit outside demand"), D2: tr("Inferred outside demand"), D3: tr("Verified programme"), D4: tr("Early context") } as Record<string, string>)[finding.demand_tier] : tr("Verified finding · no current buying signal")}</div>
              <ul className="tight small">{finding.sources.map((source: any) => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer noopener">{source.source}</a></li>)}</ul>
            </div>)}
          </details>)}
        </div>
      )}</Load>}
      {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
    </div>
  );
}

function Sources({ canEdit }: { canEdit: boolean }) {
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const bulk = async (activate: boolean) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post("/api/admin/sources/validate-all", { activate }); setMsg(activate ? "Testing every source now. Each one that passes is switched on. Refresh in a minute to see the result on each card." : tr("Testing every source now. Nothing is switched on.")); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (<>
    {canEdit && <ScanTop />}
    {canEdit && <div className="card row spread"><div><b>{tr("Test all sources")}</b><div className="small muted">{tr("Tests each source from the server. We only read pages the site allows (robots.txt). Sources missing an address or a key are skipped and say why.")}</div></div>
      <div className="row"><button disabled={busy} onClick={() => bulk(false)}>{tr("Test only")}</button><button className="primary" disabled={busy} onClick={() => bulk(true)}>{tr("Test, then switch on those that pass")}</button></div></div>}
    {msg && <div className="okmsg mb-3" role="status">{tr(msg)}</div>}<Err e={err} />
    <Library render={(s, reload) => <SourceControls s={s} can={canEdit} reload={reload} />} />
  </>);
}

const Identity = () => <Load path="/api/admin/identity-reviews">{(d, reload) => <IdentityList d={d} reload={reload} />}</Load>;
function IdentityList({ d, reload }: { d: any; reload: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [note, setNote] = useState("");
  const act = async (id: string, decision: string, org?: string) => { try { await post(`/api/admin/identity-reviews/${id}/resolve`, { decision, organization_id: org, note }); reload(); } catch (e) { setErr(e); } };
  return <><Err e={err} /><p className="small muted">{tr("A parent company and its subsidiary are linked, never merged. Every decision needs a reason and is logged.")}</p><input aria-label={tr("Your reason")} placeholder={tr("Your reason (required)")} value={note} onChange={(e) => setNote(e.target.value)} className="field-lg mb-3" />
    {d.items.length === 0 && <div className="empty"><b>{tr("Nothing to match")}</b>{tr("Every company name from your workbooks has been matched.")}</div>}
    {d.items.map((r: any) => <div key={r.id} className="card"><b>{r.raw_company_name}</b> <Badge kind="warn">{r.reason === "ambiguous_match" ? tr("Could be more than one company") : humanize(r.reason)}</Badge> <span className="muted small">{r.sheet_title ? tr("from {}, row {}", r.sheet_title, r.row_number) : ""}</span>
      <div className="row mt-2">{r.candidates.map((c: any) => <button key={c.id} disabled={note.length < 3} onClick={() => act(r.id, "link", c.id)}>{tr("Link to") + " "}{c.display_name} ({c.country_code ?? "?"})</button>)}
        <button disabled={note.length < 3} onClick={() => act(r.id, "new")}>{tr("It is a new company")}</button><button disabled={note.length < 3} onClick={() => act(r.id, "reject")}>{tr("Ignore this row")}</button></div></div>)}</>;
}

const TWO_PERSON = new Set(["signal_campaign_push", "contact_enrichment", "crm_write", "crm_activity", "outbound_message", "sequence_enrolment", "supplier_application", "quote_or_commitment", "proof_release"]);
const APPROVAL_FIELDS: Record<string, string> = { subject: "Subject", type: "Activity type", note: "Note", due_date: "Due date", org_id: "Pipedrive organisation", person_id: "Contact ID", delay_days: "Days after the previous email" };

function PayloadDetails({ payload }: { payload: Record<string, unknown> }) {
  const readable = (v: unknown): React.ReactNode => {
    if (Array.isArray(v)) return <ol className="tight">{v.map((item, i) => <li key={i}>{readable(item)}</li>)}</ol>;
    if (v && typeof v === "object") return <PayloadDetails payload={v as Record<string, unknown>} />;
    if (typeof v === "boolean") return v ? tr("Yes") : tr("No");
    return String(v);
  };
  const values = Object.entries(payload).filter(([, v]) => v != null && v !== "");
  return <dl className="approval-payload">{values.map(([k, v]) => <div key={k}>
    <dt>{APPROVAL_FIELDS[k] ?? humanize(k)}</dt><dd>{readable(v)}</dd>
  </div>)}</dl>;
}

/** A signal campaign approval: who gets it and the exact emails, without dumping every lead's variables. */
function CampaignPayload({ p }: { p: any }) {
  const members: any[] = p.members ?? [];
  const companies = new Set(members.map((m) => m.organization_id)).size;
  return <div className="approval-payload">
    <p className="m-0"><b>{p.campaign_name}</b>: {tr("{} people at {} companies", members.length, companies)} · <Link to={`/campaigns/${p.campaign_id}`}>{tr("Review the people and copy")}</Link></p>
    <details className="mt-2"><summary>{tr("The emails ({})", (p.steps ?? []).length)}</summary>
      {(p.steps ?? []).map((st: any, i: number) => <div key={i} className="mail"><div className="small muted">{i === 0 ? tr("First email") : tr("Follow-up after {} days", st.delay_days)}</div><b>{st.subject}</b><div className="mail-body">{st.body}</div></div>)}</details>
    <details className="mt-2"><summary>{tr("Who gets it ({})", members.length)}</summary>
      <ul className="tight small">{members.map((m) => <li key={m.member_id}>{m.variables?.firstName} {m.variables?.lastName}, {m.variables?.jobTitle} · {m.variables?.companyName} · {m.email}</li>)}</ul></details>
  </div>;
}

function Approvals({ me, refresh }: { me: AdminUser; refresh: () => void }) {
  return <Load path="/api/approvals">{(d, reload) => <ApprovalList d={d} me={me} reload={() => { void reload(); refresh(); }} />}</Load>;
}
function ApprovalList({ d, reload, me }: { d: any; reload: () => void; me: AdminUser }) {
  const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (path: string, body?: unknown) => {
    setBusy(true); setErr(null); setMsg("");
    try {
      const result = await post(path, body);
      setMsg(result.status === "approved" ? tr("Approved. Press Run now to carry it out.")
        : result.status === "executed" ? result.note ?? tr("Action completed.")
        : result.status === "rejected" ? "Rejected." : tr("Request status: {}.", humanize(result.status)));
      reload();
    } catch (e) { setErr(e); }
    finally { setBusy(false); }
  };
  const canDecide = me.permissions.includes("approvals.decide");
  return <><Err e={err} />{msg && <div className="okmsg" role="status">{tr(msg)}</div>}{d.items.length === 0 && <div className="empty"><b>{tr("Nothing waiting")}</b>{tr("Requests for signal campaigns, outreach and email reveals will appear here.")}</div>}
    {d.items.map((a: any) => <div id={`approval-${a.id}`} key={a.id} className="card"><div className="row spread"><b>{lab("approval", a.operation === "create_activity" ? "crm_activity" : a.kind)}{a.payload?.subject ? `: ${a.payload.subject}` : ""}</b><Badge kind={a.status === "approved" ? "ok" : a.status === "pending" ? "warn" : a.status === "executed" ? "info" : ""}>{({ pending: tr("Waiting for approval"), approved: tr("Approved, not run yet"), executed: tr("Done"), rejected: tr("Rejected"), expired: tr("Expired") } as Record<string, string>)[a.status] ?? humanize(a.status)}</Badge></div>
      <div className="small">{tr("For") + " "}{a.target_system === "pipedrive" ? "Pipedrive" : humanize(a.target_system)}{" " + tr("· requested by") + " "}{a.requested_by}{" " + tr("· expires") + " "}{fmtDateTime(a.expires_at)}</div>
      {a.kind === "signal_campaign_push" ? <CampaignPayload p={a.payload ?? {}} /> : <PayloadDetails payload={a.payload ?? {}} />}
      {a.justification && <div className="small muted">{tr("Why:") + " "}{a.justification}</div>}
      {a.status === "pending" && TWO_PERSON.has(a.kind) && <p className="small muted">{tr("Someone other than the requester must approve this.")}</p>}
      {canDecide && <div className="row mt-2">
        {a.status === "pending" && <>
          <button className="primary" disabled={busy || (TWO_PERSON.has(a.kind) && a.requested_by === me.email)} onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: true })}>{tr("Approve this")}</button>
          <button disabled={busy || (TWO_PERSON.has(a.kind) && a.requested_by === me.email)} onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: false })}>{tr("Reject")}</button>
        </>}
        {a.status === "approved" && <button disabled={busy} onClick={() => go(`/api/approvals/${a.id}/execute`)}>{tr("Run now")}</button>}
      </div>}
    </div>)}</>;
}

const Jobs = () => <Load path="/api/admin/jobs">{(d, reload) => <><div className="row">{d.by_status.map((s: any) => <Badge key={s.status} kind={s.status.includes("failed") ? "bad" : ""}>{({ done: tr("Done"), queued: tr("Waiting"), running: tr("Running"), retrying: tr("Retrying"), permanent_failed: tr("Failed for good") } as Record<string, string>)[s.status] ?? humanize(s.status)}: {s.n}</Badge>)}</div>
  <h3>{tr("Jobs that failed for good")}</h3>{d.dead_letters.length === 0 && <div className="empty"><b>{tr("None")}</b>{tr("Nothing has failed for good.")}</div>}{d.dead_letters.length > 0 && <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Job · Problem · Detail")}><table><thead><tr><th>{tr("Job")}</th><th>Problem</th><th>{tr("Detail")}</th><th><span className="sr">{tr("Action")}</span></th></tr></thead><tbody>{d.dead_letters.map((x: any) => <tr key={x.id}><td>{x.kind}</td><td>{x.error_class}</td><td>{x.error}</td><td>{x.replayed_at ? tr("Retried") : <button onClick={() => post(`/api/admin/dead-letters/${x.id}/replay`).then(reload)}>{tr("Try again")}</button>}</td></tr>)}</tbody></table></div>}</>}</Load>;

function BudgetDetails() {
  return <Load path="/api/admin/costs">{(d) => <>
    <p className="small muted">{tr(d.note)}</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Provider budgets")}>
      <table><thead><tr><th>{tr("Provider")}</th><th>{tr("Kind")}</th><th>{tr("Period")}</th><th>Limit</th><th>{tr("Set aside")}</th><th>{tr("Spent")}</th></tr></thead>
        <tbody>{d.budgets.map((b: any) => <tr key={`${b.provider}-${b.kind}-${b.period}`}><td>{humanize(b.provider)}</td><td>{humanize(b.kind)}</td><td>{b.period}</td><td>{b.cap}</td><td>{b.reserved}</td><td>{b.spent}</td></tr>)}
          {d.budgets.length === 0 && <tr><td colSpan={6}>{tr("No budgets configured.")}</td></tr>}</tbody></table>
    </div>
  </>}</Load>;
}

function Audit() {
  const [limit, setLimit] = useState(100);
  return <Load path={`/api/admin/audit?limit=${limit}`}>{(d) => <>
    <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Audit log")}><table><thead><tr><th>{tr("When")}</th><th>{tr("Who")}</th><th>{tr("What they did")}</th><th>{tr("About")}</th><th>{tr("Approval")}</th></tr></thead>
      <tbody>{d.items.map((a: any) => {
        const type = a.record_type;
        const link = type === "opportunity_hypothesis" ? "/opportunities/" : type === "organization" ? "/accounts/" : null;
        const name = type === "opportunity_hypothesis" ? tr("Opportunity") : type === "organization" ? tr("Account") : humanize(type);
        return <tr key={a.id}><td>{fmtDateTime(a.at)}</td><td>{a.actor}</td><td>{a.operation === "evaluate_hypothesis" ? tr("Scored opportunity") : humanize(a.operation)}</td>
          <td>{link && a.record_id ? <Link to={link + a.record_id}>{name}</Link> : name} <span className="small muted">{a.record_id?.slice(0, 8)}</span></td>
          <td>{a.approval_id ? a.approval_id.slice(0, 8) : tr("None")}</td></tr>;
      })}</tbody></table></div>
    {d.items.length >= limit && limit < 500 && <button onClick={() => setLimit((n) => n + 100)}>{tr("Show older entries")}</button>}
    {d.items.length >= 500 && <p className="small muted">{tr("Showing the latest 500 entries.")}</p>}
  </>}</Load>;
}

const QUALITY_LABELS: Record<string, [string, string]> = {
  stale_sources: ["Sources gone quiet", "Active sources that haven't produced anything recently."],
  dead_letters_unreplayed: ["Failed jobs not retried", "Jobs that failed for good and haven't been replayed. See Jobs."],
  alerts_sent: ["Alerts sent", "Alerts sent to the team."],
  duplicate_alert_groups: ["Duplicate alerts", "Stories alerted more than once. Should be zero."],
  high_priority_without_citation: ["High priority without a source", "High-priority cards with no cited source. Should be zero."],
  open_identity_reviews: ["Company matches to review", "Companies we couldn't match with confidence. See Identity review."],
  unverified_events: ["Findings not checked yet", "Findings waiting to be checked against the original page."],
  unauthorised_writes_detected: ["Unapproved changes detected", "Outreach or other outside changes made without an approval. Should be zero."],
  pending_approvals: ["Approvals waiting", "Requests waiting for a second person. See Approvals."],
};
const Quality = () => <Load path="/api/admin/metrics">{(d) => <><p className="sub mb-4">{tr("Health checks on the platform itself. Anything marked \"should be zero\" and isn't is worth a look.")}</p>
  <div className="kpis">{Object.entries(d).filter(([, v]) => typeof v === "number").map(([k, v]: any) => { const [t, hint] = QUALITY_LABELS[k] ?? [humanize(k), ""]; return <div key={k} className="card kpi"><div className="eyebrow">{tr(t)}</div><div className="kpi-n">{v}</div>{hint && <div className="small muted">{tr(hint)}</div>}</div>; })}</div>
  <Load path="/api/admin/funnel">{(f) => <><h2 className="h-sub">{tr("Outcome funnel")}</h2>{Object.keys(f.cohorts).length === 0 && <div className="empty"><b>{tr("No outcomes recorded yet")}</b>{tr("Nothing here is inferred.")}</div>}{Object.entries(f.cohorts).map(([k, c]: any) => <div key={k} className="card"><b>{humanize(k)}</b>{" " + tr("· presented") + " "}{c.presented} {c.insufficient_sample && <Badge kind="warn">{tr("too few to judge")}</Badge>}</div>)}<ul className="tight small muted">{f.caveats.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}</Load></>}</Load>;

function Imports() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/admin/imports").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 8000); return () => clearInterval(t); }, []);
  const send = async (f: File | undefined) => {
    if (!f) return; setBusy(true); setErr(null); setMsg("");
    try { const r = await upload("/api/admin/import/upload", f); setMsg(tr("Workbook {} received. It is being loaded now, which can take several minutes.", r.workbook)); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const sync = async (wb: string) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post(`/api/admin/import/sync?workbook=${wb}`); setMsg(tr("Sync of workbook {} started. This can take several minutes.", wb)); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const gs = d?.google_sheets;
  return (
    <>
      <div className="card">
        <div className="eyebrow">{tr("Connect Google Sheets (no uploads needed)")}</div>
        {gs?.configured ? <p className="mt-0"><Badge kind="ok">{tr("Connected")}</Badge>{" " + tr("Share both workbooks with") + " "}<b>{gs.service_account_email ?? tr("the service account")}</b>{" " + tr("as Viewer, then press Sync. Each sync re-reads the sheets and keeps the data up to date.")}</p>
          : <ol className="small mt-0">
              <li>{tr("In Google Cloud, create a service account in any project and enable the Google Sheets API. Create a JSON key for it.")}</li>
              <li>{tr("In Render, add the whole JSON as the secret") + " "}<code>GOOGLE_SERVICE_ACCOUNT_JSON</code>{" " + tr("on the API and the worker.")}</li>
              <li>{tr("Open each workbook in Google Sheets, press Share, and add the service account's email as Viewer.")}</li>
              <li>{tr("Come back here and press Sync. If your Google admin blocks sharing outside the company, upload the exports below instead.")}</li>
            </ol>}
        <div className="row">
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("A")}>{tr("Sync workbook A")}</button>
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("B")}>{tr("Sync workbook B")}</button>
          {gs?.workbooks && <><a href={gs.workbooks.A} target="_blank" rel="noreferrer">{tr("Open A")}</a><a href={gs.workbooks.B} target="_blank" rel="noreferrer">{tr("Open B")}</a></>}
        </div>
      </div>
      <div className="card">
        <div className="eyebrow">{tr("Or upload a workbook export")}</div>
        <p className="mt-0">{tr("In Google Sheets choose") + " "}<b>{tr("File, Download, Microsoft Excel (.xlsx)")}</b>{" " + tr("for each workbook, then upload it here. We work out which workbook it is, check every tab's column headings against the agreed layout, and load it in the background. A renamed or reordered column pauses that tab instead of guessing.")}</p>
        <input type="file" accept=".xlsx" aria-label={tr("Workbook file (.xlsx)")} disabled={busy} onChange={(e) => { void send(e.target.files?.[0]); e.target.value = ""; }} />
        {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
        <p className="small muted mb-0">{tr("Uploading the same file again changes nothing. We delete the file after loading it and keep only its fingerprint and the result.")}</p>
      </div>
      <h3>{tr("Recent uploads")}</h3>
      {!d ? <Skeleton /> : d.uploads.length === 0 ? <div className="empty">{tr("No uploads yet.")}</div> :
        <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("When · File · Workbook")}><table><thead><tr><th>{tr("When")}</th><th>{tr("File")}</th><th>{tr("Workbook")}</th><th>{tr("Status")}</th><th>{tr("Detail")}</th></tr></thead><tbody>{d.uploads.map((u: any) => {
          const tabs = u.stats?.tabs ? Object.entries(u.stats.tabs) as [string, any][] : [];
          return <tr key={u.id}><td>{fmtDateTime(u.created_at)}</td><td>{u.filename}<div className="small muted">{u.uploaded_by}</div></td><td>{u.workbook_key}</td>
            <td><Badge kind={u.status === "completed" ? "ok" : u.status === "failed" ? "bad" : "info"}>{({ completed: "Imported", failed: tr("Failed"), running: tr("Loading"), queued: tr("Waiting") } as Record<string, string>)[u.status] ?? humanize(u.status)}</Badge></td>
            <td className="small">{u.error ?? <>{tabs.filter(([, v]) => v.status !== "unknown_tab").map(([k, v]) => `${k}: ${TAB_STATUS[v.status] ? tr(TAB_STATUS[v.status]) : v.status}${v.rows !== undefined ? ` (${number(v.new ?? 0) + " " + tr("new")}, ${number(v.rows - (v.blank ?? 0))} ${tr("rows")})` : ""}`).join(" · ")}
              {tabs.some(([, v]) => v.status === "unknown_tab") && <div className="muted">{tr("{} other tabs not imported (archives and filtered views)", number(tabs.filter(([, v]) => v.status === "unknown_tab").length))}</div>}</>}</td></tr>; })}</tbody></table></div>}
    </>
  );
}

const TAB_STATUS: Record<string, string> = { ok: "imported", drift: "columns changed, paused", missing: "tab not found", failed: "failed" };

const ROLES = ["bdm", "sales_manager", "research_analyst", "marketing", "delivery_expert", "admin", "compliance"];
function Users() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const [f, setF] = useState({ email: "", display_name: "", role: "bdm", team: "default" });
  const load = () => api("/api/admin/users").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = async (fn: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await fn(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  return (
    <>
      <div className="card">
        <div className="eyebrow">{tr("Add a person")}</div>
        <p className="small muted mt-0">{tr("They sign in with Google using this address")}{d?.domain ? ` (only @${d.domain} is accepted)` : ""}.</p>
        <div className="row">
          <input aria-label={tr("Work email")} placeholder="name@thecodest.co" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className="field-md" />
          <input aria-label={tr("Display name")} placeholder={tr("Display name")} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <select aria-label={tr("Role")} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select>
          <input aria-label={tr("Team")} placeholder={tr("Team")} value={f.team} onChange={(e) => setF({ ...f, team: e.target.value })} className="field-sm" />
          <button className="primary" disabled={!f.email.includes("@") || !f.display_name} onClick={() => run(async () => { await post("/api/admin/users", f); setF({ ...f, email: "", display_name: "" }); }, "Person added. They can sign in now.")}>{tr("Add")}</button>
        </div>
        {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
      </div>
      {d && <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Person · Role · Team")}><table><thead><tr><th>{tr("Person")}</th><th>{tr("Role")}</th><th>{tr("Team")}</th><th>{tr("Status")}</th><th><span className="sr">{tr("Actions")}</span></th></tr></thead><tbody>{d.items.map((u: any) => <tr key={u.id}>
        <td>{u.display_name}<div className="small muted">{u.email}</div></td>
        <td><select aria-label={tr("Role for {}", u.email)} value={u.role} onChange={(e) => run(() => patch(`/api/admin/users/${u.id}`, { role: e.target.value }), "Role updated.")}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select></td>
        <td>{u.team}</td><td><Badge kind={u.active ? "ok" : ""}>{u.active ? tr("Active") : "Deactivated"}</Badge></td>
        <td><button className="ghost" onClick={() => run(() => patch(`/api/admin/users/${u.id}`, { active: !u.active }), u.active ? "Deactivated." : "Reactivated.")}>{u.active ? "Deactivate" : "Reactivate"}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}

type AdminUser = { email: string; permissions: string[] };

function SubNav({ me, waiting }: { me: AdminUser; waiting: number }) {
  const p = (x: string) => me.permissions.includes(x);
  const [expanded, setExpanded] = useState(false);
  const L = (show: boolean, to: string, label: string, end = false, badge = 0) => show ? <NavLink key={to} to={to} end={end} onClick={() => setExpanded(false)}>{label}{badge > 0 && <span className="tabn">{badge}</span>}</NavLink> : null;
  const groups: [string, (React.ReactNode)[]][] = [
    [tr("Needs a person"), [L(p("approvals.read_own") || p("approvals.read_all"), "/admin/approvals", tr("Approvals"), false, waiting), L(p("identity.manage"), "/admin/identity", tr("Company matches")), L(p("sources.read"), "/admin/verification", tr("Pages being checked"))]],
    [tr("Monitoring health"), [L(p("admin.jobs"), "/admin/pipeline", tr("Processing pipeline")), L(p("admin.jobs"), "/admin/jobs", tr("Failed jobs")), L(p("admin.costs"), "/admin/usage", tr("Usage and cost")), L(p("admin.rules"), "/admin/coverage", tr("Are we finding things?")), L(p("admin.rules"), "/admin/quality", tr("Health checks"))]],
    [tr("Sources and imports"), [L(p("sources.read"), "/admin", tr("Sources"), true), L(p("sources.read"), "/admin/connections", tr("Where we read from")), L(p("admin.sources"), "/admin/imports", tr("Workbooks")), L(p("admin.costs"), "/admin/discovery", tr("Web search")), L(p("admin.sources"), "/admin/knowledge", tr("Knowledge base"))]],
    [tr("Signals"), [L(p("admin.rules"), "/admin/precision", tr("Which signals work"))]],
    [tr("Access and integrations"), [L(p("admin.users"), "/admin/users", tr("People")), L(p("admin.costs"), "/admin/integrations", tr("Connected services")), L(p("audit.read"), "/admin/audit", tr("Audit log"))]],
  ];
  return <div><button type="button" className="admin-nav-toggle" aria-expanded={expanded} aria-controls="admin-sections" onClick={() => setExpanded(!expanded)}>{tr("Administration sections")}</button><nav id="admin-sections" className={`subnav${expanded ? " expanded" : ""}`} aria-label={tr("Administration sections")}>{groups.map(([g, items]) => items.some(Boolean) ? <div key={g} className="subnav-g"><div className="subnav-h">{tr(String(g))}</div>{items}</div> : null)}</nav></div>;
}

export default function Admin({ me, waiting, refreshApprovals }: { me: AdminUser; waiting: number; refreshApprovals: () => void }) {
  const p = (x: string) => me.permissions.includes(x);
  return (
    <>
      <div className="page-head"><div><h1>{tr("Administration")}</h1><p className="sub">{tr("Monitor data collection, review issues and manage integrations.")}</p></div></div>
      <div className="admin-shortcuts">
        {p("sources.read") && <Link to="/admin/verification"><b>{tr("Pages being checked")}</b><span>{tr("Find stalled checks and open their original sources.")}</span></Link>}
        {p("admin.rules") && <Link to="/admin/quality"><b>{tr("Health checks")}</b><span>{tr("See whether scheduled work is completing.")}</span></Link>}
        {p("sources.read") && <Link to="/admin/connections"><b>{tr("Source connections")}</b><span>{tr("See what runs on a schedule and what needs setup.")}</span></Link>}
      </div>
      <div className="admin-grid">
        <SubNav me={me} waiting={waiting} />
        <div className="admin-main">
          <Routes><Route index element={<Sources canEdit={p("admin.sources")} />} /><Route path="pipeline" element={<Pipeline />} /><Route path="connections" element={<Connections />} /><Route path="integrations" element={<Integrations />} /><Route path="knowledge" element={<KnowledgeAdmin />} /><Route path="imports" element={<Imports />} /><Route path="users" element={<Users />} /><Route path="identity" element={<Identity />} /><Route path="approvals" element={<Approvals me={me} refresh={refreshApprovals} />} /><Route path="jobs" element={<Jobs />} /><Route path="costs" element={<Navigate to="/admin/usage" replace />} /><Route path="usage" element={<><Usage /><details className="card mt-4"><summary>{tr("All provider budgets")}</summary><BudgetDetails /></details></>} /><Route path="discovery" element={<Discovery />} /><Route path="verification" element={<Verification />} />
            <Route path="rules" element={<Navigate to="/library?tab=signals" replace />} /><Route path="precision" element={<Precision />} /><Route path="coverage" element={<Coverage />} /><Route path="deadlines" element={<Navigate to="/admin" replace />} /><Route path="quality" element={<Quality />} /><Route path="audit" element={<Audit />} /></Routes>
        </div>
      </div>
    </>
  );
}
