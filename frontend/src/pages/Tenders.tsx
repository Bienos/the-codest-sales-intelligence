import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime } from "../api";
import { counted, number, tr } from "../locale";
import { Empty, Err, PageHeader, Skeleton, safeHref } from "../components/ui";
import { REGIONS, countryName } from "./targetMarket";

type Tender = {
  id: string; notice_id: string; title: string; category: string | null; buyer: string; buyer_kind: "finance" | "public";
  account: { id: string; name: string } | null; country: string | null; region: string; deadline: string; deadline_tz: string | null;
  days_left: number; status: string; url: string; source: string; scope: string | null; value: number | null; checked_at: string | null; check_is_old: boolean;
};
type Result = { items: Tender[]; counts: { total: number; finance: number; closing_this_week: number; closing_too_soon: number }; has_more: boolean; page: number;
  sources: { source_id: string; display_name: string; status: string; last_success_at: string | null }[] };

/** Open tenders for software work from TED, UK Find a Tender, UK Contracts Finder and e-Zamówienia, soonest deadline first. */
export default function Tenders() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [data, setData] = useState<Result | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const query = new URLSearchParams({ page: String(page), limit: "30", min_days: params.get("soon") === "1" ? "0" : "3" });
  ["q", "region", "buyer"].forEach((k) => { const v = params.get(k); if (v) query.set(k, v); });
  const qs = query.toString();
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setData(null);
    api<Result>(`/api/tenders?${qs}`, { signal: request.signal }).then(setData).catch((e) => { if (!request.signal.aborted) setErr(e); });
    return () => request.abort();
  }, [qs]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setParams(n); };
  const live = data?.sources.filter((s) => s.status === "active").length ?? 0;
  return <>
    <PageHeader title="Open tenders" sub="Public and private tenders for software development and IT services, from the official procurement sites. Soonest deadline first." />
    <form className="feedbar" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
      <input type="search" aria-label={tr("Search tenders")} placeholder={tr("Buyer, title or keyword")} maxLength={120} value={search} onChange={(e) => setSearch(e.target.value)} />
      <button type="submit">{tr("Search")}</button>
      <select aria-label={tr("Region")} value={params.get("region") ?? ""} onChange={(e) => set("region", e.target.value)}>
        <option value="">{tr("All regions")}</option>{REGIONS.map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
      </select>
      <select aria-label={tr("Buyer")} value={params.get("buyer") ?? ""} onChange={(e) => set("buyer", e.target.value)}>
        <option value="">{tr("All buyers")}</option><option value="finance">{tr("Banks, insurers and payments")}</option><option value="public">{tr("Public sector and other buyers")}</option>
      </select>
      <label className="check"><input type="checkbox" checked={params.get("soon") === "1"} onChange={(e) => set("soon", e.target.checked ? "1" : "")} /> {tr("Include tenders closing within 3 days")}</label>
    </form>
    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading tenders")} /> : <>
      <p className="small muted">{tr("{} open", number(data.counts.total))} · {tr("{} closing within a week", number(data.counts.closing_this_week))}
        {data.counts.closing_too_soon > 0 && params.get("soon") !== "1" && <> · {tr("{} more close within 3 days", number(data.counts.closing_too_soon))}</>}
        {" · "}{tr("Read from {} of {} tender sources", live, data.sources.length)}</p>
      {data.items.length === 0 ? <Empty title="No open tender matches">{live === 0 ? tr("No tender source is switched on yet. An administrator switches them on in Administration, Sources.") : tr("Clear a filter, or include tenders closing within 3 days.")}</Empty> :
        <ol className="tenders" aria-label={tr("Open tenders")}>
          {data.items.map((t) => <li key={t.id} className={`tender-row${t.days_left <= 7 ? " soon" : ""}`}>
            <div className="tender-due" aria-label={tr("Closes in {}", counted(t.days_left, ["day", "days"], ["dzień", "dni", "dni"]))}>
              <b>{t.days_left}</b><span>{t.days_left === 1 ? tr("day left") : tr("days left")}</span></div>
            <div className="tender-main">
              <h3>{safeHref(t.url) ? <a href={safeHref(t.url)} target="_blank" rel="noreferrer noopener">{t.title}</a> : t.title}</h3>
              <p className="tender-buyer">{t.account ? <Link to={`/accounts/${t.account.id}`}>{t.buyer}</Link> : t.buyer}
                <span className="muted"> · {countryName(t.country) ?? tr("Country not given")}</span>
                {t.buyer_kind === "finance" && <span className="tender-tag">{tr("Finance or insurance")}</span>}</p>
              {t.category && <p className="small mb-0">{t.category}</p>}
              {t.scope && <details className="small"><summary>{tr("Scope")}</summary><p className="mb-0">{t.scope}</p></details>}
            </div>
            <div className="tender-meta small">
              <div>{tr("Closes {}", fmtDateTime(t.deadline))}{t.deadline_tz ? ` (${t.deadline_tz})` : ""}</div>
              {t.value != null && <div>{tr("Value {}", number(t.value))}</div>}
              <div className="muted">{t.source}</div>
              <div className={t.check_is_old ? "warnline" : "muted"}>{t.checked_at ? tr("Status checked {}", fmtDate(t.checked_at)) : tr("Status not rechecked")}</div>
            </div>
          </li>)}
        </ol>}
      {(page > 1 || data.has_more) && <div className="row mt-2">
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("Page {}", page)}</span>
        <button type="button" disabled={!data.has_more} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </div>}
      <p className="small muted mt-4">{tr("Always read the official notice before deciding to bid: deadlines and documents can change. A tender is only listed while the official source says it is open.")}</p>
    </>}
  </>;
}
