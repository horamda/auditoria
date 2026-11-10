import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { getToken, setToken } from "./api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";
import Compare from "./pages/Compare";
import "./styles.css";

function useTheme() {
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem("qacc_theme") ?? "auto"; } catch { return "auto"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    theme === "auto" ? root.removeAttribute("data-theme") : root.setAttribute("data-theme", theme);
    try { localStorage.setItem("qacc_theme", theme); } catch { /* sin storage */ }
  }, [theme]);
  return [theme, setTheme] as const;
}

function Shell() {
  const [theme, setTheme] = useTheme();
  if (!getToken()) return <Navigate to="/login" replace />;
  return (
    <div className="layout">
      <nav className="side">
        <div className="brand">QA Control Center</div>
        <NavLink to="/" end>Dashboard</NavLink>
        <NavLink to="/proyectos">Proyectos</NavLink>
        <NavLink to="/ejecuciones">Ejecuciones</NavLink>
        <NavLink to="/comparar">Comparar</NavLink>
        <div className="grow" />
        <select aria-label="Tema" value={theme} onChange={(e) => setTheme(e.target.value)}>
          <option value="auto">Tema: sistema</option>
          <option value="light">Tema: claro</option>
          <option value="dark">Tema: oscuro</option>
        </select>
        <button className="secondary" onClick={() => { setToken(null); location.assign("/login"); }}>Salir</button>
      </nav>
      <main><Outlet /></main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Shell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/proyectos" element={<Projects />} />
          <Route path="/proyectos/:id" element={<ProjectDetail />} />
          <Route path="/ejecuciones" element={<Runs />} />
          <Route path="/ejecuciones/:id" element={<RunDetail />} />
          <Route path="/comparar" element={<Compare />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
