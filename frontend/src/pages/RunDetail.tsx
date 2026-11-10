import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, download, getToken, openAuthed, RunDetail as RD, streamEvents } from "../api";

function Shot({ id }: { id: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let url = "";
    fetch(`/api/artifacts/${id}`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => r.blob()).then((b) => { url = URL.createObjectURL(b); setSrc(url); });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id]);
  return src ? <img className="shot" src={src} alt="Evidencia" /> : null;
}

export default function RunDetail() {
  const { id } = useParams();
  const [run, setRun] = useState<RD | null>(null);
  const [metrics, setMetrics] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const load = () => Promise.all([api<RD>(`/api/runs/${id}`).then(setRun), api<any[]>(`/api/runs/${id}/metrics`).then(setMetrics)]).catch((e) => setErr(e.message));

  useEffect(() => {
    load();
    const ctl = new AbortController();
    streamEvents(`/api/runs/${id}/events`, () => load(), ctl.signal).catch(() => { /* fin del stream */ });
    return () => ctl.abort();
  }, [id]);

  if (!run) return err ? <div className="alert">{err}</div> : <p className="muted">Cargando…</p>;
  const active = run.status === "queued" || run.status === "running";
  return (
    <>
      <div className="row between">
        <h1>Ejecución #{run.id} <span className={`chip ${run.status}`}>{run.status}</span></h1>
        <div className="row">
          {active && <button className="danger" onClick={() => api(`/api/runs/${id}/cancel`, { method: "POST" }).then(load)}>Cancelar</button>}
          <button className="secondary" onClick={() => openAuthed(`/api/runs/${id}/report.html`)}>Ver informe</button>
          <button onClick={() => download(`/api/runs/${id}/report.html?download=true`, `informe_run_${id}.html`)}>Descargar HTML</button>
        </div>
      </div>
      <p className="muted">{run.kind} · versión {run.version_label || "—"} · entorno {run.config?.environment_kind} · por {run.triggered_by} · {run.started_at?.replace("T", " ").slice(0, 19)} → {run.finished_at?.replace("T", " ").slice(0, 19) ?? "en curso"}</p>
      <div className="grid">
        {[["Total", "total"], ["Aprobadas", "passed"], ["Fallidas", "failed"], ["Errores", "error"], ["Bloqueadas", "blocked"], ["Omitidas", "skipped"]].map(([l, k]) =>
          <div key={k} className="card kpi"><div className="label">{l}</div><div className="value">{run.summary[k] ?? (active ? "…" : 0)}</div></div>)}
      </div>

      {metrics.length > 0 && <><h2>Métricas medidas</h2><div className="card table-wrap"><table>
        <thead><tr><th>Operación</th><th>Solicitudes</th><th>Fallidas</th><th>Prom.</th><th>p50</th><th>p95</th><th>p99</th><th>RPS</th><th>Fallos/s</th></tr></thead>
        <tbody>{metrics.map((m) => <tr key={m.name}><td>{m.name}</td><td>{m.requests}</td><td>{m.failures}</td><td>{m.avg_ms.toFixed(0)} ms</td>
          <td>{m.p50_ms.toFixed(0)}</td><td>{m.p95_ms.toFixed(0)}</td><td>{m.p99_ms.toFixed(0)}</td><td>{m.rps.toFixed(2)}</td><td>{m.failures_per_s.toFixed(2)}</td></tr>)}</tbody></table></div></>}

      <h2>Resultados</h2>
      {run.results.map((r) => <details key={r.id} className="card" style={{ marginBottom: 8 }} open={r.status !== "passed"}>
        <summary className="row between" style={{ cursor: "pointer" }}>
          <span><span className={`chip ${r.status}`}>{r.status}</span> <b>{r.case_name}</b></span>
          <span className="muted">{r.duration_ms.toFixed(0)} ms {r.status !== "passed" && <span className={`chip ${r.severity}`}>{r.severity}</span>}</span>
        </summary>
        {r.error && <div className="alert">{r.error}</div>}
        {r.recommendation && <p className="muted">Recomendación (orientativa): {r.recommendation}</p>}
        {r.artifacts.filter((a) => a.kind === "screenshot").map((a) => <Shot key={a.id} id={a.id} />)}
        {Object.keys(r.details ?? {}).length > 0 && <pre>{JSON.stringify(r.details, null, 2)}</pre>}
      </details>)}
      {run.results.length === 0 && <p className="muted">{active ? "Esperando resultados…" : "Sin resultados."}</p>}

      <h2>Log</h2><pre>{run.log || "—"}</pre>
    </>
  );
}
