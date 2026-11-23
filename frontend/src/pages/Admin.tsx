import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, fmtDateTime, post, put, upload, patch } from "../api";
import { Badge, Err, humanize, lab, Skeleton } from "../components/ui";
import Library from "./Library";
import { KnowledgeAdmin, Pipeline } from "./PipelineStatus";
import Usage from "./Usage";
import { Discovery, Verification } from "./Pipeline";


function Load({ path, children }: { path: string; children: (d: any, reload: () => void) => any }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const reload = () => api(path).then(setD).catch(setErr);
  useEffect(() => { reload(); }, [path]);
  if (err) return <Err e={err} onRetry={reload} />;
  return d ? children(d, reload) : <Skeleton />;
}

function SourceControls({ s, can, reload }: { s: any; can: boolean; reload: () => void }) {
  const [url, setUrl] = useState<string>(s.url ?? "");
  const [licence, setLicence] = useState("Public newsroom page. robots.txt allows access. Reviewed by an administrator.");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const wrap = async (f: () => Promise<any>, done: (r: any) => string) => { setBusy(true); setErr(null); setMsg(""); try { setMsg(done(await f())); reload(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const v = s.validation;
  return (
    <div className="src-ctl">
      {s.url_basis && s.url && <p className="small muted mt-0 mb-2">Why this address: {s.url_basis}</p>}
      <div className="row">
        <input aria-label={`Page URL for ${s.display_name}`} placeholder={`https://… on ${s.canonical_domain}`} value={url} onChange={(e) => setUrl(e.target.value)} className="grow field-md" disabled={!can} />
        <button disabled={!can || busy || url.length < 12} onClick={() => wrap(() => put(`/api/admin/sources/${s.source_id}/config`, { url }), () => "Address saved. Test it next.")}>Save URL</button>
        <button disabled={!can || busy || !s.url} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/validate`), (r) => (r.ok ? "Test passed." : `Test failed: ${r.detail}`))}>Test from server</button>
      </div>
      {v && <p className="small mt-2 mb-0"><Badge kind={v.ok ? "ok" : "bad"}>{v.ok ? "Test passed" : "Test failed"}</Badge> {v.detail} <span className="muted">· {fmtDateTime(v.at)}</span></p>}
      {can && v?.ok && s.status !== "active" && (
        <div className="mt-3">
          <label className="small">Permission note: why we may read this page<br /><textarea value={licence} onChange={(e) => setLicence(e.target.value)} /></label>
          <label className="small row mt-2 mb-2"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I have checked that monitoring this page is permitted</label>
          <button className="primary" disabled={busy || !ok || licence.length < 10} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/activate`, { licence_note: licence, confirm_permitted: ok }), () => "Source switched on. It will start within five minutes.")}>Switch source on</button>
        </div>)}
      {can && s.status === "active" && <button className="mt-3" disabled={busy} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/pause`, { reason: "paused by administrator" }), () => "Source paused.")}>Pause</button>}
      {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
    </div>
  );
}


const STATE_KIND: Record<string, "ok" | "warn" | "bad" | "info"> = { live: "ok", built_in: "ok", per_account: "info", needs_address: "warn", blocked_by_site: "bad", needs_credential: "warn", not_built: "info", deferred: "info", needs_retest: "warn", testing: "info" };

function ApolloCap({ r }: { r: any }) {
  const [v, setV] = useState(String(r.cap ?? 0)); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const save = async () => { setErr(null); try { await post("/api/admin/integrations/apollo/cap", { credits: Number(v) }); setMsg("Saved for this month."); } catch (e) { setErr(e); } };
  return <div className="mt-2"><span className="k">Credits this month</span>
    <div className="row"><input aria-label="Monthly credit cap" type="number" min={0} value={v} onChange={(e) => setV(e.target.value)} className="field-sm" /><button onClick={save}>Set cap</button><span className="small muted">Spent {r.spent ?? 0}. Price per reveal: {r.credits_per_reveal ?? "not set (RI_APOLLO_CREDITS_PER_REVEAL)"}. At zero, no reveal can run.</span></div>
    {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} /></div>;
}

function Integrations() {
  const [res, setRes] = useState<Record<string, { ok: boolean; detail: string }>>({}); const [busy, setBusy] = useState("");
  const test = async (k: string) => { setBusy(k); try { const r = await post(`/api/admin/integrations/${k}/test`); setRes((x) => ({ ...x, [k]: r })); } catch (e) { setRes((x) => ({ ...x, [k]: { ok: false, detail: e instanceof Error ? e.message : "Test failed" } })); } finally { setBusy(""); } };
  return (
    <Load path="/api/admin/integrations">{(d) => (<>
      <p className="sub mb-4">The accounts we connect to for outreach, Pipedrive and contact data. Keys are stored on the server and never shown here. A passing test only proves the key works. Sending and Pipedrive changes also need their switch turned on and a colleague's approval.</p>
      <div className="conn-list">
        {d.items.map((r: any) => <article key={r.key} className={`conn ${r.configured ? "live" : "needs_credential"}`}>
          <div className="conn-head"><div><div className="conn-name">{r.name}</div><div className="conn-meta">{r.what}</div></div>
            <div className="row"><Badge kind={r.configured ? "ok" : "warn"}>{r.configured ? "Connected" : "Not connected"}</Badge>{r.switch && <Badge kind={r.switch_on ? "ok" : ""}>{r.switch_on ? "Switched on" : "Switched off"}</Badge>}</div></div>
          {r.blocked_reason && <div className="err mt-2 mb-2" role="alert">{r.blocked_reason}</div>}
          <div className="conn-cols"><div><span className="k">How to connect it</span>{r.how}</div>{r.switch && <div><span className="k">Safety switch</span>{r.switch}: {r.switch_on ? "on" : "off. Nothing is sent or changed until it is turned on."}</div>}</div>
          {r.key === "apollo" && <ApolloCap r={r} />}
          {r.testable && <div className="row mt-2"><button disabled={!r.configured || busy === r.key} onClick={() => test(r.key)}>Test the connection</button>
            {res[r.key] && <span className={res[r.key].ok ? "okmsg" : "small"} role="status">{res[r.key].detail}</span>}</div>}
        </article>)}
      </div></>)}</Load>
  );
}

function DeadlinesAdmin() {
  const empty = { title: "", regime: "", deadline_date: "", applies_to: "", region: "", source_url: "", source_quote: "" };
  const [f, setF] = useState(empty); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return <Load path="/api/deadlines?all=true">{(d, reload) => {
    const add = async () => { setErr(null); setMsg(""); try { await post("/api/deadlines", { ...f, applies_to: f.applies_to || null, region: f.region || null }); setF(empty); setMsg("Proposed. A colleague has to approve it before sellers see it."); reload(); } catch (e) { setErr(e); } };
    const act = async (id: string, decision: string) => { setErr(null); try { await post(`/api/deadlines/${id}/decision`, { decision }); reload(); } catch (e) { setErr(e); } };
    return (<>
      <p className="sub">A date only shows for sellers once it has an official link, the sentence that states it, and a colleague's approval. Nothing is guessed.</p>
      <div className="card"><b>Propose a deadline</b>
        <div className="grid2 mt-2">
          <label className="small">Title<input value={f.title} onChange={set("title")} /></label><label className="small">Regulation<input value={f.regime} onChange={set("regime")} placeholder="e.g. DORA" /></label>
          <label className="small">Date<input type="date" value={f.deadline_date} onChange={set("deadline_date")} /></label><label className="small">Applies to<input value={f.applies_to} onChange={set("applies_to")} placeholder="e.g. banks in the EU" /></label>
          <label className="small">Region<input value={f.region} onChange={set("region")} placeholder="EU, UK, DACH…" /></label><label className="small">Official page (https)<input value={f.source_url} onChange={set("source_url")} /></label>
        </div>
        <label className="small">The sentence on that page that states the date<textarea rows={2} value={f.source_quote} onChange={set("source_quote")} className="w-full" /></label>
        <button className="primary" disabled={!f.title || !f.regime || !f.deadline_date || !f.source_url || f.source_quote.length < 15} onClick={add}>Propose</button>
        {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} /></div>
      <div className="card table-wrap"><table><thead><tr><th>Deadline</th><th>Date</th><th>Quote from the official page</th><th>Status</th><th /></tr></thead><tbody>
        {d.items.map((x: any) => <tr key={x.id}><td><a href={x.source_url} target="_blank" rel="noreferrer noopener">{x.title}</a><div className="small muted">{x.regime} · proposed by {x.proposed_by}</div></td><td>{x.deadline_date}</td><td className="small">“{x.source_quote}”</td>
          <td><Badge kind={x.status === "approved" ? "ok" : x.status === "proposed" ? "warn" : ""}>{x.status === "proposed" ? "Waiting for a second approver" : humanize(x.status)}</Badge></td>
          <td>{x.status === "proposed" && <div className="row"><button className="primary" onClick={() => act(x.id, "approve")}>Approve</button><button onClick={() => act(x.id, "reject")}>Reject</button></div>}{x.status === "approved" && <button onClick={() => act(x.id, "retire")}>Retire</button>}</td></tr>)}
        {d.items.length === 0 && <tr><td colSpan={5} className="muted">No deadlines yet.</td></tr>}</tbody></table></div></>);
  }}</Load>;
}

const Coverage = () => <Load path="/api/admin/recall">{(d) => (<>
  <p className="sub">Are we finding what is out there? Each cell counts what we found in the last {d.grid.days} days. A red 0 is worth a look: a source may be broken, or the market may just be quiet.</p>
  {d.grid.empty.length > 0 && <div className="err" role="alert">{d.grid.empty.length} of {d.grid.cells.length} market and topic combinations found nothing in {d.grid.days} days.</div>}
  <div className="card table-wrap"><table><thead><tr><th>Market</th>{d.grid.kinds.map((k: string) => <th key={k}>{k}</th>)}</tr></thead><tbody>
    {d.grid.markets.map((m: string) => <tr key={m}><td><b>{m}</b></td>{d.grid.kinds.map((k: string) => { const c = d.grid.cells.find((x: any) => x.market === m && x.kind === k); return <td key={k}>{c.empty ? <Badge kind="bad">0</Badge> : c.count}</td>; })}</tr>)}</tbody></table></div>
  <div className="card"><b>Sources finding less than usual</b>
    {d.yield_drops.length === 0 ? <p className="small muted">None. We only judge a source after two weeks of history, and only if it normally finds at least 5 items a week.</p>
      : <ul className="tight small">{d.yield_drops.map((x: any) => <li key={x.source_id}><b>{x.source_id}</b>: {x.last_7_days} this week, usually about {x.usual_per_week}</li>)}</ul>}</div>
  <div className="card"><b>Pages sellers say we missed</b>
    <p className="small muted">{d.misses.total === 0 ? "None yet. Sellers can paste a link on the Market feed." : `${d.misses.total} reported: we already had ${d.misses.caught}, and missed ${d.misses.missed}. Each missed page shows which source should have found it.`}</p>
    {d.misses.items.length > 0 && <div className="table-wrap"><table><thead><tr><th>Page</th><th>Did we have it?</th><th>Result</th><th>Reported</th></tr></thead><tbody>
      {d.misses.items.map((m: any) => <tr key={m.id}><td><a href={m.url} target="_blank" rel="noreferrer noopener">{m.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a>{m.note && <div className="small muted">{m.note}</div>}</td>
        <td>{m.we_already_had_it ? <Badge kind="ok">Yes ({m.from_source})</Badge> : <Badge kind="bad">No</Badge>}</td><td className="small">{humanize(m.status)}{m.events_created > 0 ? ` · ${m.events_created} finding(s)` : ""}</td><td className="small">{fmtDateTime(m.reported_at)}</td></tr>)}</tbody></table></div>}</div></>)}</Load>;

const Precision = () => <Load path="/api/admin/precision">{(d) => (<>
  <p className="sub">What sellers did with each signal in the last {d.window_days} days. This only reports: a person decides whether a signal changes. A dismissal is an opinion, so read the top reason first.</p>
  {d.flagged.length > 0 && <div className="err" role="alert">Dismissed more than 60% of the time, with enough decisions to judge: <b>{d.flagged.join(", ")}</b></div>}
  <div className="card table-wrap"><table><thead><tr><th>Signal</th><th>Fired</th><th>Pursued</th><th>Dismissed</th><th>Dismissed share</th><th>Main reason</th><th><span className="sr">Flag</span></th></tr></thead><tbody>
    {d.rules.map((r: any) => <tr key={r.rule_id}><td>{r.rule_id}</td><td>{r.fired}</td><td>{r.accepted}</td><td>{r.dismissed}</td><td>{r.dismissal_rate === null ? "n/a" : `${Math.round(r.dismissal_rate * 100)}%`}<div className="small muted">{r.enough_data ? `likely range ${Math.round(r.interval_95[0] * 100)} to ${Math.round(r.interval_95[1] * 100)}%` : "Too few decisions to judge"}</div></td>
      <td className="small">{r.top_dismiss_reason ? lab("dismiss", r.top_dismiss_reason) : "None"}</td><td>{r.flag && <Badge kind="bad">Look at this</Badge>}</td></tr>)}
    {d.rules.length === 0 && <tr><td colSpan={7} className="muted">No signals fired in this period.</td></tr>}</tbody></table></div>
  <div className="card"><b>Outreach results</b><div className="chips mt-2">{Object.keys(d.outreach).length === 0 ? <span className="muted small">Nobody added to a sequence yet.</span> : Object.entries(d.outreach).map(([k, n]) => <Badge key={k}>{humanize(k)}: {n as number}</Badge>)}</div></div>
  <ul className="tight small muted">{d.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></>)}</Load>;

function Connections() {
  const [filter, setFilter] = useState("");
  return (
    <Load path="/api/admin/connections">{(d) => {
      const rows = d.items.filter((r: any) => !filter || r.state === filter);
      return (<>
        <p className="sub mb-4">Every place we read from, what state it is in, what it needs to be switched on, and where to get that. “Runs per account” means it is found for each account when you press “Check for news” on that account.</p>
        <div className="chips" role="group" aria-label="Filter by state">
          <button className="chip" aria-pressed={!filter} onClick={() => setFilter("")}>All<b>{d.items.length}</b></button>
          {Object.entries(d.counts).map(([k, n]) => <button key={k} className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{d.labels[k]}<b>{n as number}</b></button>)}
        </div>
        <div className="conn-list">
          {rows.map((r: any) => <article key={r.source_id} className={`conn ${r.state}`}>
            <div className="conn-head">
              <div><div className="conn-name">{r.name}</div><div className="conn-meta">{humanize(r.family)}{r.countries?.length ? ` · ${r.countries.join(", ")}` : ""}</div></div>
              <Badge kind={STATE_KIND[r.state] ?? "info"}>{r.state_label}</Badge>
            </div>
            <div className="conn-cols">
              <div><span className="k">What to do next</span>{r.what_to_do}{r.last_error && <div className="small muted">Last problem: {r.last_error}</div>}</div>
              <div><span className="k">Where to get it</span>{r.where_to_get ?? <span className="muted">Nothing to obtain</span>}</div>
            </div>
            <div className="small muted">
              {r.documents ? <>{r.documents.toLocaleString()} pages read · latest {fmtDateTime(r.last_document_at)}</> : "Nothing read yet"}
              {r.last_success_at && <> · last success {fmtDateTime(r.last_success_at)}</>}
              {r.address && <div className="conn-addr">{r.address}</div>}
            </div>
          </article>)}
          {rows.length === 0 && <div className="empty"><b>Nothing in this state</b>Pick another filter above.</div>}
        </div></>);
    }}</Load>
  );
}

function ScanTop() {
  const [n, setN] = useState(25); const [search, setSearch] = useState(true); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(null); setMsg(""); try { const r = await post("/api/admin/scan-accounts", { limit: n, with_search: search }); setMsg(`Checking ${r.queued} ${r.queued === 1 ? "account" : "accounts"}. ${r.note} Results appear in the Market feed as we check them.`); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <div className="card">
      <b>Check your priority accounts for news</b>
      <div className="small muted mt-1 mb-3">Reads each account's own news, press, careers and job-board pages{search ? " and runs one web search per account" : ""}. This only runs when you press the button. Cost stays within the monthly limits under Usage and cost.</div>
      <div className="row">
        <label className="small">How many accounts <input type="number" min={1} max={300} value={n} onChange={(e) => setN(Number(e.target.value))} className="field-xs" /></label>
        <label className="small row"><input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} /> Include one web search per account (uses the Exa budget)</label>
        <button className="primary" disabled={busy || n < 1} onClick={go}>Check these accounts</button>
      </div>
      {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
    </div>
  );
}

function Sources({ canEdit }: { canEdit: boolean }) {
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const bulk = async (activate: boolean) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post("/api/admin/sources/validate-all", { activate }); setMsg(activate ? "Testing every source now. Each one that passes is switched on. Refresh in a minute to see the result on each card." : "Testing every source now. Nothing is switched on."); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (<>
    {canEdit && <ScanTop />}
    {canEdit && <div className="card row spread"><div><b>Test all sources</b><div className="small muted">Tests each source from the server. We only read pages the site allows (robots.txt). Sources missing an address or a key are skipped and say why.</div></div>
      <div className="row"><button disabled={busy} onClick={() => bulk(false)}>Test only</button><button className="primary" disabled={busy} onClick={() => bulk(true)}>Test, then switch on those that pass</button></div></div>}
    {msg && <div className="okmsg mb-3" role="status">{msg}</div>}<Err e={err} />
    <Library render={(s, reload) => <SourceControls s={s} can={canEdit} reload={reload} />} />
  </>);
}

const Identity = () => <Load path="/api/admin/identity-reviews">{(d, reload) => <IdentityList d={d} reload={reload} />}</Load>;
function IdentityList({ d, reload }: { d: any; reload: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [note, setNote] = useState("");
  const act = async (id: string, decision: string, org?: string) => { try { await post(`/api/admin/identity-reviews/${id}/resolve`, { decision, organization_id: org, note }); reload(); } catch (e) { setErr(e); } };
  return <><Err e={err} /><p className="small muted">A parent company and its subsidiary are linked, never merged. Every decision needs a reason and is logged.</p><input aria-label="Your reason" placeholder="Your reason (required)" value={note} onChange={(e) => setNote(e.target.value)} className="field-lg mb-3" />
    {d.items.length === 0 && <div className="empty"><b>Nothing to match</b>Every company name from your workbooks has been matched.</div>}
    {d.items.map((r: any) => <div key={r.id} className="card"><b>{r.raw_company_name}</b> <Badge kind="warn">{r.reason === "ambiguous_match" ? "Could be more than one company" : humanize(r.reason)}</Badge> <span className="muted small">{r.sheet_title ? `from ${r.sheet_title}, row ${r.row_number}` : ""}</span>
      <div className="row mt-2">{r.candidates.map((c: any) => <button key={c.id} disabled={note.length < 3} onClick={() => act(r.id, "link", c.id)}>Link to {c.display_name} ({c.country_code ?? "?"})</button>)}
        <button disabled={note.length < 3} onClick={() => act(r.id, "new")}>It is a new company</button><button disabled={note.length < 3} onClick={() => act(r.id, "reject")}>Ignore this row</button></div></div>)}</>;
}

const Approvals = () => <Load path="/api/approvals">{(d, reload) => <ApprovalList d={d} reload={reload} />}</Load>;
function ApprovalList({ d, reload }: { d: any; reload: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const go = async (path: string, body?: unknown, done = "Done.") => { setErr(null); setMsg(""); try { await post(path, body); setMsg(done); reload(); } catch (e) { setErr(e); } };
  return <><Err e={err} />{msg && <div className="okmsg" role="status">{msg}</div>}{d.items.length === 0 && <div className="empty"><b>Nothing waiting</b>Requests for Pipedrive changes, outreach and email reveals show up here for a second person to approve.</div>}
    {d.items.map((a: any) => <div key={a.id} className="card"><div className="row spread"><b>{lab("approval", a.kind)}{a.payload?.subject ? `: ${a.payload.subject}` : ""}</b><Badge kind={a.status === "approved" ? "ok" : a.status === "pending" ? "warn" : a.status === "executed" ? "info" : ""}>{({ pending: "Waiting for approval", approved: "Approved, not run yet", executed: "Done", rejected: "Rejected", expired: "Expired" } as Record<string, string>)[a.status] ?? humanize(a.status)}</Badge></div>
      <div className="small">For {humanize(a.target_system)} · asked by {a.requested_by} · expires {fmtDateTime(a.expires_at)}</div><div className="small mt-2 mb-2">{Object.entries(a.payload ?? {}).map(([k, v]: any) => <div key={k}><b>{humanize(k)}:</b> {typeof v === "object" ? JSON.stringify(v) : String(v)}</div>)}</div><div className="small muted">Why: {a.justification}</div>
      <div className="row mt-2">{a.status === "pending" && <><button className="primary" onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: true }, "Approved. Press Run now to carry it out.")}>Approve this</button><button onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: false }, "Rejected.")}>Reject</button></>}
        {a.status === "approved" && <button onClick={() => go(`/api/approvals/${a.id}/execute`, undefined, "Done.")}>Run now</button>}</div></div>)}</>;
}

const Jobs = () => <Load path="/api/admin/jobs">{(d, reload) => <><div className="row">{d.by_status.map((s: any) => <Badge key={s.status} kind={s.status.includes("failed") ? "bad" : ""}>{({ done: "Done", queued: "Waiting", running: "Running", retrying: "Retrying", permanent_failed: "Failed for good" } as Record<string, string>)[s.status] ?? humanize(s.status)}: {s.n}</Badge>)}</div>
  <h3>Jobs that failed for good</h3>{d.dead_letters.length === 0 && <div className="empty"><b>None</b>Nothing has failed for good.</div>}{d.dead_letters.length > 0 && <div className="card table-wrap"><table><thead><tr><th>Job</th><th>Problem</th><th>Detail</th><th><span className="sr">Action</span></th></tr></thead><tbody>{d.dead_letters.map((x: any) => <tr key={x.id}><td>{x.kind}</td><td>{x.error_class}</td><td>{x.error}</td><td>{x.replayed_at ? "Retried" : <button onClick={() => post(`/api/admin/dead-letters/${x.id}/replay`).then(reload)}>Try again</button>}</td></tr>)}</tbody></table></div>}</>}</Load>;

const Costs = () => <Load path="/api/admin/costs">{(d) => <><p className="small muted">{d.note}</p><div className="card table-wrap"><table><thead><tr><th>Provider</th><th>Kind</th><th>Period</th><th>Limit</th><th>Set aside</th><th>Spent</th></tr></thead><tbody>{d.budgets.map((b: any, i: number) => <tr key={i}><td>{b.provider}</td><td>{b.kind}</td><td>{b.period}</td><td>{b.cap}</td><td>{b.reserved}</td><td>{b.spent}</td></tr>)}
  {d.budgets.length === 0 && <tr><td colSpan={6} className="muted">No budgets configured: no paid provider call can run.</td></tr>}</tbody></table></div></>}</Load>;

const Rules = () => <Load path="/api/admin/rules">{(d) => <><div className="row">{d.summary.map((s: any) => <Badge key={s.status} kind={s.status === "active" ? "ok" : ""}>{humanize(s.status)}: {s.n}</Badge>)}</div><div className="card table-wrap"><table><thead><tr><th>ID</th><th>Signal (original wording)</th><th>Strength</th><th>Stays relevant (days)</th><th>Version</th><th>Status</th></tr></thead><tbody>
  {d.items.map((r: any) => <tr key={r.id}><td>{r.id}</td><td>{r.signal}</td><td>{r.strength}</td><td>{r.fresh_for_days}</td><td>{r.version}</td><td><Badge kind={r.status === "active" ? "ok" : ""}>{r.status === "active" ? "On" : "Not yet on"}</Badge></td></tr>)}</tbody></table></div></>}</Load>;

const Audit = () => <Load path="/api/admin/audit">{(d) => <div className="card table-wrap"><table><thead><tr><th>When</th><th>Who</th><th>What they did</th><th>About</th><th>Approval</th></tr></thead><tbody>{d.items.map((a: any) => <tr key={a.id}><td>{fmtDateTime(a.at)}</td><td>{a.actor}</td><td>{humanize(a.operation)}</td><td>{humanize(a.record_type)} <span className="small muted">{a.record_id?.slice(0, 8)}</span></td><td>{a.approval_id ? a.approval_id.slice(0, 8) : "None needed"}</td></tr>)}</tbody></table></div>}</Load>;

const QUALITY_LABELS: Record<string, [string, string]> = {
  stale_sources: ["Sources gone quiet", "Active sources that haven't produced anything recently."],
  dead_letters_unreplayed: ["Failed jobs not retried", "Jobs that failed for good and haven't been replayed. See Jobs."],
  alerts_sent: ["Alerts sent", "Alerts sent to the team."],
  duplicate_alert_groups: ["Duplicate alerts", "Stories alerted more than once. Should be zero."],
  high_priority_without_citation: ["High priority without a source", "High-priority cards with no cited source. Should be zero."],
  open_identity_reviews: ["Company matches to review", "Companies we couldn't match with confidence. See Identity review."],
  unverified_events: ["Findings not checked yet", "Findings waiting to be checked against the original page."],
  unauthorised_writes_detected: ["Unapproved changes detected", "Writes to Pipedrive or outreach without an approval. Should be zero."],
  pending_approvals: ["Approvals waiting", "Requests waiting for a second person. See Approvals."],
};
const Quality = () => <Load path="/api/admin/metrics">{(d) => <><p className="sub mb-4">Health checks on the platform itself. Anything marked "should be zero" and isn't is worth a look.</p>
  <div className="kpis">{Object.entries(d).filter(([, v]) => typeof v === "number").map(([k, v]: any) => { const [t, hint] = QUALITY_LABELS[k] ?? [humanize(k), ""]; return <div key={k} className="card kpi"><div className="eyebrow">{t}</div><div className="kpi-n">{v}</div>{hint && <div className="small muted">{hint}</div>}</div>; })}</div>
  <Load path="/api/admin/funnel">{(f) => <><h3>Outcome funnel</h3>{Object.keys(f.cohorts).length === 0 && <div className="empty"><b>No outcomes recorded yet</b>Nothing here is inferred.</div>}{Object.entries(f.cohorts).map(([k, c]: any) => <div key={k} className="card"><b>{humanize(k)}</b> · presented {c.presented} {c.insufficient_sample && <Badge kind="warn">too few to judge</Badge>}</div>)}<ul className="tight small muted">{f.caveats.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}</Load></>}</Load>;

function Imports() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/admin/imports").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 8000); return () => clearInterval(t); }, []);
  const send = async (f: File | undefined) => {
    if (!f) return; setBusy(true); setErr(null); setMsg("");
    try { const r = await upload("/api/admin/import/upload", f); setMsg(`Workbook ${r.workbook} received. It is being loaded now, which can take several minutes.`); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const sync = async (wb: string) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post(`/api/admin/import/sync?workbook=${wb}`); setMsg(`Sync of workbook ${wb} started. This can take several minutes.`); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const gs = d?.google_sheets;
  return (
    <>
      <div className="card">
        <div className="eyebrow">Connect Google Sheets (no uploads needed)</div>
        {gs?.configured ? <p className="mt-0"><Badge kind="ok">Connected</Badge> Share both workbooks with <b>{gs.service_account_email ?? "the service account"}</b> as Viewer, then press Sync. Each sync re-reads the sheets and keeps the data up to date.</p>
          : <ol className="small mt-0">
              <li>In Google Cloud, create a service account in any project and enable the Google Sheets API. Create a JSON key for it.</li>
              <li>In Render, add the whole JSON as the secret <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> on the API and the worker.</li>
              <li>Open each workbook in Google Sheets, press Share, and add the service account's email as Viewer.</li>
              <li>Come back here and press Sync. If your Google admin blocks sharing outside the company, upload the exports below instead.</li>
            </ol>}
        <div className="row">
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("A")}>Sync workbook A</button>
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("B")}>Sync workbook B</button>
          {gs?.workbooks && <><a href={gs.workbooks.A} target="_blank" rel="noreferrer">Open A</a><a href={gs.workbooks.B} target="_blank" rel="noreferrer">Open B</a></>}
        </div>
      </div>
      <div className="card">
        <div className="eyebrow">Or upload a workbook export</div>
        <p className="mt-0">In Google Sheets choose <b>File, Download, Microsoft Excel (.xlsx)</b> for each workbook, then upload it here. We work out which workbook it is, check every tab's column headings against the agreed layout, and load it in the background. A renamed or reordered column pauses that tab instead of guessing.</p>
        <input type="file" accept=".xlsx" aria-label="Workbook file (.xlsx)" disabled={busy} onChange={(e) => { void send(e.target.files?.[0]); e.target.value = ""; }} />
        {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
        <p className="small muted mb-0">Uploading the same file again changes nothing. We delete the file after loading it and keep only its fingerprint and the result.</p>
      </div>
      <h3>Recent uploads</h3>
      {!d ? <Skeleton /> : d.uploads.length === 0 ? <div className="empty">No uploads yet.</div> :
        <div className="card table-wrap"><table><thead><tr><th>When</th><th>File</th><th>Workbook</th><th>Status</th><th>Detail</th></tr></thead><tbody>{d.uploads.map((u: any) => {
          const tabs = u.stats?.tabs ? Object.entries(u.stats.tabs) as [string, any][] : [];
          return <tr key={u.id}><td>{fmtDateTime(u.created_at)}</td><td>{u.filename}<div className="small muted">{u.uploaded_by}</div></td><td>{u.workbook_key}</td>
            <td><Badge kind={u.status === "completed" ? "ok" : u.status === "failed" ? "bad" : "info"}>{({ completed: "Loaded", failed: "Failed", running: "Loading", queued: "Waiting" } as Record<string, string>)[u.status] ?? humanize(u.status)}</Badge></td>
            <td className="small">{u.error ?? tabs.map(([k, v]) => `${k}: ${v.status}${v.rows !== undefined ? ` (${v.rows} rows)` : ""}`).join(" · ")}</td></tr>; })}</tbody></table></div>}
    </>
  );
}

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
        <div className="eyebrow">Add a person</div>
        <p className="small muted mt-0">They sign in with Google using this address{d?.domain ? ` (only @${d.domain} is accepted)` : ""}. Sellers see accounts with no Pipedrive owner, plus their own and any shared with them.</p>
        <div className="row">
          <input aria-label="Work email" placeholder="name@thecodest.co" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className="field-md" />
          <input aria-label="Display name" placeholder="Display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <select aria-label="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select>
          <input aria-label="Team" placeholder="Team" value={f.team} onChange={(e) => setF({ ...f, team: e.target.value })} className="field-sm" />
          <button className="primary" disabled={!f.email.includes("@") || !f.display_name} onClick={() => run(async () => { await post("/api/admin/users", f); setF({ ...f, email: "", display_name: "" }); }, "Person added. They can sign in now.")}>Add</button>
        </div>
        {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
      </div>
      {d && <div className="card table-wrap"><table><thead><tr><th>Person</th><th>Role</th><th>Team</th><th>Status</th><th><span className="sr">Actions</span></th></tr></thead><tbody>{d.items.map((u: any) => <tr key={u.id}>
        <td>{u.display_name}<div className="small muted">{u.email}</div></td>
        <td><select aria-label={`Role for ${u.email}`} value={u.role} onChange={(e) => run(() => patch(`/api/admin/users/${u.id}`, { role: e.target.value }), "Role updated.")}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select></td>
        <td>{u.team}</td><td><Badge kind={u.active ? "ok" : ""}>{u.active ? "Active" : "Deactivated"}</Badge></td>
        <td><button className="ghost" onClick={() => run(() => patch(`/api/admin/users/${u.id}`, { active: !u.active }), u.active ? "Deactivated." : "Reactivated.")}>{u.active ? "Deactivate" : "Reactivate"}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}

function SubNav({ me }: { me: { permissions: string[] } }) {
  const p = (x: string) => me.permissions.includes(x);
  const [waiting, setWaiting] = useState(0);
  useEffect(() => { api("/api/approvals").then((r) => setWaiting(r.items.filter((a: any) => a.status === "pending").length)).catch(() => undefined); }, []);
  const L = (show: boolean, to: string, label: string, end = false, badge = 0) => show ? <NavLink key={to} to={to} end={end}>{label}{badge > 0 && <span className="tabn">{badge}</span>}</NavLink> : null;
  const groups: [string, (React.ReactNode)[]][] = [
    ["Needs a person", [L(true, "/admin/approvals", "Approvals", false, waiting), L(p("identity.manage"), "/admin/identity", "Company matches"), L(p("sources.read"), "/admin/verification", "Pages being checked")]],
    ["Is it working?", [L(p("admin.jobs"), "/admin/pipeline", "Pipeline"), L(p("admin.jobs"), "/admin/jobs", "Failed jobs"), L(p("admin.costs"), "/admin/usage", "Usage and cost"), L(p("admin.rules"), "/admin/coverage", "Are we finding things?"), L(p("admin.rules"), "/admin/quality", "Health checks")]],
    ["Data and sources", [L(p("sources.read"), "/admin", "Sources", true), L(p("sources.read"), "/admin/connections", "Where we read from"), L(p("admin.sources"), "/admin/imports", "Workbooks"), L(p("admin.costs"), "/admin/discovery", "Web search"), L(p("admin.rules"), "/admin/deadlines", "Regulation deadlines"), L(p("admin.sources"), "/admin/knowledge", "Knowledge base")]],
    ["Signals", [L(p("admin.rules"), "/admin/rules", "Signal list"), L(p("admin.rules"), "/admin/precision", "Which signals work")]],
    ["Access", [L(p("admin.users"), "/admin/users", "People"), L(p("admin.costs"), "/admin/integrations", "Connected services"), L(p("audit.read"), "/admin/audit", "Audit log")]],
  ];
  return <nav className="subnav" aria-label="Administration sections">{groups.map(([g, items]) => items.some(Boolean) ? <div key={g} className="subnav-g"><div className="subnav-h">{g}</div>{items}</div> : null)}</nav>;
}

export default function Admin({ me }: { me: { permissions: string[] } }) {
  const p = (x: string) => me.permissions.includes(x);
  return (
    <>
      <div className="page-head"><h1>Administration</h1></div>
      <div className="admin-grid">
        <SubNav me={me} />
        <div className="admin-main">
          <Routes><Route index element={<Sources canEdit={p("admin.sources")} />} /><Route path="pipeline" element={<Pipeline />} /><Route path="connections" element={<Connections />} /><Route path="integrations" element={<Integrations />} /><Route path="knowledge" element={<KnowledgeAdmin />} /><Route path="imports" element={<Imports />} /><Route path="users" element={<Users />} /><Route path="identity" element={<Identity />} /><Route path="approvals" element={<Approvals />} /><Route path="jobs" element={<Jobs />} /><Route path="costs" element={<Costs />} /><Route path="usage" element={<Usage />} /><Route path="discovery" element={<Discovery />} /><Route path="verification" element={<Verification />} />
            <Route path="rules" element={<Rules />} /><Route path="precision" element={<Precision />} /><Route path="coverage" element={<Coverage />} /><Route path="deadlines" element={<DeadlinesAdmin />} /><Route path="quality" element={<Quality />} /><Route path="audit" element={<Audit />} /></Routes>
        </div>
      </div>
    </>
  );
}
