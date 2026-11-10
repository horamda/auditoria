import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type Sched = {
  id: number; suite_id: number; environment_id: number; name: string; interval_minutes: number;
  enabled: boolean; next_run_at: string; last_run_id: number | null;
};
type Opt = { id: number; name: string; kind: string };

const INTERVALS: [number, string][] = [[5, "Cada 5 min"], [15, "Cada 15 min"], [60, "Cada hora"], [360, "Cada 6 horas"], [1440, "Cada día"]];

export default function AutomationTab({ projectId, suites, envs, onChange }: { projectId: string; suites: Opt[]; envs: Opt[]; onChange: () => void }) {
  const [items, setItems] = useState<Sched[]>([]);
  const [form, setForm] = useState({ suite_id: "", environment_id: "", interval_minutes: "60" });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const base = `/api/projects/${projectId}`;
  const load = () => api<Sched[]>(`${base}/schedules`).then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [projectId]);

  const run = async (fn: () => Promise<any>) => { setErr(""); setMsg(""); try { await fn(); await load(); } catch (e: any) { setErr(e.message); } };
  const nameOf = (list: Opt[], id: number) => list.find((x) => x.id === id)?.name ?? `#${id}`;
  const fmt = (s: string) => s.replace("T", " ").slice(0, 16) + " UTC";

  const create = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    await api(`${base}/schedules`, { method: "POST", body: { suite_id: +form.suite_id, environment_id: +form.environment_id, interval_minutes: +form.interval_minutes } });
    setMsg("Programación creada: la primera ejecución sale en ~1 minuto.");
  }); };
  const toggle = (s: Sched) => run(() => api(`${base}/schedules/${s.id}`, { method: "PATCH", body: { suite_id: s.suite_id, environment_id: s.environment_id, interval_minutes: s.interval_minutes, enabled: !s.enabled } }));
  const remove = (s: Sched) => run(() => api(`${base}/schedules/${s.id}`, { method: "DELETE" }));
  const smoke = () => run(async () => {
    const r = await api<{ suite_id: number; cases: number }>(`${base}/suites/auto-smoke`, { method: "POST", body: {} });
    setMsg(`Suite "Humo (autogenerada)" lista con ${r.cases} casos. Solo comprueba disponibilidad (sin error 5xx).`);
    onChange();
  });

  return <>
    {err && <div className="error">{err}</div>}
    {msg && <div className="card muted">{msg}</div>}
    <form className="card" onSubmit={create}>
      <h3>Ejecución automática</h3>
      <p className="muted">Corre la suite sola cada cierto tiempo y avisa si falla (requiere <code>QA_NOTIFY_WEBHOOK_URL</code>). Las pruebas de carga no se programan.</p>
      <div className="form">
        <label>Suite<select value={form.suite_id} onChange={(e) => setForm({ ...form, suite_id: e.target.value })} required>
          <option value="">—</option>{suites.filter((s) => s.kind !== "load").map((s) => <option key={s.id} value={s.id}>{s.name} ({s.kind})</option>)}</select></label>
        <label>Entorno<select value={form.environment_id} onChange={(e) => setForm({ ...form, environment_id: e.target.value })} required>
          <option value="">—</option>{envs.map((e) => <option key={e.id} value={e.id}>{e.name} ({e.kind})</option>)}</select></label>
        <label>Frecuencia<select value={form.interval_minutes} onChange={(e) => setForm({ ...form, interval_minutes: e.target.value })}>
          {INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      </div>
      <div className="row" style={{ marginTop: 12 }}><button>Programar</button>
        <button type="button" onClick={smoke} title="Crea una suite con los GET descubiertos sin autenticación">Generar suite de humo desde el descubrimiento</button></div>
    </form>
    <div className="card table-wrap"><table>
      <thead><tr><th>Nombre</th><th>Frecuencia</th><th>Próxima</th><th>Última</th><th>Estado</th><th></th></tr></thead>
      <tbody>{items.map((s) => <tr key={s.id}>
        <td>{s.name || `${nameOf(suites, s.suite_id)} @ ${nameOf(envs, s.environment_id)}`}</td>
        <td>{INTERVALS.find(([v]) => v === s.interval_minutes)?.[1] ?? `Cada ${s.interval_minutes} min`}</td>
        <td className="muted">{s.enabled ? fmt(s.next_run_at) : "—"}</td>
        <td>{s.last_run_id ? <Link to={`/ejecuciones/${s.last_run_id}`}>#{s.last_run_id}</Link> : <span className="muted">—</span>}</td>
        <td><span className={`chip ${s.enabled ? "passed" : "skipped"}`}>{s.enabled ? "activa" : "pausada"}</span></td>
        <td className="row"><button type="button" onClick={() => toggle(s)}>{s.enabled ? "Pausar" : "Reanudar"}</button>
          <button type="button" className="danger" onClick={() => remove(s)}>Eliminar</button></td></tr>)}
        {items.length === 0 && <tr><td colSpan={6} className="muted">Sin programaciones.</td></tr>}</tbody></table></div>
    <div className="card">
      <h3>Después de cada deploy</h3>
      <p className="muted">Un CI o Railway puede llamar a <code>POST /api/hooks/deploy</code> con el encabezado <code>X-QA-Token</code> y el cuerpo{" "}
        <code>{`{"project":"<nombre>","environment":"<entorno>","version_label":"<versión>"}`}</code>. Lanza las suites api y web del entorno
        (nunca carga). Requiere definir <code>QA_DEPLOY_HOOK_TOKEN</code>; sin él queda deshabilitado. Ver <code>docs/AUTOMATIZACION.md</code>.</p>
    </div>
  </>;
}
