import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api<{ access_token: string }>("/api/auth/login", { method: "POST", body: { email, password } });
      setToken(r.access_token);
      nav("/");
    } catch (e: any) {
      setErr(e.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>QA Control Center</h1>
        <label>Email<input data-testid="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></label>
        <label>Contraseña<input data-testid="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" /></label>
        {err && <div className="alert">{err}</div>}
        <button data-testid="login-submit" disabled={busy}>{busy ? "Ingresando…" : "Ingresar"}</button>
      </form>
    </div>
  );
}
