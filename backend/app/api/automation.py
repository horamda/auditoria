"""Automatización: programaciones, hook de deploy y suite de humo autogenerada."""
from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user, require
from app.core.config import get_settings
from app.core.db import get_db
from app.core.guard import kill_switch_active
from app.models import (
    AuditLog, DiscoveredFeature, Environment, Project, Schedule, TestCase, TestRun, TestSuite, User, utcnow,
)
from app.services import executor

router = APIRouter(prefix="/api", tags=["automatizacion"])
WRITERS = ("admin", "qa")
HOOK_KINDS = ("api", "web")  # los hooks de deploy nunca lanzan carga, Android ni seguridad


# ---------------- programaciones ----------------
class ScheduleIn(BaseModel):
    suite_id: int
    environment_id: int
    interval_minutes: int = Field(60, ge=5, le=10080)
    name: str = ""
    enabled: bool = True


class ScheduleOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    suite_id: int
    environment_id: int
    name: str
    interval_minutes: int
    enabled: bool
    next_run_at: datetime
    last_run_id: int | None
    created_by: str


def _valid_target(db: Session, pid: int, suite_id: int, env_id: int) -> tuple[TestSuite, Environment]:
    suite, env = db.get(TestSuite, suite_id), db.get(Environment, env_id)
    if not suite or suite.project_id != pid or not env or env.project_id != pid:
        raise HTTPException(404, "Suite o entorno inexistente para el proyecto")
    if suite.kind in ("load", "android") and env.kind == "production":
        raise HTTPException(403, "Carga/Android automáticos sobre producción bloqueados por política")
    if suite.kind == "load":
        raise HTTPException(422, "Las pruebas de carga no se programan automáticamente; ejecutarlas manualmente")
    return suite, env


@router.get("/projects/{pid}/schedules", response_model=list[ScheduleOut])
def list_schedules(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(Schedule).where(Schedule.project_id == pid).order_by(Schedule.id)))


@router.post("/projects/{pid}/schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(pid: int, data: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    if not db.get(Project, pid):
        raise HTTPException(404, "Proyecto inexistente")
    suite, env = _valid_target(db, pid, data.suite_id, data.environment_id)
    s = Schedule(project_id=pid, suite_id=suite.id, environment_id=env.id, interval_minutes=data.interval_minutes,
                 name=data.name or f"{suite.name} @ {env.name}", enabled=data.enabled, created_by=user.email,
                 next_run_at=utcnow() + timedelta(minutes=1))
    db.add(s)
    db.flush()
    audit(db, user, "schedule_create", f"schedule:{s.id}", interval=data.interval_minutes)
    db.commit()
    return s


@router.patch("/projects/{pid}/schedules/{sid}", response_model=ScheduleOut)
def update_schedule(pid: int, sid: int, data: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    s = db.get(Schedule, sid)
    if not s or s.project_id != pid:
        raise HTTPException(404, "Programación inexistente")
    suite, env = _valid_target(db, pid, data.suite_id, data.environment_id)
    s.suite_id, s.environment_id, s.interval_minutes, s.enabled = suite.id, env.id, data.interval_minutes, data.enabled
    if data.name:
        s.name = data.name
    audit(db, user, "schedule_update", f"schedule:{sid}", enabled=data.enabled)
    db.commit()
    return s


@router.delete("/projects/{pid}/schedules/{sid}", status_code=204)
def delete_schedule(pid: int, sid: int, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    s = db.get(Schedule, sid)
    if not s or s.project_id != pid:
        raise HTTPException(404, "Programación inexistente")
    audit(db, user, "schedule_delete", f"schedule:{sid}")
    db.delete(s)
    db.commit()


# ---------------- suite de humo autogenerada ----------------
PARAM = re.compile(r"[<{:]")
NO_SERVER_ERROR = list(range(200, 500))


class AutoSmokeIn(BaseModel):
    max_cases: int = Field(50, ge=1, le=200)


@router.post("/projects/{pid}/suites/auto-smoke", status_code=201)
def auto_smoke(pid: int, data: AutoSmokeIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    """Crea/actualiza la suite 'Humo (autogenerada)' con los GET descubiertos sin autenticación detectada ni parámetros.

    El criterio es deliberadamente laxo: pasa si el endpoint responde sin error de servidor (< 500).
    Es una prueba de disponibilidad, no de corrección funcional.
    """
    if not db.get(Project, pid):
        raise HTTPException(404, "Proyecto inexistente")
    feats = db.scalars(select(DiscoveredFeature).where(
        DiscoveredFeature.project_id == pid, DiscoveredFeature.kind == "endpoint", DiscoveredFeature.present.is_(True)))
    cases, seen = [], set()
    for f in sorted(feats, key=lambda x: x.name):
        d = f.details or {}
        path = d.get("path", "")
        if d.get("method") != "GET" or d.get("auth_detected") or not path.startswith("/") or PARAM.search(path) or path in seen:
            continue
        seen.add(path)
        cases.append({"name": f"GET {path}", "objective": "Disponibilidad: responde sin error de servidor",
                      "definition": {"method": "GET", "path": path, "expect_status": NO_SERVER_ERROR, "max_ms": 5000},
                      "feature_ids": [f.stable_id], "severity": "medium"})
        if len(cases) >= data.max_cases:
            break
    if not cases:
        raise HTTPException(422, "No hay endpoints GET descubiertos sin autenticación y sin parámetros. Ejecutar el descubrimiento primero.")
    suite = db.scalar(select(TestSuite).where(TestSuite.project_id == pid, TestSuite.name == "Humo (autogenerada)"))
    if suite:
        for c in list(suite.cases):
            db.delete(c)
    else:
        suite = TestSuite(project_id=pid, name="Humo (autogenerada)", kind="api",
                          description="Generada desde el descubrimiento. Solo comprueba disponibilidad (sin 5xx).")
        db.add(suite)
        db.flush()
    for c in cases:
        db.add(TestCase(suite_id=suite.id, **c))
    audit(db, user, "suite_autosmoke", f"suite:{suite.id}", cases=len(cases))
    db.commit()
    return {"suite_id": suite.id, "cases": len(cases)}


# ---------------- hook de deploy (CI / Railway) ----------------
def _check_token(token: str | None) -> None:
    expected = get_settings().deploy_hook_token
    if not expected:
        raise HTTPException(404, "Hook de deploy deshabilitado")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Token inválido")


class DeployHookIn(BaseModel):
    project: str
    environment: str
    version_label: str = Field("", max_length=60)


@router.post("/hooks/deploy", status_code=202)
def deploy_hook(data: DeployHookIn, x_qa_token: str | None = Header(None), db: Session = Depends(get_db)):
    """Lanza las suites api/web del entorno indicado tras un deploy. Autenticado por token, no por usuario."""
    _check_token(x_qa_token)
    if kill_switch_active():
        raise HTTPException(423, "Kill switch activo")
    proj = db.scalar(select(Project).where(Project.name == data.project))
    env = db.scalar(select(Environment).where(Environment.project_id == proj.id, Environment.name == data.environment)) if proj else None
    if not proj or not env:
        raise HTTPException(404, "Proyecto o entorno inexistente")
    suites = list(db.scalars(select(TestSuite).where(TestSuite.project_id == proj.id, TestSuite.kind.in_(HOOK_KINDS)).limit(10)))
    runs = []
    for suite in suites:
        run = TestRun(project_id=proj.id, suite_id=suite.id, environment_id=env.id, kind=suite.kind,
                      triggered_by="hook:deploy", version_label=data.version_label,
                      config={"timeout_s": 900, "environment_kind": env.kind})
        db.add(run)
        db.flush()
        runs.append(run.id)
    db.add(AuditLog(actor="hook:deploy", action="hook_deploy", target=f"project:{proj.id}",
                    details={"environment": env.name, "runs": runs, "version": data.version_label}))
    db.commit()
    for rid in runs:
        executor.submit_run(rid)
    return {"runs": runs}


@router.get("/hooks/runs/{run_id}")
def hook_run_status(run_id: int, x_qa_token: str | None = Header(None), db: Session = Depends(get_db)):
    """Estado mínimo para que un CI espere el resultado (solo ejecuciones lanzadas por el hook)."""
    _check_token(x_qa_token)
    run = db.get(TestRun, run_id)
    if not run or run.triggered_by != "hook:deploy":
        raise HTTPException(404, "Ejecución inexistente")
    return {"id": run.id, "status": run.status, "summary": run.summary}
