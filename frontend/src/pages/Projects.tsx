import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Project } from "../api";

const EMPTY = { name: "", description: "", app_type: "mixed", repo_url: "", local_path: "", admin_url: "", api_url: "", framework: "", language: "", version: "", owner: "" };

export default function Projects() {
  const [items, setItems] = useState<Project[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const load = () => api<Project[]>("/api/projects").then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function create(e: FormEvent) {
    e.preventDefault(); setErr("");
    const body: any = { ...form };
    for (const k of ["repo_url", "local_path", "admin_url", "api_url"]) if (!body[k]) body[k] = null;
    try { await api("/api/projects", { method: "POST", body }); setForm({ ...EMPTY }); setOpen(false); load(); }
    catch (e: any) { setErr(e.message); }
  }
  const f = (k: keyof typeof EMPTY, label: string, ph = "") => (
    <label>{label}<input value={form[k]} placeholder={ph} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>
  );

  return (
    <>
      <div className="row between"><h1>Proyectos</h1><button onClick={() => setOpen(!open)}>{open ? "Cancelar" : "Nuevo proyecto"}</button></div>
      {err && <div className="alert">{err}</div>}
      {open && (
        <form className="card" onSubmit={create} style={{ marginBottom: 16 }}>
          <div className="form">
            {f("name", "Nombre *")}{f("app_type", "Tipo de aplicación")}{f("framework", "Framework")}{f("language", "Lenguaje")}
            {f("version", "Versión")}{f("owner", "Responsable")}
            {f("local_path", "Ruta local", "Debe estar en QA_ALLOWED_REPO_ROOTS")}{f("repo_url", "Repositorio GitHub", "https://github.com/…")}
            {f("admin_url", "URL panel admin")}{f("api_url", "URL API")}
          </div>
          <label style={{ marginTop: 10 }}>Descripción<input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="row" style={{ marginTop: 12 }}><button disabled={!form.name}>Guardar</button></div>
        </form>
      )}
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Nombre</th><th>Tipo</th><th>Framework</th><th>Versión</th><th>Estado</th><th>Componentes</th></tr></thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td><Link to={`/proyectos/${p.id}`}>{p.name}</Link><div className="muted">{p.description.slice(0, 90)}</div></td>
                <td>{p.app_type}</td><td>{p.framework}</td><td>{p.version || "—"}</td><td>{p.status}</td>
                <td>{p.components.map((c) => <span key={c.id} className="chip" style={{ marginRight: 4 }}>{c.kind}</span>)}</td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={6} className="muted">Sin proyectos registrados.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
