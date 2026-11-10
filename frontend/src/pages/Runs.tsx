import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Project, Run } from "../api";

export default function Runs() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [projects, setProjects] = useState<Record<number, string>>({});
  const [err, setErr] = useState("");
  useEffect(() => {
    api<Run[]>("/api/runs?limit=200").then(setRuns).catch((e) => setErr(e.message));
    api<Project[]>("/api/projects").then((ps) => setProjects(Object.fromEntries(ps.map((p) => [p.id, p.name])))).catch(() => {});
  }, []);
  return (
    <>
      <h1>Historial de ejecuciones</h1>
      {err && <div className="alert">{err}</div>}
      <div className="card table-wrap"><table>
        <thead><tr><th>#</th><th>Proyecto</th><th>Tipo</th><th>Versión</th><th>Estado</th><th>Resumen</th><th>Usuario</th><th>Fecha</th></tr></thead>
        <tbody>{runs.map((r) => <tr key={r.id}>
          <td><Link to={`/ejecuciones/${r.id}`}>#{r.id}</Link></td><td>{projects[r.project_id] ?? r.project_id}</td><td>{r.kind}</td>
          <td>{r.version_label || "—"}</td><td><span className={`chip ${r.status}`}>{r.status}</span></td>
          <td className="muted">{r.summary.passed ?? 0}✓ {(r.summary.failed ?? 0) + (r.summary.error ?? 0)}✗ {r.summary.blocked ?? 0} bloq.</td>
          <td className="muted">{r.triggered_by}</td><td className="muted">{r.created_at.replace("T", " ").slice(0, 16)}</td></tr>)}
          {runs.length === 0 && <tr><td colSpan={8} className="muted">Sin ejecuciones.</td></tr>}</tbody>
      </table></div>
    </>
  );
}
