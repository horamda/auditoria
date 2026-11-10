from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=190)
    full_name: str = ""
    password: str = Field(min_length=10)
    role: Literal["admin", "qa", "viewer"] = "viewer"


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    is_active: bool


ComponentKind = Literal["flutter_android", "android_native", "web_admin", "backend", "api", "mysql", "postgresql", "module"]


class ComponentIn(BaseModel):
    kind: ComponentKind
    name: str
    path: str | None = None
    framework: str = ""


class ComponentOut(ComponentIn, ORM):
    id: int


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    app_type: str = "mixed"
    repo_url: str | None = None
    local_path: str | None = None
    admin_url: str | None = None
    api_url: str | None = None
    framework: str = ""
    language: str = ""
    version: str = ""
    status: str = "activo"
    owner: str = ""
    components: list[ComponentIn] = []


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    app_type: str
    repo_url: str | None
    local_path: str | None
    admin_url: str | None
    api_url: str | None
    framework: str
    language: str
    version: str
    status: str
    owner: str
    created_at: datetime
    components: list[ComponentOut]


class EnvironmentIn(BaseModel):
    name: str
    kind: Literal["development", "staging", "production"]
    base_url: str | None = None
    api_url: str | None = None
    allowed_hosts: list[str] = []
    allow_private_network: bool = False
    allow_writes: bool = False
    allow_load: bool = False
    max_users: int = Field(50, ge=1, le=1000)
    max_rps: float = Field(20, gt=0, le=5000)
    max_duration_s: int = Field(300, ge=1, le=1800)
    credentials_env_prefix: str = Field("", pattern=r"^[A-Z0-9_]*$")

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        out = [h.strip().lower() for h in v if h.strip()]
        for h in out:
            if "/" in h or ":" in h or h in ("*", "*.*"):
                raise ValueError(f"Host inválido en allowlist: {h}")
        return out


class EnvironmentOut(EnvironmentIn, ORM):
    id: int
    project_id: int


class FeatureOut(ORM):
    id: int
    stable_id: str
    layer: str
    kind: str
    name: str
    location: str
    details: dict
    evidence_level: str
    present: bool


class CaseIn(BaseModel):
    name: str
    objective: str = ""
    preconditions: str = ""
    definition: dict
    feature_ids: list[str] = []
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    enabled: bool = True


class CaseOut(CaseIn, ORM):
    id: int


class SuiteIn(BaseModel):
    name: str
    kind: Literal["api", "web", "load", "android"]
    description: str = ""
    cases: list[CaseIn] = []


class SuiteOut(ORM):
    id: int
    project_id: int
    name: str
    kind: str
    description: str
    cases: list[CaseOut]


class RunIn(BaseModel):
    suite_id: int
    environment_id: int
    version_label: str = ""
    timeout_s: int = Field(900, ge=10, le=3600)


class ArtifactOut(ORM):
    id: int
    kind: str
    path: str


class ResultOut(ORM):
    id: int
    case_id: int | None
    case_name: str
    status: str
    duration_ms: float
    error: str
    severity: str
    recommendation: str
    details: dict
    artifacts: list[ArtifactOut]


class RunOut(ORM):
    id: int
    project_id: int
    suite_id: int | None
    environment_id: int | None
    kind: str
    status: str
    triggered_by: str
    version_label: str
    summary: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunOut):
    log: str
    config: dict
    results: list[ResultOut]
