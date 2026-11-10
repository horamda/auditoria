import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Env, Project, Run, Suite } from "../api";
import SecurityTab from "./SecurityTab";

const TEMPLATES: Record<string, any> = {
  api: [{ name: "Health check", objective: "La API responde", severity: "high",
    definition: { method: "GET", path: "/api/health", expect_status: 200, max_ms: 2000 } }],
  web: [{ name: "Login y navegación", objective: "Ingresar con cuenta de prueba", severity: "high",
    definition: { start_path: "/login", steps: [
      { action: "fill", selector: "[data-testid=login-email]", value: "${USER}" },
      { action: "fill", selector: "[data-testid=login-password]", value: "${PASSWORD}" },
      { action: "click", selector: "[data-testid=login-submit]" },
      { action: "expect_url", contains: "/" }, { action: "screenshot", name: "inicio" }] } }],
  android: [{ name: "Arranque y estabilidad", objective: "Inicia, rota, texto grande y se recupera sin cierres", severity: "high",
    definition: { apk_path: "C:/ruta/app-debug.apk", package: "com.ejemplo.app", activity: ".MainActivity", device_kind: "emulator",
      grant_permissions: [], checks: ["launch", "no_crash", "memory", "cpu", "rotation", "font_scale", "recovery"], settle_s: 8 } }],
  load: [{ name: "Carga 10 usuarios", objective: "Latencia bajo carga leve", severity: "medium",
    definition: { users: 10, spawn_rate: 2, duration_s: 60, requests: [{ method: "GET", path: "/api/health", weight: 1 }],
      abort_on_error_rate: 0.2, thresholds: { p95_ms: 800, error_rate: 0.01 } } }],
};

const ENV0 = { name: "", kind: "staging", base_url: "", api_url: "", allowed_hosts: "", allow_private_network: false,
  allow_writes: false, allow_load: false, max_users: 50, max_rps: 20, max_duration_s: 300, credentials_env_prefix: "" };

export default function ProjectDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [p, setP] = useState<Project | null>(null);
  const [tab, setTab] = useState("entornos");
  const [envs, setEnvs] = useState<Env[]>([]);
  const [suites, setSuites] = useState<Suite[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [feats, setFeats] = useState<any[]>([]);
  const [cov, setCov] = useState<Record<string, any>>({});
  const [disc, setDisc] = useState<any>(null);
  const [filter, setFilter] = useState({ layer: "", q: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [envForm, setEnvForm] = useState<any>({ ...ENV0 });
  const [suiteForm, setSuiteForm] = useState({ name: "", kind: "api", json: JSON.stringify(TEMPLATES.api, null, 2) });
  const [runForm, setRunForm] = useState({ suite_id: "", environment_id: "", version_label: "" });

  const base = `/api/projects/${id}`;
  const guard = (fn: () => Promise<any>) => async () => { setErr(""); setBusy(true); try { await fn(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  const loadAll = () => Promise.all([
    api<Project>(base).then(setP), api<Env[]>(`${base}/environments`).then(setEnvs),
    api<Suite[]>(`${base}/suites`).then(setSuites), api<Run[]>(`/api/runs?project_id=${id}`).then(setRuns),
    api(`${base}/coverage`).then(setCov),
  ]).catch((e) => setErr(e.message));
  const loadFeats = () => api<any[]>(`${base}/features?layer=${filter.layer}&q=${encodeURIComponent(filter.q)}`).then(setFeats).catch((e) => setErr(e.message));
  useEffect(() => { loadAll(); }, [id]);
  useEffect(() => { if (tab === "descubrimiento") loadFeats(); }, [tab, filter.layer]);

  const saveEnv = (e: FormEvent) => { e.preventDefault(); guard(async () => {
    const body = { ...envForm, allowed_hosts: envForm.allowed_hosts.split(",").map((s: string) => s.trim()).filter(Boolean),
      base_url: envForm.base_url || null, api_url: envForm.api_url || null };
    await api(`${base}/environments`, { method: "POST", body }); setEnvForm({ ...ENV0 }); loadAll();
  })(); };
  const saveSuite = (e: FormEvent) => { e.preventDefault(); guard(async () => {
    let cases; try { cases = JSON.parse(suiteForm.json); } catch { throw new Error("JSON de casos inválido"); }
    await api(`${base}/suites`, { method: "POST", body: { name: suiteForm.name, kind: suiteForm.kind, cases } });
    setSuiteForm({ ...suiteForm, name: "" }); loadAll();
  })(); };
  const startRun = (e: FormEvent) => { e.preventDefault(); guard(async () => {
    const env = envs.find((x) => x.id === +runForm.environment_id);
    if (env?.kind === "production" && !confirm("Vas a ejecutar sobre PRODUCCIÓN (solo lectura). ¿Confirmás?")) return;
    const r = await api<Run>(`${base}/runs`, { method: "POST", body: { suite_id: +runForm.suite_id, environment_id: +runForm.environment_id, version_label: runForm.version_label } });
    nav(`/ejecuciones/${r.id}`);
  })(); };

  if (!p) return err ? <div className="alert">{err}</div> : <p className="muted">Cargando…</p>;
  const tabs = ["entornos", "descubrimiento", "seguridad", "suites", "ejecutar", "historial"];
  return (
    <>
      <h1>{p.name} <span className="muted" style={{ fontSize: 14 }}>{p.version && `v${p.version}`} · {p.framework}</span></h1>
      <p className="muted">{p.description}</p>
      <div className="tabs">{tabs.map((t) => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
      {err && <div className="alert">{err}</div>}

      {tab === "entornos" && <>
        <div className="card table-wrap"><table>
          <thead><tr><th>Nombre</th><th>Tipo</th><th>URLs</th><th>Hosts autorizados</th><th>Escritura</th><th>Carga</th><th>Límites</th></tr></thead>
          <tbody>{envs.map((e) => <tr key={e.id}>
            <td>{e.name}</td><td><span className={`chip ${e.kind}`}>{e.kind}</span></td>
            <td className="muted">{e.base_url}<br />{e.api_url}</td><td>{e.allowed_hosts.join(", ") || <span className="alert">vacía</span>}</td>
            <td>{e.allow_writes ? "sí" : "no"}</td><td>{e.allow_load ? "sí" : "no"}</td>
            <td className="muted">{e.max_users} u · {e.max_rps} rps · {e.max_duration_s}s</td></tr>)}
            {envs.length === 0 && <tr><td colSpan={7} className="muted">Sin entornos.</td></tr>}</tbody></table></div>
        <h2>Nuevo entorno</h2>
        <form className="card" onSubmit={saveEnv}>
          <div className="note">Producción se fuerza a solo lectura: sin escrituras, sin carga y sin red privada, aunque se marquen.</div>
          <div className="form">
            <label>Nombre<input value={envForm.name} onChange={(e) => setEnvForm({ ...envForm, name: e.target.value })} required /></label>
            <label>Tipo<select value={envForm.kind} onChange={(e) => setEnvForm({ ...envForm, kind: e.target.value })}>
              <option value="development">development</option><option value="staging">staging</option><option value="production">production</option></select></label>
            <label>URL panel web<input value={envForm.base_url} onChange={(e) => setEnvForm({ ...envForm, base_url: e.target.value })} /></label>
            <label>URL API<input value={envForm.api_url} onChange={(e) => setEnvForm({ ...envForm, api_url: e.target.value })} /></label>
            <label>Hosts autorizados (coma)<input value={envForm.allowed_hosts} placeholder="app.up.railway.app, *.midominio.com" onChange={(e) => setEnvForm({ ...envForm, allowed_hosts: e.target.value })} /></label>
            <label>Prefijo de credenciales (env)<input value={envForm.credentials_env_prefix} placeholder="QA_FICHAYA_STG_" onChange={(e) => setEnvForm({ ...envForm, credentials_env_prefix: e.target.value.toUpperCase() })} /></label>
            <label>Máx. usuarios<input type="number" value={envForm.max_users} onChange={(e) => setEnvForm({ ...envForm, max_users: +e.target.value })} /></label>
            <label>Máx. RPS<input type="number" value={envForm.max_rps} onChange={(e) => setEnvForm({ ...envForm, max_rps: +e.target.value })} /></label>
            <label>Máx. duración (s)<input type="number" value={envForm.max_duration_s} onChange={(e) => setEnvForm({ ...envForm, max_duration_s: +e.target.value })} /></label>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            {[["allow_writes", "Permitir escrituras"], ["allow_load", "Permitir carga"], ["allow_private_network", "Red privada/localhost (solo admin)"]].map(([k, l]) =>
              <label key={k} className="check"><input type="checkbox" checked={envForm[k]} onChange={(e) => setEnvForm({ ...envForm, [k]: e.target.checked })} />{l}</label>)}
          </div>
          <div className="row" style={{ marginTop: 12 }}><button disabled={busy}>Guardar entorno</button></div>
        </form>
      </>}

      {tab === "descubrimiento" && <>
        <div className="row between">
          <div className="note" style={{ flex: 1 }}>Lo descubierto es <b>estático</b>: existe en el código, no está probado. Pasa a <b>verificado</b> solo cuando una prueba aprobada lo cubre.</div>
          <button disabled={busy} onClick={guard(async () => { setDisc(await api(`${base}/discover`, { method: "POST" })); loadFeats(); loadAll(); })}>{busy ? "Analizando…" : "Analizar repositorio"}</button>
        </div>
        {disc && <pre>{JSON.stringify(disc, null, 2)}</pre>}
        <div className="grid">{Object.entries(cov).map(([layer, c]: any) =>
          <div key={layer} className="card kpi"><div className="label">{layer}</div><div className="value">{c.static + c.observed + c.verified}</div>
            <div className="muted">{c.verified} verificadas · {c.observed} observadas</div></div>)}</div>
        <div className="row" style={{ margin: "12px 0" }}>
          <select style={{ width: 180 }} value={filter.layer} onChange={(e) => setFilter({ ...filter, layer: e.target.value })}>
            <option value="">Todas las capas</option>{["backend", "flutter", "web", "database"].map((l) => <option key={l}>{l}</option>)}</select>
          <input style={{ maxWidth: 280 }} placeholder="Buscar…" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} onKeyDown={(e) => e.key === "Enter" && loadFeats()} />
          <span className="muted">{feats.length} resultados (máx. 500)</span>
        </div>
        <div className="card table-wrap"><table>
          <thead><tr><th>ID estable</th><th>Capa</th><th>Tipo</th><th>Nombre</th><th>Ubicación</th><th>Evidencia</th></tr></thead>
          <tbody>{feats.map((f) => <tr key={f.id} style={{ opacity: f.present ? 1 : .5 }}>
            <td><code>{f.stable_id}</code></td><td>{f.layer}</td><td>{f.kind}</td>
            <td>{f.name}{f.details?.auth_detected === false && f.kind === "endpoint" && <span className="chip warn" title="No se detectó decorador de autenticación"> sin auth detectada</span>}</td>
            <td className="muted">{f.location}</td><td><span className={`chip ${f.evidence_level}`}>{f.evidence_level}</span></td></tr>)}</tbody></table></div>
      </>}

      {tab === "seguridad" && <SecurityTab projectId={id!} />}

      {tab === "suites" && <>
        {suites.map((s) => <div key={s.id} className="card" style={{ marginBottom: 10 }}>
          <div className="row between"><b>{s.name} <span className="chip">{s.kind}</span></b>
            <button className="secondary" onClick={guard(async () => { if (confirm("¿Eliminar la suite?")) { await api(`${base}/suites/${s.id}`, { method: "DELETE" }); loadAll(); } })}>Quitar</button></div>
          <ul>{s.cases.map((c) => <li key={c.id}>{c.name} <span className={`chip ${c.severity}`}>{c.severity}</span> <span className="muted">{c.objective}</span></li>)}</ul>
        </div>)}
        <h2>Nueva suite</h2>
        <form className="card" onSubmit={saveSuite}>
          <div className="form">
            <label>Nombre<input value={suiteForm.name} onChange={(e) => setSuiteForm({ ...suiteForm, name: e.target.value })} required /></label>
            <label>Tipo<select value={suiteForm.kind} onChange={(e) => setSuiteForm({ ...suiteForm, kind: e.target.value, json: JSON.stringify(TEMPLATES[e.target.value], null, 2) })}>
              <option value="api">API</option><option value="web">Web (Playwright)</option><option value="load">Carga (Locust)</option><option value="android">Android (adb)</option></select></label>
          </div>
          <label style={{ marginTop: 10 }}>Casos (JSON) — <span className="muted">${"{VAR}"} se resuelve desde variables de entorno con el prefijo del entorno</span>
            <textarea value={suiteForm.json} onChange={(e) => setSuiteForm({ ...suiteForm, json: e.target.value })} /></label>
          <div className="row" style={{ marginTop: 12 }}><button disabled={busy}>Crear suite</button></div>
        </form>
      </>}

      {tab === "ejecutar" && <form className="card" onSubmit={startRun}>
        <div className="form">
          <label>Suite<select value={runForm.suite_id} onChange={(e) => setRunForm({ ...runForm, suite_id: e.target.value })} required>
            <option value="">—</option>{suites.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.kind})</option>)}</select></label>
          <label>Entorno<select value={runForm.environment_id} onChange={(e) => setRunForm({ ...runForm, environment_id: e.target.value })} required>
            <option value="">—</option>{envs.map((e) => <option key={e.id} value={e.id}>{e.name} ({e.kind})</option>)}</select></label>
          <label>Etiqueta de versión<input value={runForm.version_label} onChange={(e) => setRunForm({ ...runForm, version_label: e.target.value })} placeholder="1.2.4" /></label>
        </div>
        <div className="row" style={{ marginTop: 12 }}><button disabled={busy}>Ejecutar</button></div>
      </form>}

      {tab === "historial" && <div className="card table-wrap"><table>
        <thead><tr><th>#</th><th>Tipo</th><th>Versión</th><th>Estado</th><th>Resumen</th><th>Fecha</th></tr></thead>
        <tbody>{runs.map((r) => <tr key={r.id}><td><Link to={`/ejecuciones/${r.id}`}>#{r.id}</Link></td><td>{r.kind}</td><td>{r.version_label || "—"}</td>
          <td><span className={`chip ${r.status}`}>{r.status}</span></td>
          <td className="muted">{r.summary.passed ?? 0}✓ {r.summary.failed ?? 0}✗ {r.summary.blocked ?? 0} bloq.</td><td className="muted">{r.created_at.replace("T", " ").slice(0, 16)}</td></tr>)}</tbody></table></div>}
    </>
  );
}
