from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment as JEnv
from jinja2 import FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Environment, PerformanceMetric, Project, SecurityFinding, TestRun, TestSuite, utcnow

_jenv = JEnv(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
             autoescape=select_autoescape(["html"]))

LIMITATIONS = [
    "Los resultados reflejan únicamente los casos ejecutados en este entorno y momento; no implican ausencia de errores.",
    "Las funcionalidades 'descubiertas' provienen de análisis estático y no están verificadas salvo indicación.",
    "Las métricas de carga dependen de la red y la máquina generadora; no son garantía de capacidad.",
    "Los casos 'bloqueados' no se ejecutaron por la política de seguridad del entorno.",
    "El análisis de seguridad estático produce falsos positivos y no detecta todas las vulnerabilidades; no reemplaza una revisión manual.",
]


def compare_runs(db: Session, base_id: int, head_id: int) -> dict:
    base, head = db.get(TestRun, base_id), db.get(TestRun, head_id)
    b = {r.case_name: r.status for r in base.results}
    h = {r.case_name: r.status for r in head.results}
    rows, regressions, fixed = [], [], []
    for name in sorted(set(b) | set(h)):
        bs, hs = b.get(name), h.get(name)
        change = "igual"
        if bs == "passed" and hs in ("failed", "error"):
            change = "regresión"
            regressions.append(name)
        elif bs in ("failed", "error") and hs == "passed":
            change = "corregido"
            fixed.append(name)
        elif bs is None:
            change = "nuevo"
        elif hs is None:
            change = "eliminado"
        elif bs != hs:
            change = "cambio"
        rows.append({"case": name, "base": bs, "head": hs, "change": change})
    pm = lambda rid: {m.name: m for m in db.scalars(select(PerformanceMetric).where(PerformanceMetric.run_id == rid))}  # noqa: E731
    perf = []
    bm, hm = pm(base_id), pm(head_id)
    for name in sorted(set(bm) & set(hm)):
        perf.append({"name": name, "base_p95": bm[name].p95_ms, "head_p95": hm[name].p95_ms,
                     "delta_p95_pct": round((hm[name].p95_ms - bm[name].p95_ms) / bm[name].p95_ms * 100, 1) if bm[name].p95_ms else None})
    return {"base_run": base_id, "head_run": head_id, "regressions": regressions, "fixed": fixed, "cases": rows, "performance": perf}


def render_run_report(db: Session, run: TestRun) -> str:
    project = db.get(Project, run.project_id)
    env = db.get(Environment, run.environment_id) if run.environment_id else None
    suite = db.get(TestSuite, run.suite_id) if run.suite_id else None
    metrics = list(db.scalars(select(PerformanceMetric).where(PerformanceMetric.run_id == run.id)))
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = sorted(db.scalars(select(SecurityFinding).where(SecurityFinding.run_id == run.id)),
                      key=lambda f: (order.get(f.severity, 9), f.file))
    art_root = Path(get_settings().artifacts_dir).resolve()
    images: dict[int, list[str]] = {}
    for r in run.results:
        for a in r.artifacts:
            p = (art_root / a.path).resolve()
            if a.kind == "screenshot" and art_root in p.parents and p.exists() and p.stat().st_size < 3_000_000:
                images.setdefault(r.id, []).append("data:image/png;base64," + base64.b64encode(p.read_bytes()).decode())
    return _jenv.get_template("report.html").render(
        run=run, project=project, env=env, suite=suite, metrics=metrics, findings=findings, images=images,
        limitations=LIMITATIONS, generated_at=utcnow(),
    )
