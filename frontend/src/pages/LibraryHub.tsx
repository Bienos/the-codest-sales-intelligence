import { counted, number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Err, humanize, Skeleton, reloadPage, Strength, Tabs } from "../components/ui";
import Library, { Dot, LEmpty, LFacts, LFilter, LGroup, LRow, shortUrl, TIERS } from "./Library";

type Tab = "signals" | "sources" | "urls";

/** Waits a moment after typing before asking the server again. */
function useDebounced<T>(value: T, ms = 220): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function Signals({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  const [group, setGroup] = useState(""); const [status, setStatus] = useState("");
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const dq = useDebounced(q);
  useEffect(() => { api(`/api/library/signals?q=${encodeURIComponent(dq)}&group=${encodeURIComponent(group)}&status=${status}`).then(setD).catch(setErr); }, [dq, group, status]);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const groups = (d.groups as string[]).map((g) => [g, d.items.filter((s: any) => s.grp === g)] as [string, any[]]).filter(([, l]) => l.length);
  return (
    <>
      <p className="lintro">{tr("The buying signals we look for, in their original wording.") + " "}<b>{tr("On")}</b>{" " + tr("means we detect it automatically from checked findings;") + " "}<b>{tr("Not yet on")}</b>{" " + tr("means it is written down and waiting for a source or a test.") + " "}<b>{tr("Strength")}</b>{" " + tr("is how strongly it suggests outside engineering work, from 1 to 3.")}</p>
      <LFilter q={q} setQ={setQ} placeholder="Search signals" count={counted(d.items.length, ["signal", "signals"], ["sygnał", "sygnały", "sygnałów"])} selects={[
        { label: "Signal group", value: group, onChange: setGroup, options: [["", tr("Every group")], ...d.groups.map((g: string) => [g, tr(g)] as [string, string])] },
        { label: "Status", value: status, onChange: setStatus, options: [["", tr("Any status")], ["active", tr("On: detected automatically")], ["defined_not_activated", tr("Not yet on")]] },
      ]} />
      {d.items.length === 0 && <LEmpty>{tr("No signals match these filters.")}</LEmpty>}
      {groups.map(([g, list]) => (
        <LGroup key={g} title={g} count={list.length} cols={["Signal", "Strength", "Status", "Stays relevant"]}>
          {list.map((s) => (
            <LRow key={s.rule_id} name={s.signal} sub={s.rule_id} kind={<Strength n={Number(s.strength)} />}
              status={s.status === "active" ? ["ok", "On"] : ["", "Not yet on"]} extraLabel="Stays relevant" extra={tr("{} days", s.fresh_for_days)}>
              <LFacts rows={[
                ["How to spot it", s.how_to_spot ?? tr("Not recorded")],
                ["Who to contact", s.who_to_contact ?? tr("Not recorded")],
                ["Check it is genuine", s.check_it_is_real ?? tr("Not recorded")],
                ["Pairs best with", s.pairs_best_with],
                ["What cancels it", s.stop_effect],
                ["Stays relevant for", <>{tr("{} days", s.fresh_for_days)}{s.window_note ? `. ${s.window_note}` : ""}</>],
                ["Comes from", s.fed_by?.length ? s.fed_by.map((f: string) => <span key={f} className="pill">{f}</span>) : <span className="muted">{tr("No source connected yet")}</span>],
              ]} />
            </LRow>))}
        </LGroup>))}
    </>
  );
}

const METHOD_GROUP: Record<string, string> = { feed: "Feeds", change_monitor: "Pages checked for changes" };
const METHOD_LABEL: Record<string, string> = { feed: "Reads its feed", change_monitor: "Checks the page for changes" };
const feedStatus = (s: string): [Dot, string] => s === "active" ? ["ok", "Live"] : s === "paused" ? ["", "Paused"] : ["warn", "Not set up yet"];

function Urls({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const [type, setType] = useState(""); const [status, setStatus] = useState("");
  const dq = useDebounced(q);
  useEffect(() => { api(`/api/library/urls?q=${encodeURIComponent(dq)}&limit=80`).then(setD).catch(setErr); }, [dq]);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const feeds = d.feeds.filter((f: any) => (!q || `${f.display_name} ${f.url} ${f.canonical_domain ?? ""}`.toLowerCase().includes(q.toLowerCase()))
    && (!type || f.collection_method === type) && (!status || (status === "active" ? f.status === "active" : f.status !== "active")));
  const methods = Array.from(new Set<string>(d.feeds.map((f: any) => f.collection_method)));
  const groups = methods.map((m) => [m, feeds.filter((f: any) => f.collection_method === m)] as [string, any[]]).filter(([, l]) => l.length);
  return (
    <>
      <p className="lintro">{tr("Every news page and feed we read on a schedule, and every link your workbooks cite. Open one to see the full address and how we found it.")}</p>
      <LFilter q={q} setQ={setQ} placeholder="Search pages and feeds" count={tr("{} pages and feeds", number(feeds.length))} selects={[
        { label: "Type", value: type, onChange: setType, options: [["", tr("Feeds and pages")], ...methods.map((m) => [m, tr(METHOD_GROUP[m] ?? humanize(m))] as [string, string])] },
        { label: "Status", value: status, onChange: setStatus, options: [["", tr("Any status")], ["active", tr("Live")], ["other", tr("Not set up yet")]] },
      ]} />
      {feeds.length === 0 && <LEmpty>{tr("No pages or feeds match these filters.")}</LEmpty>}
      {groups.map(([m, list]) => (
        <LGroup key={m} title={METHOD_GROUP[m] ?? humanize(m)} count={list.length} cols={["Page or feed", "Source type", "Status", "Address"]}>
          {list.map((f) => (
            <LRow key={f.source_id} name={f.display_name} sub={f.canonical_domain ?? shortUrl(f.url, 40)} kind={tr(TIERS[f.source_tier] ?? f.source_tier)} status={feedStatus(f.status)}
              extraLabel="Address" extra={<span className="lrow-addr">{shortUrl(f.url, 44)}</span>}>
              <LFacts rows={[
                ["Address", <a href={f.url} target="_blank" rel="noreferrer noopener">{shortUrl(f.url, 90)} ↗</a>],
                ["How", tr(METHOD_LABEL[f.collection_method] ?? humanize(f.collection_method))],
                ["How we found it", f.basis],
                ["Source type", tr(TIERS[f.source_tier] ?? f.source_tier)],
              ]} />
            </LRow>))}
        </LGroup>))}
      <LGroup title="Links in your workbooks" count={d.items.length} open={false} cols={["Account and link", "Workbook column", "", "Website"]}
        note={<>{tr("LinkedIn links are left out on purpose. Open the original and read it before you act.")}{d.domains.length > 0 && <span className="ldomains">{tr("Cited most:")}{d.domains.map((x: any) => <button key={x.domain} type="button" className="chip" onClick={() => setQ(x.domain)}>{x.domain} <b>{x.count}</b></button>)}</span>}</>}>
        {d.items.length === 0 && <LEmpty>{tr("No workbook links match.")}</LEmpty>}
        {d.items.map((u: any) => (
          <LRow key={u.url} name={u.organization ?? u.domain} sub={shortUrl(u.url, 60)} kind={u.field} extraLabel="Website" extra={u.domain}>
            <LFacts rows={[
              ["Link", <a href={u.url} target="_blank" rel="noreferrer noopener">{shortUrl(u.url, 90)} ↗</a>],
              ["Account", u.organization_id ? <Link to={`/accounts/${u.organization_id}`}>{u.organization}</Link> : u.organization],
              ["Workbook column", u.field],
            ]} />
          </LRow>))}
      </LGroup>
    </>
  );
}

export default function LibraryHub() {
  const [sp, setSp] = useSearchParams();
  const requested = sp.get("tab");
  const tab: Tab = requested === "signals" || requested === "urls" ? requested : "sources";
  const q = sp.get("q") ?? "";
  const setQ = (v: string) => setSp({ tab, ...(v ? { q: v } : {}) }, { replace: true });
  return (
    <>
      <div className="page-head"><div className="eyebrow">{tr("Reference")}</div><h1>{tr("Library")}</h1>
        <p className="sub">{tr("Every buying signal we look for, every source we read, and every page and feed we watch.")}</p></div>
      <Tabs label={tr("Library sections")} items={[["signals", "Signals"], ["sources", "Sources"], ["urls", "Pages and feeds"]].map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setSp({ tab: k, ...(q ? { q } : {}) })}>
        <div className="libtab">
          {tab === "signals" && <Signals q={q} setQ={setQ} />}
          {tab === "sources" && <Library query={q} setQuery={setQ} />}
          {tab === "urls" && <Urls q={q} setQ={setQ} />}
        </div>
      </Tabs>
    </>
  );
}
