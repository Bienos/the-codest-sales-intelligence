// Thin API client. In production an authenticating reverse proxy (e.g. oauth2-proxy) injects the OIDC bearer token;
// in development a signed dev token can be pasted once and is kept in sessionStorage only.
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function token(): string | null {
  try { return sessionStorage.getItem("ri_token"); } catch { return null; }
}
export function clearToken() { try { sessionStorage.removeItem("ri_token"); } catch { /* storage unavailable */ } }
export function setToken(t: string) { try { sessionStorage.setItem("ri_token", t); } catch { /* storage unavailable */ } }

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const t = token();
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(init.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
export const post = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) });
export const patch = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "PATCH", body: JSON.stringify(body ?? {}) });
export const put = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "PUT", body: JSON.stringify(body ?? {}) });

export function fmtDate(v?: string | null): string {
  if (!v) return "unknown";
  const d = new Date(v);
  return isNaN(d.getTime()) ? "unknown" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
export function fmtDateTime(v?: string | null): string {
  if (!v) return "unknown";
  const d = new Date(v);
  return isNaN(d.getTime()) ? "unknown" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export async function upload<T = any>(path: string, file: File): Promise<T> {
  const t = token();
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
