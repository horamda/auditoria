from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user, require
from app.api.schemas import RunDetail, RunIn, RunOut
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.guard import kill_switch_active
from app.models import utcnow
from app.models import (
    AuditLog, DiscoveredFeature, Environment, PerformanceMetric, Project, FindingTriage, SecurityFinding, SystemFlag, TestArtifact, TestResult,
    TestRun, TestSuite, User,
)
from app.services import executor
from app.services.reports import compare_runs, render_run_report

router = APIRouter(prefix="/api", tags=["ejecuciones"])
FINAL = {"passed", "failed", "error", "cancelled", "blocked"}


@router.post("/projects/{pid}/runs", response_model=RunOut, status_code=202)
def start_run(pid: int, data: RunIn, db: Session = Depends(get_db), user: User = Depends(require("admin", "qa"))):
    suite, env = db.get(TestSuite, data.suite_id), db.get(Environment, data.environment_id)
    if not suite or suite.project_id != pid or not env or env.project_id != pid:
        raise HTTPException(404, "Suite o entorno inexistente para el proyecto")
    if kill_switch_active():
        raise HTTPException(423, "Kill switch activo: ejecuciones suspendidas")
    if suite.kind == "load" and env.kind == "production":
        raise HTTPException(403, "Pruebas de carga sobre producción bloqueadas por política")
    run = TestRun(project_id=pid, suite_id=suite.id, environment_id=env.id, kind=suite.kind, triggered_by=user.email,
                  version_label=data.version_label, config={"timeout_s": data.timeout_s, "environment_kind": env.kind})
    db.add(run)
    db.flush()
    audit(db, user, "run_start", f"run:{run.id}", suite=suite.name, environment=env.name, env_kind=env.kind)
    db.commit()
    executor.submit_run(run.id)
    return run


class SecurityScanIn(BaseModel):
    tools: list[Literal["semgrep", "osv-scanner", "pip-audit"]] = ["semgrep", "osv-scanner", "pip-audit"]
    version_label: str = ""
    timeout_s: int = Field(1800, ge=60, le=3600)


@router.post("/projects/{pid}/security-scans", response_model=RunOut, status_code=202)
def start_security_scan(pid: int, data: SecurityScanIn, db: Session = Depends(get_db), user: User = Depends(require("admin", "qa"))):
    if not db.get(Project, pid):
        raise HTTPException(404, "Proyecto inexistente")
    if kill_switch_active():
        raise HTTPException(423, "Kill switch activo: ejecuciones suspendidas")
    run = TestRun(project_id=pid, kind="security", triggered_by=user.email, version_label=data.version_label,
                  config={"tools": data.tools, "timeout_s": data.timeout_s, "environment_kind": "código (solo lectura)"})
    db.add(run)
    db.flush()
    audit(db, user, "security_scan_start", f"run:{run.id}", tools=data.tools)
    db.commit()
    executor.submit_run(run.id)
    return run


@router.get("/projects/{pid}/security-findings")
def security_findings(pid: int, run_id: int | None = None, severity: str | None = None,
                      db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Hallazgos de un escaneo (por defecto el último finalizado), con estado nuevo/existente y resueltos."""
    done = select(TestRun).where(TestRun.project_id == pid, TestRun.kind == "security",
                                 TestRun.status.in_(["passed", "failed", "error"])).order_by(TestRun.id.desc())
    runs_ = list(db.scalars(done.limit(50)))
    current = next((r for r in runs_ if r.id == run_id), None) if run_id else (runs_[0] if runs_ else None)
    if not current:
        return {"run": None, "previous_run": None, "findings": [], "resolved": []}
    prev = next((r for r in runs_ if r.id < current.id), None)
    rows = list(db.scalars(select(SecurityFinding).where(SecurityFinding.run_id == current.id)))
    prev_rows = list(db.scalars(select(SecurityFinding).where(SecurityFinding.run_id == prev.id))) if prev else []
    prev_fp = {f.fingerprint for f in prev_rows}
    cur_fp = {f.fingerprint for f in rows}
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    triage = {t.fingerprint: t for t in db.scalars(select(FindingTriage).where(FindingTriage.project_id == pid))}
    out = [{c: getattr(f, c) for c in ("id", "tool", "category", "rule_id", "severity", "title", "message", "file", "line",
                                        "package", "installed_version", "fixed_version", "cwe", "details", "fingerprint")}
           | {"status": "existente" if f.fingerprint in prev_fp else ("nuevo" if prev else "primer análisis"),
              "triage": ({"status": triage[f.fingerprint].status, "note": triage[f.fingerprint].note,
                          "by": triage[f.fingerprint].decided_by} if f.fingerprint in triage else None)}
           for f in rows if not severity or f.severity == severity]
    out.sort(key=lambda f: (order.get(f["severity"], 9), f["category"], f["file"]))
    resolved = [{"title": f.title, "severity": f.severity, "file": f.file} for f in prev_rows if f.fingerprint not in cur_fp]
    return {"run": {"id": current.id, "status": current.status, "summary": current.summary, "at": current.created_at.isoformat()},
            "previous_run": prev.id if prev else None, "findings": out, "resolved": resolved}


class TriageIn(BaseModel):
    fingerprint: str = Field(min_length=8, max_length=40)
    status: Literal["false_positive", "accepted_risk", "in_progress", "open"]
    note: str = Field("", max_length=2000)


@router.put("/projects/{pid}/security-triage")
def set_triage(pid: int, data: TriageIn, db: Session = Depends(get_db), user: User = Depends(require("admin", "qa"))):
    """Marca un hallazgo (por huella). 'open' elimina la decisión. Persiste entre escaneos."""
    if not db.get(Project, pid):
        raise HTTPException(404, "Proyecto inexistente")
    if not db.scalar(select(SecurityFinding.id).where(SecurityFinding.project_id == pid,
                                                      SecurityFinding.fingerprint == data.fingerprint).limit(1)):
        raise HTTPException(404, "Hallazgo inexistente en este proyecto")
    row = db.scalar(select(FindingTriage).where(FindingTriage.project_id == pid, FindingTriage.fingerprint == data.fingerprint))
    if data.status == "open":
        if row:
            db.delete(row)
    else:
        if data.status in ("false_positive", "accepted_risk") and len(data.note.strip()) < 5:
            raise HTTPException(422, "Indicá el motivo (mín. 5 caracteres) para descartar o aceptar un hallazgo")
        if row:
            row.status, row.note, row.decided_by, row.decided_at = data.status, data.note, user.email, utcnow()
        else:
            db.add(FindingTriage(project_id=pid, fingerprint=data.fingerprint, status=data.status, note=data.note, decided_by=user.email))
    audit(db, user, "finding_triage", f"project:{pid}", fingerprint=data.fingerprint, status=data.status)
    db.commit()
    return {"fingerprint": data.fingerprint, "status": data.status}


@router.get("/runs", response_model=list[RunOut])
def list_runs(project_id: int | None = None, limit: int = 50, db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(TestRun).order_by(TestRun.id.desc()).limit(min(limit, 500))
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)
    return list(db.scalars(stmt))


def _run(db: Session, rid: int) -> TestRun:
    r = db.get(TestRun, rid)
    if not r:
        raise HTTPException(404, "Ejecución inexistente")
    return r


@router.get("/runs/{rid}", response_model=RunDetail)
def get_run(rid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _run(db, rid)


@router.get("/runs/{rid}/metrics")
def run_metrics(rid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{k: getattr(m, k) for k in ("name", "requests", "failures", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "rps", "failures_per_s")}
            for m in db.scalars(select(PerformanceMetric).where(PerformanceMetric.run_id == rid))]


@router.post("/runs/{rid}/cancel")
def cancel(rid: int, db: Session = Depends(get_db), user: User = Depends(require("admin", "qa"))):
    r = _run(db, rid)
    if r.status in FINAL:
        return {"status": r.status}
    r.cancel_requested = True
    audit(db, user, "run_cancel", f"run:{rid}")
    db.commit()
    executor.cancel_run(rid)
    return {"status": "cancel_requested"}


@router.get("/runs/{rid}/events")
async def run_events(rid: int, _: User = Depends(current_user)):
    """SSE con el progreso de la ejecución."""
    async def gen():
        last = None
        for _ in range(3600):
            with SessionLocal() as db:
                r = db.get(TestRun, rid)
                if not r:
                    yield "event: error\ndata: {}\n\n"
                    return
                done = db.scalar(select(func.count()).select_from(TestResult).where(TestResult.run_id == rid))
                payload = {"status": r.status, "done": done, "summary": r.summary}
            if payload != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload
            if payload["status"] in FINAL:
                return
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runs/{rid}/report.html", response_class=HTMLResponse)
def report(rid: int, download: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)):
    html = render_run_report(db, _run(db, rid))
    headers = {"Content-Disposition": f'attachment; filename="informe_run_{rid}.html"'} if download else {}
    return HTMLResponse(html, headers=headers)


@router.get("/artifacts/{aid}")
def artifact(aid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    a = db.get(TestArtifact, aid)
    if not a:
        raise HTTPException(404)
    root = Path(get_settings().artifacts_dir).resolve()
    p = (root / a.path).resolve()
    if root not in p.parents or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@router.get("/runs/compare/{base_id}/{head_id}")
def compare(base_id: int, head_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    _run(db, base_id), _run(db, head_id)
    return compare_runs(db, base_id, head_id)


# ---------- Dashboard ----------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(current_user)):
    by_status = dict(db.execute(select(TestResult.status, func.count()).group_by(TestResult.status)).all())
    critical = db.scalar(select(func.count()).select_from(TestResult).where(
        TestResult.status.in_(["failed", "error"]), TestResult.severity.in_(["high", "critical"])))
    last = db.scalar(select(TestRun).order_by(TestRun.id.desc()).limit(1))
    cov = dict(db.execute(select(DiscoveredFeature.evidence_level, func.count())
                          .where(DiscoveredFeature.present.is_(True)).group_by(DiscoveredFeature.evidence_level)).all())
    recent = list(db.scalars(select(TestRun).order_by(TestRun.id.desc()).limit(30)))
    p95 = db.execute(select(PerformanceMetric.run_id, PerformanceMetric.p95_ms, PerformanceMetric.rps)
                     .where(PerformanceMetric.name == "Aggregated").order_by(PerformanceMetric.run_id.desc()).limit(20)).all()
    return {
        "projects": db.scalar(select(func.count()).select_from(Project)),
        "runs": db.scalar(select(func.count()).select_from(TestRun)),
        "results": by_status,
        "critical_open": critical,
        "coverage": {"static": cov.get("static", 0), "observed": cov.get("observed", 0), "verified": cov.get("verified", 0)},
        "last_run": {"id": last.id, "status": last.status, "at": last.created_at.isoformat()} if last else None,
        "trend": [{"id": r.id, "status": r.status, "passed": r.summary.get("passed", 0),
                   "failed": r.summary.get("failed", 0) + r.summary.get("error", 0), "at": r.created_at.isoformat()} for r in reversed(recent)],
        "performance": [{"run_id": a, "p95_ms": b, "rps": c} for a, b, c in reversed(p95)],
        "kill_switch": kill_switch_active(),
    }


# ---------- Sistema ----------
@router.post("/system/kill-switch")
def set_kill_switch(active: bool, db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    flag = db.get(SystemFlag, "kill_switch") or SystemFlag(key="kill_switch", value="0")
    flag.value = "1" if active else "0"
    db.merge(flag)
    audit(db, user, "kill_switch", "system", active=active)
    db.commit()
    if active:
        for r in db.scalars(select(TestRun).where(TestRun.status.in_(["queued", "running"]))):
            r.cancel_requested = True
            executor.cancel_run(r.id)
        db.commit()
    return {"kill_switch": active}


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require("admin"))):
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "target": a.target, "details": a.details}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)))]
