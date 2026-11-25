import { number, tr } from "../locale";
import { ReactNode, useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";
import "../styles/library.css";

/* ------------------------------------------------------------------ one list style for the whole Library
 * Signals, sources, and pages and feeds all use these pieces: the same filter bar, the same group heading,
 * the same row (name, two facts, a status dot, details that open in place) and the same facts list inside. */

export type Dot = "ok" | "warn" | "bad" | "info" | "";

/** A plain status: a coloured square and the words next to it. Colour is never the only signal. */
export const Status = ({ s }: { s: [Dot, string] }) => <span className={`dotlabel ${s[0]}`}>{tr(s[1])}</span>;

export type FilterSelect = { label: string; value: string; onChange: (v: string) => void; options: [string, string][] };

/** Search, one or two selects and a live count, in the same place on every tab. */
export function LFilter({ q, setQ, placeholder, selects, count }: { q: string; setQ: (v: string) => void; placeholder: string; selects: FilterSelect[]; count: string }) {
  return (
    <div className="lfilter">
      <input type="search" aria-label={tr(placeholder)} placeholder={tr(placeholder)} value={q} onChange={(e) => setQ(e.target.value)} />
      {selects.map((s) => (
        <select key={s.label} aria-label={tr(s.label)} value={s.value} onChange={(e) => s.onChange(e.target.value)}>
          {s.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>))}
      <span className="lfilter-n" role="status">{count}</span>
    </div>
  );
}

/** A group of rows under one heading, with the column names once at the top. */
export function LGroup({ title, count, cols, open = true, note, children }: { title: string; count: number; cols: string[]; open?: boolean; note?: ReactNode; children: ReactNode }) {
  return (
    <details className="lgroup" open={open}>
      <summary><span>{tr(title)}</span><span className="lgroup-n">{number(count)}</span></summary>
      {note && <p className="lgroup-note">{note}</p>}
      <div className="lhead" aria-hidden="true">{cols.map((c, i) => <span key={i}>{tr(c)}</span>)}</div>
      <div className="lrows">{children}</div>
    </details>
  );
}

/** One row. Its details are only built when it is opened, so long lists stay quick. */
export function LRow({ name, sub, kind, status, extra, extraLabel, children }: {
  name: ReactNode; sub?: ReactNode; kind?: ReactNode; status?: [Dot, string] | null; extra?: ReactNode; extraLabel?: string; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="lrow" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <span className="lrow-name"><span className="lrow-t">{name}</span>{sub && <span className="lrow-sub">{sub}</span>}</span>
        <span className="lrow-kind">{kind}</span>
        <span className="lrow-status">{status && <Status s={status} />}</span>
        <span className="lrow-extra">{extraLabel && <span className="lrow-lab">{tr(extraLabel) + ": "}</span>}{extra}</span>
      </summary>
      {open && <div className="lrow-body">{children}</div>}
    </details>
  );
}

/** Label and value pairs inside an opened row. */
export const LFacts = ({ rows }: { rows: [string, ReactNode][] }) => (
  <dl className="lfacts">{rows.filter(([, v]) => v !== null && v !== undefined && v !== false && v !== "").map(([k, v]) => <div key={k}><dt>{tr(k)}</dt><dd>{v}</dd></div>)}</dl>
);

export const LEmpty = ({ children }: { children: ReactNode }) => <p className="lempty">{children}</p>;

/** A web address without its scheme, shortened for a row; the full address stays in the opened row. */
export const shortUrl = (url: string, max = 64) => { const t = url.replace(/^https?:\/\/(www\.)?/, ""); return t.length > max ? `${t.slice(0, max - 1)}…` : t; };

/* ------------------------------------------------------------------ sources */

export const TIERS: Record<string, string> = { S1: "Official records", S2: "Company’s own sites", S3: "Vendors and integrators", S4: "Trade press", S5: "Search finds", internal: "Internal" };
const hk = (s: string) => s === "healthy" ? "ok" : ["degraded", "failed", "blocked", "failing"].includes(s) ? "bad" : "";
const HEALTH: Record<string, string> = { healthy: "Working", unconfigured: "Not set up yet", paused: "Paused", degraded: "Having problems", failing: "Failing", failed: "Failed", blocked: "Access blocked" };
const METHOD: Record<string, string> = { feed: "Reads its feed", change_monitor: "Checks the page for changes", api: "Calls its API", official_api: "Calls its API", search: "Searches the web", internal: "Internal" };
const FAMILY: Record<string, string> = { attention: "Press and news", vendor: "Vendors and integrators", regulatory: "Regulators", procurement: "Tenders", jobs: "Job boards",
  verification: "Page checks", registry: "Company registers", internal: "Internal", company_ir: "Company news and careers", discovery: "Web search" };
const MODES: Record<string, string> = { account: "Runs per account", identity: "Company identity checks", on_demand: "On demand or requires setup" };
/** Where a source stands in setting it up (from Administration's connection directory). */
const CONN_KIND: Record<string, Dot> = { live: "ok", built_in: "ok", per_account: "info", needs_address: "warn", blocked_by_site: "bad", needs_credential: "warn", not_built: "", deferred: "", needs_retest: "warn", testing: "warn" };

/** Pipedrive is not connected and is hidden from the interface for now; the API still lists it as an internal source. */
const hidden = (s: any) => /pipedrive/i.test(`${s.source_id} ${s.provider ?? ""}`);

const cadence = (seconds: number) => {
  if (!seconds) return "";
  if (seconds === 86400) return ` · ${tr("daily")}`;
  if (seconds === 3600) return ` · ${tr("hourly")}`;
  return seconds >= 86400 ? ` · ${tr("every {} days", Math.round(seconds / 86400))}` : ` · ${tr("every {} minutes", Math.max(1, Math.round(seconds / 60)))}`;
};

/** Every source as one compact row, grouped by kind of source. In Administration (`admin`) each row also says what to do next and opens to its setup. */
export default function Library({ render, query, setQuery, admin = false }: { render?: (s: any, reload: () => void) => ReactNode; query?: string; setQuery?: (v: string) => void; admin?: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [qLocal, setQLocal] = useState("");
  const q = query ?? qLocal; const setQ = setQuery ?? setQLocal;
  const [status, setStatus] = useState(""); const [family, setFamily] = useState("");
  const [signals, setSignals] = useState<Record<string, string>>({});
  const [conn, setConn] = useState<{ items: Record<string, any>; labels: Record<string, string> } | null>(null);
  useEffect(() => {
    api("/api/library/signals").then((r) => setSignals(Object.fromEntries(r.items.map((item: any) => [item.rule_id, item.signal])))).catch(() => undefined);
  }, []);
  const load = () => {
    api("/api/admin/library").then(setD).catch(setErr);
    if (admin) api("/api/admin/connections").then((r) => setConn({ items: Object.fromEntries(r.items.map((c: any) => [c.source_id, c])), labels: r.labels })).catch(() => setConn(null));
  };
  useEffect(load, [admin]);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const periodic = (s: any) => s.monitoring_mode === "scheduled";
  const isStale = (s: any) => periodic(s) && s.last_success_at && Date.now() - Date.parse(s.last_success_at) > 2000 * s.poll_interval_s;
  const isReporting = (s: any) => periodic(s) && s.status === "active" && s.health === "healthy" && s.last_success_at && !isStale(s);
  /** One plain status per source. In Administration a source that is not live says what stage of setup it is at. */
  const state = (s: any): [Dot, string] => {
    const c = conn?.items[s.source_id];
    if (c && c.state !== "live") return [CONN_KIND[c.state] ?? "", conn!.labels[c.state] ?? humanize(c.state)];
    if (!periodic(s)) return ["info", MODES[s.monitoring_mode] ?? "On demand or requires setup"];
    if (isReporting(s)) return ["ok", "Reporting successfully"];
    if (isStale(s)) return ["warn", "Last check overdue"];
    if (s.status === "pending_connector_validation") return ["warn", "Not set up yet"];
    if (s.status === "paused") return ["", "Paused"];
    if (s.health === "healthy") return ["warn", "Waiting for first successful check"];
    return ["bad", HEALTH[s.health] ?? "Needs attention"];
  };
  const all = d.items.filter((s: any) => !hidden(s));
  const statusOptions: [string, string][] = conn
    ? Object.keys(conn.labels).map((k) => [k, all.filter((s: any) => conn.items[s.source_id]?.state === k).length] as [string, number]).filter(([, n]) => n > 0).map(([k, n]) => [k, `${tr(conn.labels[k])} (${number(n)})`])
    : [["reporting", tr("Reporting successfully")], ["needs_attention", tr("Scheduled sources needing attention")], ["other", tr("Sources outside the regular schedule")]];
  const matchesStatus = (s: any) => !status || (conn ? conn.items[s.source_id]?.state === status
    : status === "reporting" ? isReporting(s) : status === "needs_attention" ? periodic(s) && !isReporting(s) : !periodic(s));
  const families = Array.from(new Set<string>(all.map((s: any) => s.source_family))).sort((a, b) => tr(FAMILY[a] ?? a).localeCompare(tr(FAMILY[b] ?? b)));
  const items = all.filter((s: any) =>
    (!q || `${s.display_name} ${s.source_id} ${s.provider} ${s.canonical_domain ?? ""}`.toLowerCase().includes(q.toLowerCase())) && (!family || s.source_family === family) && matchesStatus(s));
  const groups = Object.keys(TIERS).map((t) => [t, items.filter((s: any) => s.source_tier === t)] as [string, any[]]).filter(([, l]) => l.length);
  const live = all.filter(isReporting).length;
  return (
    <>
      <p className="lintro">{tr("{} sources, {} reporting successfully.", number(all.length), number(live)) + " "}{admin ? tr("Open a source to see what it needs next, test its page and switch it on.") : tr("Open a source to see its last test, what it feeds and whether monitoring is running. Company careers and supplier pages found for an account appear on that account.")}</p>
      <LFilter q={q} setQ={setQ} placeholder="Search sources" count={tr("{} shown", number(items.length))} selects={[
        { label: "Kind of source", value: family, onChange: setFamily, options: [["", tr("Every kind")], ...families.map((f) => [f, tr(FAMILY[f] ?? humanize(f))] as [string, string])] },
        { label: "Status", value: status, onChange: setStatus, options: [["", tr("Any status")], ...statusOptions] },
      ]} />
      {items.length === 0 && <LEmpty>{tr("No sources match these filters.")}</LEmpty>}
      {groups.map(([t, list]) => (
        <LGroup key={t} title={TIERS[t]} count={list.length} cols={["Source", "Kind", "Status", "Last successful check"]}>
          {list.map((s) => {
            const c = conn?.items[s.source_id];
            const setup = admin && render && s.canonical_domain && ["feed", "change_monitor"].includes(s.collection_method);
            return (
              <LRow key={s.source_id} name={s.display_name} sub={s.provider ?? s.source_id} kind={FAMILY[s.source_family] ? tr(FAMILY[s.source_family]) : humanize(s.source_family)} status={state(s)}
                extraLabel="Last successful check" extra={s.last_success_at ? fmtDateTime(s.last_success_at) : <span className="muted">{tr("None yet")}</span>}>
                {c && (c.what_to_do || c.where_to_get) && <div className="lnext">
                  {c.what_to_do && <div><span className="lnext-k">{tr("What to do next")}</span>{tr(c.what_to_do)}</div>}
                  {c.where_to_get && <div><span className="lnext-k">{tr("Where to get it")}</span>{tr(c.where_to_get)}</div>}
                </div>}
                <LFacts rows={[
                  ["How", <>{tr(METHOD[s.collection_method] ?? humanize(s.collection_method))}{periodic(s) ? cadence(s.poll_interval_s) : ""}</>],
                  ["Monitoring", periodic(s) && <><Badge kind={isStale(s) ? "warn" : hk(s.health)}>{isStale(s) ? tr("Last check overdue") : s.health === "healthy" && !s.last_success_at ? tr("Waiting for first successful check") : tr(HEALTH[s.health ?? "unconfigured"] ?? humanize(s.health))}</Badge>{s.last_success_at && <span className="small muted">{" " + tr("last worked") + " "}{fmtDateTime(s.last_success_at)}</span>}</>],
                  ["Last error", s.health_detail && <span className="small">{s.health_detail}</span>],
                  ["Latest test", s.validation ? <>
                    <Badge kind={s.validation.ok ? "ok" : "bad"}>{s.validation.ok ? tr("Test passed") : tr("Test failed")}</Badge>
                    <span className="small muted"> {fmtDateTime(s.validation.at)}</span>
                    {!s.validation.ok && s.validation.detail && <div className="small">{s.validation.detail}</div>}
                  </> : <span className="muted">{tr("No test on record")}</span>],
                  ["Last attempt", s.last_attempt_at && fmtDateTime(s.last_attempt_at)],
                  ["Feeds signals", s.signal_rule_ids?.length ? s.signal_rule_ids.map((r: string) => <span key={r} className="pill" title={r}>{signals[r] ?? r}</span>) : <span className="muted">{tr("None yet")}</span>],
                  ["So far", <>{tr("{} pages read, {} findings", number(s.documents), number(s.events))}{Number(s.cost_usd_month) > 0 && <span className="small muted"> · ${Number(s.cost_usd_month).toFixed(3)}{" " + tr("this month")}</span>}</>],
                  ["Last 30 days", tr("{} verified findings with current buying signals", number(s.recent_buying_findings ?? 0))],
                  ["Permission", <span className="small">{s.licence_notes ?? humanize(s.retention_policy)}</span>],
                  ["Page", (s.url || c?.address) && <a className="small" href={s.url ?? c.address} target="_blank" rel="noreferrer noopener">{shortUrl(s.url ?? c.address, 70)} ↗</a>],
                ]} />
                {setup && <div className="lsetup"><div className="lnext-k">{s.url ? tr("Page address and test") : tr("Set up this source")}</div>{render!(s, load)}</div>}
              </LRow>);
          })}
        </LGroup>))}
    </>
  );
}
