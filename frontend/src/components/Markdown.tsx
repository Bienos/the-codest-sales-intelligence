import { Fragment, ReactNode } from "react";
import { Link } from "react-router-dom";

/* The small piece of Markdown the assistant writes: paragraphs, headings, bullet and numbered lists, tables, **bold**, *italic*, `code` and links.
   Built as React elements (never innerHTML), so nothing in an answer can run as code. Links inside the app stay in the app; other links must be https. */

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*([^*\s][^*\n]*?)\*|\b_([^_\n]+)_\b)/g;
  let last = 0, m: RegExpExecArray | null, n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[2]) out.push(<b key={k}>{inline(m[2], k)}</b>);
    else if (m[3]) out.push(<code key={k}>{m[3]}</code>);
    else if (m[4]) {
      const href = m[5];
      if (/^\/(?!\/)/.test(href)) out.push(<Link key={k} to={href}>{m[4]}</Link>);
      else if (/^https:\/\//i.test(href)) out.push(<a key={k} href={href} target="_blank" rel="noopener noreferrer">{m[4]}</a>);
      else out.push(m[4]);
    } else if (m[6] || m[7]) out.push(<i key={k}>{m[6] || m[7]}</i>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0, b = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${b++}`;
    if (!line.trim()) { i++; continue; }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { blocks.push(<p key={key} className="md-h">{inline(h[2], key)}</p>); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      blocks.push(<div key={key} className="md-table"><table>
        <thead><tr>{head.map((c, j) => <th key={j} scope="col">{inline(c, `${key}h${j}`)}</th>)}</tr></thead>
        <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `${key}r${ri}c${j}`)}</td>)}</tr>)}</tbody>
      </table></div>);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items[items.length - 1] += " " + lines[i++].trim();
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={key}>{items.map((t, j) => <li key={j}>{inline(t, `${key}i${j}`)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={key}>{para.map((t, j) => <Fragment key={j}>{j > 0 && <br />}{inline(t, `${key}p${j}`)}</Fragment>)}</p>);
  }
  return <div className="md">{blocks}</div>;
}
