import { FormEvent, useState } from "react";
import { api } from "../api";

export default function Compare() {
  const [ids, setIds] = useState({ base: "", head: "" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  async function go(e: FormEvent) {
    e.preventDefault(); setErr("");
    try { setRes(await api(`/api/runs/compare/${ids.base}/${ids.head}`)); } catch (e: any) { setErr(e.message); }
  }
  return (
    <>
      <h1>Comparar ejecuciones (regresión)</h1>
      <form className="card row" onSubmit={go}>
        <label>Línea base (#)<input type="number" value={ids.base} onChange={(e) => setIds({ ...ids, base: e.target.value })} required /></label>
        <label>Nueva ejecución (#)<input type="number" value={ids.head} onChange={(e) => setIds({ ...ids, head: e.target.value })} required /></label>
        <button style={{ alignSelf: "end" }}>Comparar</button>
      </form>
      {err && <div className="alert">{err}</div>}
      {res && <>
        <div className="grid" style={{ marginTop: 16 }}>
          <div className="card kpi"><div className="label">Regresiones</div><div className="value" style={{ color: res.regressions.length ? "var(--bad)" : undefined }}>{res.regressions.length}</div></div>
          <div className="card kpi"><div className="label">Corregidas</div><div className="value">{res.fixed.length}</div></div>
        </div>
        <h2>Casos</h2>
        <div className="card table-wrap"><table>
          <thead><tr><th>Caso</th><th>Base</th><th>Nueva</th><th>Cambio</th></tr></thead>
          <tbody>{res.cases.map((c: any) => <tr key={c.case}><td>{c.case}</td>
            <td><span className={`chip ${c.base}`}>{c.base ?? "—"}</span></td><td><span className={`chip ${c.head}`}>{c.head ?? "—"}</span></td>
            <td>{c.change === "regresión" ? <b style={{ color: "var(--bad)" }}>{c.change}</b> : c.change}</td></tr>)}</tbody></table></div>
        {res.performance.length > 0 && <><h2>Rendimiento (p95)</h2><div className="card table-wrap"><table>
          <thead><tr><th>Operación</th><th>Base</th><th>Nueva</th><th>Δ</th></tr></thead>
          <tbody>{res.performance.map((p: any) => <tr key={p.name}><td>{p.name}</td><td>{p.base_p95.toFixed(0)} ms</td><td>{p.head_p95.toFixed(0)} ms</td>
            <td>{p.delta_p95_pct == null ? "—" : `${p.delta_p95_pct > 0 ? "+" : ""}${p.delta_p95_pct}%`}</td></tr>)}</tbody></table></div></>}
      </>}
    </>
  );
}
