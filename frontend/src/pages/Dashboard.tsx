import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";

type Dash = {
  projects: number; runs: number; results: Record<string, number>; critical_open: number;
  coverage: { static: number; observed: number; verified: number };
  last_run: { id: number; status: string; at: string } | null;
  trend: { id: number; passed: number; failed: number }[];
  performance: { run_id: number; p95_ms: number; rps: number }[];
  kill_switch: boolean;
};

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState("");
  const load = () => api<Dash>("/api/dashboard").then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function toggleKill() {
    if (!d) return;
    if (!confirm(d.kill_switch ? "¿Desactivar el kill switch?" : "¿Activar el kill switch? Se cancelarán todas las ejecuciones en curso.")) return;
    try { await api(`/api/system/kill-switch?active=${!d.kill_switch}`, { method: "POST" }); load(); } catch (e: any) { setErr(e.message); }
  }

  if (err) return <div className="alert">{err}</div>;
  if (!d) return <p className="muted">Cargando…</p>;
  const r = d.results;
  const kpis = [
    ["Proyectos", d.projects], ["Ejecuciones", d.runs], ["Pruebas aprobadas", r.passed ?? 0],
    ["Pruebas fallidas", (r.failed ?? 0) + (r.error ?? 0)], ["Bloqueadas por política", r.blocked ?? 0], ["Problemas críticos", d.critical_open],
  ];
  return (
    <>
      <div className="row between"><h1>Resumen general</h1>
        <button className={d.kill_switch ? "danger" : "secondary"} onClick={toggleKill}>
          Kill switch: {d.kill_switch ? "ACTIVO" : "inactivo"}
        </button>
      </div>
      <div className="grid">
        {kpis.map(([l, v]) => <div key={l} className="card kpi"><div className="label">{l}</div><div className="value">{v}</div></div>)}
        <div className="card kpi"><div className="label">Última ejecución</div>
          <div>{d.last_run ? <Link to={`/ejecuciones/${d.last_run.id}`}>#{d.last_run.id} <span className={`chip ${d.last_run.status}`}>{d.last_run.status}</span></Link> : "—"}</div></div>
      </div>

      <h2>Cobertura conocida de funcionalidades</h2>
      <div className="grid">
        <div className="card kpi"><div className="label">Descubiertas (estático, no verificadas)</div><div className="value">{d.coverage.static}</div></div>
        <div className="card kpi"><div className="label">Observadas en ejecución</div><div className="value">{d.coverage.observed}</div></div>
        <div className="card kpi"><div className="label">Verificadas por pruebas</div><div className="value">{d.coverage.verified}</div></div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", marginTop: 16 }}>
        <div className="card"><h3>Resultados por ejecución</h3>
          {d.trend.length === 0 ? <p className="muted">Sin ejecuciones todavía.</p> :
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={d.trend}><CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="id" stroke="var(--muted)" /><YAxis stroke="var(--muted)" allowDecimals={false} />
              <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} /><Legend />
              <Bar dataKey="passed" name="Aprobadas" stackId="a" fill="var(--ok)" />
              <Bar dataKey="failed" name="Fallidas" stackId="a" fill="var(--bad)" />
            </BarChart>
          </ResponsiveContainer>}
        </div>
        <div className="card"><h3>Rendimiento (p95 agregado de pruebas de carga)</h3>
          {d.performance.length === 0 ? <p className="muted">Sin pruebas de carga ejecutadas.</p> :
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={d.performance}><CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis dataKey="run_id" stroke="var(--muted)" /><YAxis stroke="var(--muted)" unit=" ms" />
                <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} />
                <Line dataKey="p95_ms" name="p95 (ms)" stroke="var(--accent)" strokeWidth={2} dot />
              </LineChart>
            </ResponsiveContainer>}
        </div>
      </div>
    </>
  );
}
