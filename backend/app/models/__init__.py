"""Modelo de datos de QA Control Center (base exclusiva de la plataforma)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # admin | qa | viewer
    description: Mapped[str] = mapped_column(String(255), default="")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    role: Mapped[Role] = relationship(lazy="joined")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    app_type: Mapped[str] = mapped_column(String(60), default="mixed")
    repo_url: Mapped[str | None] = mapped_column(String(500))
    local_path: Mapped[str | None] = mapped_column(String(500))
    admin_url: Mapped[str | None] = mapped_column(String(500))
    api_url: Mapped[str | None] = mapped_column(String(500))
    framework: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="activo")
    owner: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    components: Mapped[list[ProjectComponent]] = relationship(cascade="all, delete-orphan", back_populates="project")
    environments: Mapped[list[Environment]] = relationship(cascade="all, delete-orphan", back_populates="project")


class ProjectComponent(Base):
    __tablename__ = "project_components"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # flutter_android | web_admin | backend | api | mysql
    name: Mapped[str] = mapped_column(String(120))
    path: Mapped[str | None] = mapped_column(String(500))  # subcarpeta dentro de local_path
    framework: Mapped[str] = mapped_column(String(60), default="")
    project: Mapped[Project] = relationship(back_populates="components")


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))  # development | staging | production
    base_url: Mapped[str | None] = mapped_column(String(500))  # panel web
    api_url: Mapped[str | None] = mapped_column(String(500))
    allowed_hosts: Mapped[list] = mapped_column(JSON, default=list)
    allow_private_network: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_writes: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_load: Mapped[bool] = mapped_column(Boolean, default=False)
    max_users: Mapped[int] = mapped_column(Integer, default=50)
    max_rps: Mapped[float] = mapped_column(Float, default=20.0)
    max_duration_s: Mapped[int] = mapped_column(Integer, default=300)
    # Prefijo de variables de entorno con credenciales de prueba (nunca se guardan valores)
    credentials_env_prefix: Mapped[str] = mapped_column(String(80), default="")
    project: Mapped[Project] = relationship(back_populates="environments")


class DiscoveredFeature(Base):
    __tablename__ = "discovered_features"
    __table_args__ = (UniqueConstraint("project_id", "stable_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stable_id: Mapped[str] = mapped_column(String(40))
    layer: Mapped[str] = mapped_column(String(20))  # flutter | web | backend | database
    kind: Mapped[str] = mapped_column(String(40))  # endpoint | screen | route | http_call | table | permission ...
    name: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    # static -> observed -> verified (nunca se degrada automáticamente)
    evidence_level: Mapped[str] = mapped_column(String(20), default="static")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    present: Mapped[bool] = mapped_column(Boolean, default=True)


class TestSuite(Base):
    __tablename__ = "test_suites"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # api | web | load
    description: Mapped[str] = mapped_column(Text, default="")
    cases: Mapped[list[TestCase]] = relationship(cascade="all, delete-orphan", order_by="TestCase.id")


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text, default="")
    preconditions: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_ids: Mapped[list] = mapped_column(JSON, default=list)  # stable_ids cubiertos
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id", ondelete="SET NULL"))
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))  # api | web | load | discovery
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | passed | failed | error | cancelled | blocked
    triggered_by: Mapped[str] = mapped_column(String(190), default="")
    version_label: Mapped[str] = mapped_column(String(60), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    results: Mapped[list[TestResult]] = relationship(cascade="all, delete-orphan", order_by="TestResult.id")


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_cases.id", ondelete="SET NULL"))
    case_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))  # passed | failed | error | skipped | blocked
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list[TestArtifact]] = relationship(cascade="all, delete-orphan")


class TestArtifact(Base):
    __tablename__ = "test_artifacts"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("test_results.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # screenshot | log | csv
    path: Mapped[str] = mapped_column(String(500))  # relativo a artifacts_dir
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # endpoint o "Aggregated"
    requests: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    avg_ms: Mapped[float] = mapped_column(Float, default=0)
    p50_ms: Mapped[float] = mapped_column(Float, default=0)
    p95_ms: Mapped[float] = mapped_column(Float, default=0)
    p99_ms: Mapped[float] = mapped_column(Float, default=0)
    rps: Mapped[float] = mapped_column(Float, default=0)
    failures_per_s: Mapped[float] = mapped_column(Float, default=0)


class SecurityFinding(Base):
    __tablename__ = "security_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    tool: Mapped[str] = mapped_column(String(40))  # semgrep | osv-scanner | pip-audit (o varias, ver details.tools)
    category: Mapped[str] = mapped_column(String(20))  # codigo | dependencia
    rule_id: Mapped[str] = mapped_column(String(300), default="")
    severity: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text, default="")
    file: Mapped[str] = mapped_column(String(500), default="")
    line: Mapped[int | None] = mapped_column(Integer)
    package: Mapped[str] = mapped_column(String(200), default="")
    installed_version: Mapped[str] = mapped_column(String(60), default="")
    fixed_version: Mapped[str] = mapped_column(String(200), default="")
    cwe: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class FindingTriage(Base):
    """Decisión humana sobre un hallazgo; se asocia a la huella, por lo que persiste entre escaneos."""
    __tablename__ = "finding_triage"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # false_positive | accepted_risk | in_progress
    note: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(190), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(190))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemFlag(Base):
    """Banderas operativas persistentes (p. ej. kill switch global)."""
    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
