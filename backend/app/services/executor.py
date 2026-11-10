"""Orquestación de ejecuciones.

MVP: pool de hilos en proceso con cancelación cooperativa, timeout global y kill switch.
La interfaz (`submit_run` / `cancel_run`) está aislada para reemplazarla por Celery+Redis
sin cambiar la API (ver docs/ARQUITECTURA.md).
"""
from __future__ import annotations

import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.guard import EnvPolicy
from app.core.security import redact
from app.models import (
    DiscoveredFeature, Environment, PerformanceMetric, TestArtifact, TestResult, TestRun, TestSuite, utcnow,
)
from app.workers.android_runner import run_android_case
from app.workers.api_runner import run_api_case
from app.workers.base import Cancelled, RunContext
from app.workers.load_runner import run_load_case
from app.workers.web_runner import run_web_case

_pool = ThreadPoolExecutor(max_workers=get_settings().worker_threads, thread_name_prefix="qa-run")
_cancel: dict[int, threading.Event] = {}

RECOMMENDATIONS = {
    "blocked": "La política de seguridad bloqueó la operación. Revisar allowlist/permisos del entorno o usar staging.",
    "failed": "Revisar la evidencia y el error; reproducir manualmente antes de clasificar como defecto.",
    "error": "Error de infraestructura o configuración del escenario; corregir antes de evaluar la aplicación.",
}


def submit_run(run_id: int) -> None:
    _cancel[run_id] = threading.Event()
    _pool.submit(_execute, run_id)


def cancel_run(run_id: int) -> None:
    if ev := _cancel.get(run_id):
        ev.set()


def execute_sync(run_id: int) -> None:
    """Para tests y CLI."""
    _cancel[run_id] = threading.Event()
    _execute(run_id)


def _execute_security(db, run: TestRun) -> None:
    """Escaneo estático de seguridad sobre el código del proyecto (solo lectura)."""
    from pathlib import Path

    from app.core.guard import PolicyViolation
    from app.models import Project, SecurityFinding
    from app.services.discovery_service import resolve_project_root
    from app.workers import security_runner as sr

    log: list[str] = []
    cancel = _cancel.setdefault(run.id, threading.Event())
    timeout = min(int(run.config.get("timeout_s", 1800)), get_settings().max_run_timeout_s)
    deadline = time.monotonic() + timeout
    ctx = RunContext(run_id=run.id, policy=None, base_url=None, api_url=None, credentials_env_prefix="",  # type: ignore[arg-type]
                     deadline=deadline, cancel_event=cancel)

    def checkpoint() -> None:
        db.refresh(run)
        if run.cancel_requested:
            cancel.set()
        ctx.checkpoint()

    def note(msg: str) -> None:
        log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    run.status, run.started_at = "running", utcnow()
    db.commit()
    project = db.get(Project, run.project_id)
    try:
        root = resolve_project_root(project)
    except PolicyViolation as exc:
        run.status, run.finished_at, run.log = "blocked", utcnow(), str(exc)
        db.commit()
        return
    subpaths = sorted({c.path for c in project.components if c.path}) or ["."]
    scans = [(root / sp).resolve() for sp in subpaths if (root / sp).resolve().is_dir()]
    tools = [t for t in run.config.get("tools", list(sr.ALL_TOOLS)) if t in sr.ALL_TOOLS]
    # Cada herramienta puede usar el tiempo global restante; el límite real lo aplica checkpoint() (deadline global)
    per_tool_timeout = timeout
    note(f"Raíz {root} · carpetas {subpaths} · herramientas {tools}")
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "blocked": 0}
    all_findings: list[dict] = []
    final = None
    for tool in tools:
        t0 = time.perf_counter()
        found, extra, status, error = [], {}, "passed", ""
        try:
            for scan in scans:
                scan_rel = scan.relative_to(root).as_posix()
                note(f"{tool} en {scan_rel or '.'}")
                if tool == "semgrep":
                    found += sr.run_semgrep(scan, scan_rel, per_tool_timeout, checkpoint, run.config.get("semgrep_configs"))
                elif tool == "osv-scanner":
                    found += sr.run_osv(scan, scan_rel, per_tool_timeout, checkpoint)
                else:
                    f, st = sr.run_pip_audit(scan, root, Path(get_settings().artifacts_dir) / f"run_{run.id}", per_tool_timeout, checkpoint)
                    found += f
                    for k, v in st.items():
                        extra[k] = extra.get(k, 0) + v
        except Cancelled as exc:
            note(str(exc))
            final = "cancelled"
        except sr.ToolUnavailable as exc:
            status, error = "skipped", str(exc)
        except Exception as exc:  # noqa: BLE001
            status, error = "error", f"{type(exc).__name__}: {str(exc)[:600]}"
        if final:
            break
        by_sev: dict[str, int] = {}
        for f in found:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        if status == "passed" and (by_sev.get("high", 0) or by_sev.get("critical", 0)):
            status, error = "failed", f"{by_sev.get('critical', 0)} críticos y {by_sev.get('high', 0)} altos"
        counts[status] += 1
        if tool == "pip-audit" and extra.get("unpinned_skipped"):
            extra["limitacion"] = f"{extra['unpinned_skipped']} dependencias sin versión fija no se auditaron con pip-audit"
        db.add(TestResult(run_id=run.id, case_name=f"Seguridad: {tool}", status=status,
                          duration_ms=round((time.perf_counter() - t0) * 1000, 1), error=error,
                          severity="high" if status == "failed" else "info",
                          recommendation="Revisar cada hallazgo: el análisis estático produce falsos positivos." if found else RECOMMENDATIONS.get(status, ""),
                          details={"findings_by_severity": by_sev, **extra}))
        note(f" -> {status} {by_sev} {error}")
        all_findings += found
        db.commit()

    merged = sr.merge_dependency_duplicates(all_findings)
    cols = {"fingerprint", "tool", "category", "rule_id", "severity", "title", "message", "file", "line",
            "package", "installed_version", "fixed_version", "cwe"}
    for f in merged:
        db.add(SecurityFinding(run_id=run.id, project_id=run.project_id, **{k: f.get(k) if f.get(k) is not None else ("" if k != "line" else None) for k in cols},
                               details={"tools": f.get("tools", [f["tool"]]), "vuln_ids": f.get("vuln_ids", []),
                                        "confidence": f.get("confidence", ""), "ecosystem": f.get("ecosystem", "")}))
    sev_total: dict[str, int] = {}
    for f in merged:
        sev_total[f["severity"]] = sev_total.get(f["severity"], 0) + 1
    run.summary = {**counts, "total": sum(counts.values()), "findings": len(merged), "findings_by_severity": sev_total}
    run.status = final or ("error" if counts["error"] and not counts["passed"] + counts["failed"] else
                           "failed" if counts["failed"] or counts["error"] else "passed")
    run.finished_at = utcnow()
    note(f"Fin: {run.status} · {len(merged)} hallazgos únicos {sev_total}")
    run.log = "\n".join(log)
    db.commit()


def _execute(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(TestRun, run_id)
        if run.kind == "security":
            _execute_security(db, run)
            return
        env = db.get(Environment, run.environment_id) if run.environment_id else None
        suite = db.get(TestSuite, run.suite_id) if run.suite_id else None
        if env is None or suite is None:
            run.status, run.log = "error", "Entorno o suite inexistente"
            run.finished_at = utcnow()
            db.commit()
            return
        timeout = min(int(run.config.get("timeout_s", 900)), get_settings().max_run_timeout_s)
        ctx = RunContext(
            run_id=run.id, policy=EnvPolicy.from_env(env), base_url=env.base_url, api_url=env.api_url,
            credentials_env_prefix=env.credentials_env_prefix, deadline=time.monotonic() + timeout,
            cancel_event=_cancel.setdefault(run_id, threading.Event()),
        )
        run.status, run.started_at = "running", utcnow()
        db.commit()
        ctx.log(f"Inicio: suite={suite.name} entorno={env.name} ({env.kind})")
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "blocked": 0}
        final = None
        verified_features: set[str] = set()
        for case in [c for c in suite.cases if c.enabled]:
            try:
                ctx.checkpoint()
                db.refresh(run)
                if run.cancel_requested:
                    ctx.cancel_event.set()
                    ctx.checkpoint()
                ctx.log(f"Caso: {case.name}")
                slug = re.sub(r"[^a-z0-9]+", "_", case.name.lower())[:40]
                if suite.kind == "api":
                    res = run_api_case(case.definition, ctx)
                elif suite.kind == "web":
                    res = run_web_case(case.definition, ctx, slug)
                elif suite.kind == "load":
                    res = run_load_case(case.definition, ctx)
                elif suite.kind == "android":
                    res = run_android_case(case.definition, ctx, slug)
                else:
                    res = {"status": "error", "error": f"Tipo de suite no soportado: {suite.kind}"}
            except Cancelled as exc:
                final = "cancelled"
                ctx.log(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                res = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "details": {"trace": traceback.format_exc()[-1500:]}}
            st = res.get("status", "error")
            counts[st] = counts.get(st, 0) + 1
            tr = TestResult(
                run_id=run.id, case_id=case.id, case_name=case.name, status=st,
                duration_ms=round(res.get("duration_ms", 0), 1), error=res.get("error", "")[:4000],
                severity=case.severity if st in ("failed", "error") else "info",
                recommendation=RECOMMENDATIONS.get(st, ""), details=redact(res.get("details", {})),
            )
            tr.artifacts = [TestArtifact(kind=a["kind"], path=a["path"]) for a in res.get("artifacts", [])]
            db.add(tr)
            for m in res.get("metrics", []):
                db.add(PerformanceMetric(run_id=run.id, **m))
            if st == "passed":
                verified_features.update(case.feature_ids or [])
            ctx.log(f" -> {st} {res.get('error', '')[:200]}")
            db.commit()

        if verified_features:
            for f in db.scalars(select(DiscoveredFeature).where(
                    DiscoveredFeature.project_id == run.project_id, DiscoveredFeature.stable_id.in_(verified_features))):
                f.evidence_level = "verified"
        total = sum(counts.values())
        run.summary = {**counts, "total": total}
        run.status = final or ("failed" if counts["failed"] or counts["error"] else
                               "blocked" if counts["blocked"] and not counts["passed"] else "passed")
        run.finished_at = utcnow()
        ctx.log(f"Fin: {run.status} {counts}")
        run.log = "\n".join(ctx.log_lines)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        run = db.get(TestRun, run_id)
        if run:
            run.status, run.finished_at = "error", utcnow()
            run.log = (run.log or "") + "\n" + traceback.format_exc()[-3000:]
            db.commit()
    finally:
        db.close()
        _cancel.pop(run_id, None)
