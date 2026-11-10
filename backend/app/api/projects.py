from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user, require
from app.api.schemas import (
    EnvironmentIn, EnvironmentOut, FeatureOut, ProjectIn, ProjectOut, SuiteIn, SuiteOut, CaseIn, CaseOut,
)
from app.core.db import get_db
from app.core.guard import PolicyViolation
from app.models import DiscoveredFeature, Environment, Project, ProjectComponent, TestCase, TestSuite, User
from app.services.discovery_service import run_discovery
from app.workers.load_runner import LOAD_PROFILES

router = APIRouter(prefix="/api/projects", tags=["proyectos"])
WRITERS = ("admin", "qa")


def _project(db: Session, pid: int) -> Project:
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "Proyecto inexistente")
    return p


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(Project).order_by(Project.name)))


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(data: ProjectIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    if db.scalar(select(Project).where(Project.name == data.name)):
        raise HTTPException(409, "Ya existe un proyecto con ese nombre")
    p = Project(**data.model_dump(exclude={"components"}))
    p.components = [ProjectComponent(**c.model_dump()) for c in data.components]
    db.add(p)
    db.flush()
    audit(db, user, "project_create", f"project:{p.id}", name=p.name)
    db.commit()
    return p


@router.get("/{pid}", response_model=ProjectOut)
def get_project(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _project(db, pid)


@router.put("/{pid}", response_model=ProjectOut)
def update_project(pid: int, data: ProjectIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    p = _project(db, pid)
    for k, v in data.model_dump(exclude={"components"}).items():
        setattr(p, k, v)
    p.components = [ProjectComponent(**c.model_dump()) for c in data.components]
    audit(db, user, "project_update", f"project:{pid}")
    db.commit()
    return p


@router.delete("/{pid}", status_code=204)
def delete_project(pid: int, db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    p = _project(db, pid)
    audit(db, user, "project_delete", f"project:{pid}", name=p.name)
    db.delete(p)
    db.commit()


# ---------- Entornos ----------
@router.get("/{pid}/environments", response_model=list[EnvironmentOut])
def list_envs(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(Environment).where(Environment.project_id == pid).order_by(Environment.id)))


def _apply_env_policy(env: Environment, user: User) -> None:
    if env.kind == "production":
        # Producción: nunca escrituras, carga ni red privada por configuración.
        env.allow_writes = False
        env.allow_load = False
        env.allow_private_network = False
    if env.allow_private_network and user.role.name != "admin":
        raise HTTPException(403, "Solo un administrador puede habilitar red privada (localhost/LAN)")


@router.post("/{pid}/environments", response_model=EnvironmentOut, status_code=201)
def create_env(pid: int, data: EnvironmentIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    _project(db, pid)
    env = Environment(project_id=pid, **data.model_dump())
    _apply_env_policy(env, user)
    db.add(env)
    db.flush()
    audit(db, user, "environment_create", f"environment:{env.id}", **data.model_dump())
    db.commit()
    return env


@router.put("/{pid}/environments/{eid}", response_model=EnvironmentOut)
def update_env(pid: int, eid: int, data: EnvironmentIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    env = db.get(Environment, eid)
    if not env or env.project_id != pid:
        raise HTTPException(404, "Entorno inexistente")
    for k, v in data.model_dump().items():
        setattr(env, k, v)
    _apply_env_policy(env, user)
    audit(db, user, "environment_update", f"environment:{eid}", **data.model_dump())
    db.commit()
    return env


# ---------- Descubrimiento ----------
@router.post("/{pid}/discover")
def discover(pid: int, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    p = _project(db, pid)
    try:
        summary = run_discovery(db, p)
    except PolicyViolation as exc:
        raise HTTPException(400, str(exc)) from None
    audit(db, user, "discovery", f"project:{pid}", total=summary["total"])
    db.commit()
    return summary


@router.get("/{pid}/features", response_model=list[FeatureOut])
def list_features(pid: int, layer: str | None = None, kind: str | None = None, q: str | None = None,
                  limit: int = 500, offset: int = 0, db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(DiscoveredFeature).where(DiscoveredFeature.project_id == pid)
    if layer:
        stmt = stmt.where(DiscoveredFeature.layer == layer)
    if kind:
        stmt = stmt.where(DiscoveredFeature.kind == kind)
    if q:
        stmt = stmt.where(DiscoveredFeature.name.contains(q))
    return list(db.scalars(stmt.order_by(DiscoveredFeature.layer, DiscoveredFeature.kind, DiscoveredFeature.name)
                           .limit(min(limit, 2000)).offset(offset)))


@router.get("/{pid}/coverage")
def coverage(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.execute(select(DiscoveredFeature.layer, DiscoveredFeature.evidence_level, func.count())
                      .where(DiscoveredFeature.project_id == pid, DiscoveredFeature.present.is_(True))
                      .group_by(DiscoveredFeature.layer, DiscoveredFeature.evidence_level)).all()
    out: dict[str, dict[str, int]] = {}
    for layer, level, n in rows:
        out.setdefault(layer, {"static": 0, "observed": 0, "verified": 0})[level] = n
    return out


# ---------- Suites y casos ----------
@router.get("/{pid}/suites", response_model=list[SuiteOut])
def list_suites(pid: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return list(db.scalars(select(TestSuite).where(TestSuite.project_id == pid).order_by(TestSuite.id)))


@router.post("/{pid}/suites", response_model=SuiteOut, status_code=201)
def create_suite(pid: int, data: SuiteIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    _project(db, pid)
    s = TestSuite(project_id=pid, name=data.name, kind=data.kind, description=data.description)
    s.cases = [TestCase(**c.model_dump()) for c in data.cases]
    db.add(s)
    db.flush()
    audit(db, user, "suite_create", f"suite:{s.id}")
    db.commit()
    return s


@router.post("/{pid}/suites/{sid}/cases", response_model=CaseOut, status_code=201)
def add_case(pid: int, sid: int, data: CaseIn, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    s = db.get(TestSuite, sid)
    if not s or s.project_id != pid:
        raise HTTPException(404, "Suite inexistente")
    c = TestCase(suite_id=sid, **data.model_dump())
    db.add(c)
    audit(db, user, "case_create", f"suite:{sid}", name=data.name)
    db.commit()
    return c


@router.delete("/{pid}/suites/{sid}", status_code=204)
def delete_suite(pid: int, sid: int, db: Session = Depends(get_db), user: User = Depends(require(*WRITERS))):
    s = db.get(TestSuite, sid)
    if not s or s.project_id != pid:
        raise HTTPException(404, "Suite inexistente")
    audit(db, user, "suite_delete", f"suite:{sid}")
    db.delete(s)
    db.commit()


@router.get("/meta/load-profiles", tags=["meta"])
def load_profiles(_: User = Depends(current_user)):
    return LOAD_PROFILES
