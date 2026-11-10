const TOKEN_KEY = "qacc_token";

export function getToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* sin storage */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method: opts.method ?? "GET", headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401 && path !== "/api/auth/login") {
    setToken(null);
    window.location.assign("/login");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* cuerpo no JSON */ }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

/** Descarga autenticada (los enlaces simples no envían el token). */
export async function download(path: string, filename: string) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new ApiError(res.status, "No se pudo descargar");
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

export async function openAuthed(path: string) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } });
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
}

/** SSE con encabezado Authorization (EventSource no lo permite). */
export async function streamEvents(path: string, onData: (d: any) => void, signal: AbortSignal) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` }, signal });
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onData(JSON.parse(line.slice(6)));
    }
  }
}

export type Project = {
  id: number; name: string; description: string; app_type: string; repo_url: string | null; local_path: string | null;
  admin_url: string | null; api_url: string | null; framework: string; language: string; version: string; status: string;
  owner: string; components: { id: number; kind: string; name: string; path: string | null; framework: string }[];
};
export type Env = {
  id: number; name: string; kind: "development" | "staging" | "production"; base_url: string | null; api_url: string | null;
  allowed_hosts: string[]; allow_private_network: boolean; allow_writes: boolean; allow_load: boolean;
  max_users: number; max_rps: number; max_duration_s: number; credentials_env_prefix: string;
};
export type Suite = { id: number; name: string; kind: "api" | "web" | "load"; description: string; cases: Case[] };
export type Case = { id: number; name: string; objective: string; definition: any; severity: string; enabled: boolean; feature_ids: string[] };
export type Run = {
  id: number; project_id: number; suite_id: number | null; environment_id: number | null; kind: string; status: string;
  triggered_by: string; version_label: string; summary: Record<string, number>; created_at: string; started_at: string | null; finished_at: string | null;
};
export type Result = {
  id: number; case_name: string; status: string; duration_ms: number; error: string; severity: string; recommendation: string;
  details: any; artifacts: { id: number; kind: string; path: string }[];
};
export type RunDetail = Run & { log: string; config: any; results: Result[] };
