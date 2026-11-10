import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, streamEvents } from "../api";

type Finding = {
  id: number; tool: string; category: string; rule_id: string; severity: string; title: string; message: string;
  file: string; line: number | null; package: string; installed_version: string; fixed_version: string; cwe: string;
  details: { tools?: string[]; vuln_ids?: string[] }; status: string; fingerprint: string;
  triage: { status: string; note: string; by: string } | null;
};
type Data = { run: { id: number; status: string; summary: any; at: string } | null; previous_run: number | null; findings: Finding[]; resolved: any[] };

const TOOLS = ["semgrep", "osv-scanner", "pip-audit"];
const SEVS = ["critical", "high", "medium", "low"];

export default function SecurityTab({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [tools, setTools] = useState<string[]>(TOOLS);
  const [running, setRunning] = useState<number | null>(null);
  const [sev, setSev] = useState("");
  const [cat, setCat] = useState("");
  const [hideTriaged, setHideTriaged] = useState(true);
  const [err, setErr] = useState("");
  const load = () => api<Data>(`/api/projects/${projectId}/security-findings`).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [projectId]);

  useEffect(() => {
    if (!running) return;
    const ctl = new AbortController();
    streamEvents(`/api/runs/${running}/events`, (d) => {
      if (!["queued", "running"].includes(d.status)) { setRunning(null); load(); }
    }, ctl.signal).catch(() => { /* fin */ });
    return () => ctl.abort();
  }, [running]);

  async function start() {
    setErr("");
    try {
      const r = await api<{ id: number }>(`/api/projects/${projectId}/security-scans`, { method: "POST", body: { tools } });
      setRunning(r.id);
    } catch (e: any) { setErr(e.message); }
  }

  const list = (data?.findings ?? []).filter((f) => (!sev || f.severity === sev) && (!cat || f.category === cat)
    && !(hideTriaged && f.triage && f.triage.status !== "in_progress"));
  const triagedCount = (data?.findings ?? []).filter((f) => f.triage && f.triage.status !== "in_progress").length;

  async function triage(f: Finding, status: string) {
    let note = f.triage?.note ?? "";
    if (status === "false_positive" || status === "accepted_risk") {
      const n = prompt(status === "false_positive" ? "Motivo por el que es un falso positivo:" : "Motivo y alcance de la aceptación del riesgo:", note);
      if (n === null) return;
      note = n;
    }
    try { await api(`/api/projects/${projectId}/security-triage`, { method: "PUT", body: { fingerprint: f.fingerprint, status, note } }); load(); }
    catch (e: any) { setErr(e.message); }
  }
  const counts = Object.fromEntries(SEVS.map((s) => [s, (data?.findings ?? []).filter((f) => f.severity === s).length]));
  return (
    <>
      <div className="note">Análisis estático del código (solo lectura): Semgrep y OSV-Scanner corren en contenedores con el repositorio montado en
        solo lectura; pip-audit no instala paquetes. Los hallazgos pueden incluir falsos positivos: revisalos antes de tratarlos como defectos.</div>
      <div className="card row between">
        <div className="row">{TOOLS.map((t) => <label key={t} className="check">
          <input type="checkbox" checked={tools.includes(t)} onChange={(e) => setTools(e.target.checked ? [...tools, t] : tools.filter((x) => x !== t))} />{t}</label>)}</div>
        <button disabled={!!running || tools.length === 0} onClick={start}>{running ? `Analizando… (#${running})` : "Ejecutar análisis de seguridad"}</button>
      </div>
      {err && <div className="alert">{err}</div>}
      {!data?.run ? <p className="muted">Todavía no hay análisis de seguridad para este proyecto.</p> : <>
        <p className="muted">Último análisis: <Link to={`/ejecuciones/${data.run.id}`}>#{data.run.id}</Link> <span className={`chip ${data.run.status}`}>{data.run.status}</span>
          {" "}· {data.run.at.replace("T", " ").slice(0, 16)} {data.previous_run ? `· comparado con #${data.previous_run}` : ""}</p>
        <div className="grid">
          {SEVS.map((s) => <button key={s} className={`card kpi ${sev === s ? "" : "secondary"}`} style={{ textAlign: "left", color: "inherit", background: "var(--surface)" }}
            onClick={() => setSev(sev === s ? "" : s)}>
            <div className="label"><span className={`chip ${s}`}>{s}</span></div><div className="value">{counts[s]}</div></button>)}
          <div className="card kpi"><div className="label">Resueltos vs. anterior</div><div className="value">{data.resolved.length}</div></div>
        </div>
        <div className="row" style={{ margin: "12px 0" }}>
          <select style={{ width: 200 }} value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">Código y dependencias</option><option value="codigo">Solo código</option><option value="dependencia">Solo dependencias</option></select>
          <label className="check"><input type="checkbox" checked={hideTriaged} onChange={(e) => setHideTriaged(e.target.checked)} />Ocultar descartados/aceptados ({triagedCount})</label>
          <span className="muted">{list.length} hallazgos{sev && ` (${sev})`}</span>
        </div>
        <div className="card table-wrap"><table>
          <thead><tr><th>Severidad</th><th>Hallazgo</th><th>Ubicación</th><th>Corrección</th><th>Herramientas</th><th>Estado</th><th>Triage</th></tr></thead>
          <tbody>{list.map((f) => <tr key={f.id}>
            <td><span className={`chip ${f.severity}`}>{f.severity}</span></td>
            <td><details><summary style={{ cursor: "pointer" }}>{f.title}</summary>
              <div className="muted" style={{ maxWidth: 560 }}>{f.message.slice(0, 600)}</div>
              {f.cwe && <div className="muted">{f.cwe}</div>}{f.details.vuln_ids?.length ? <div className="muted">{f.details.vuln_ids.join(", ")}</div> : null}
              <code className="muted">{f.rule_id}</code></details></td>
            <td className="muted">{f.file}{f.line ? `:${f.line}` : ""}</td>
            <td>{f.fixed_version || "—"}</td>
            <td className="muted">{(f.details.tools ?? [f.tool]).join(", ")}</td>
            <td><span className={`chip ${f.status === "nuevo" ? "failed" : ""}`}>{f.status}</span></td>
            <td><select aria-label="Triage" value={f.triage?.status ?? "open"} onChange={(e) => triage(f, e.target.value)} style={{ width: 150 }}>
              <option value="open">Abierto</option><option value="in_progress">En corrección</option>
              <option value="false_positive">Falso positivo</option><option value="accepted_risk">Riesgo aceptado</option></select>
              {f.triage?.note && <div className="muted" title={`por ${f.triage.by}`}>{f.triage.note.slice(0, 80)}</div>}</td></tr>)}
            {list.length === 0 && <tr><td colSpan={7} className="muted">Sin hallazgos para este filtro.</td></tr>}</tbody></table></div>
        {data.resolved.length > 0 && <><h3>Resueltos respecto del análisis anterior</h3>
          <ul>{data.resolved.map((r, i) => <li key={i}><span className={`chip ${r.severity}`}>{r.severity}</span> {r.title} <span className="muted">{r.file}</span></li>)}</ul></>}
      </>}
    </>
  );
}
