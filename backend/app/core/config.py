"""Configuración central. Todos los secretos provienen de variables de entorno."""
from __future__ import annotations

import secrets
import warnings
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]  # raíz del repo QA Control Center


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_prefix="QA_", extra="ignore")

    env: str = "development"
    database_url: str = f"sqlite:///{(BASE_DIR / 'qa_control_center.db').as_posix()}"
    secret_key: str = ""
    token_ttl_minutes: int = 480
    cors_origins: str = "http://localhost:5173"

    # Rutas locales que el descubrimiento puede leer (separadas por ';'). Vacío = ninguna.
    allowed_repo_roots: str = ""
    artifacts_dir: str = str(BASE_DIR / "artifacts")
    reports_dir: str = str(BASE_DIR / "reports")

    # Límites globales duros (no superables por configuración de entorno)
    max_virtual_users: int = 1000
    max_load_duration_s: int = 1800
    max_run_timeout_s: int = 3600
    worker_threads: int = 4
    kill_switch: bool = False

    # Automatización
    scheduler_enabled: bool = True
    notify_webhook_url: str = ""  # JSON {"text","content"} (Slack/Discord); solo https
    deploy_hook_token: str = ""  # vacío = hook de deploy deshabilitado

    # Carpeta con el frontend compilado (si existe, la API lo sirve en "/"). Vacío = no servir.
    frontend_dist: str = ""
    chromium_no_sandbox: bool = False  # solo dentro del contenedor (ya aislado); ver Dockerfile

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Railway entrega mysql://...; SQLAlchemy necesita el driver explícito.
        if v.startswith("mysql://"):
            v = "mysql+pymysql://" + v[len("mysql://"):]
        # Railway/Heroku entregan postgres:// o postgresql://; se usa el driver psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                v = "postgresql+psycopg://" + v[len(prefix):]
        if v.startswith("mysql+pymysql://") and "charset=" not in v:
            v += ("&" if "?" in v else "?") + "charset=utf8mb4"
        return v

    @property
    def repo_roots(self) -> list[Path]:
        return [Path(p).resolve() for p in self.allowed_repo_roots.split(";") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        if s.env == "production":
            raise RuntimeError("QA_SECRET_KEY es obligatorio en producción")
        warnings.warn("QA_SECRET_KEY no definido: se usa una clave efímera (tokens se invalidan al reiniciar)")
        s.secret_key = secrets.token_urlsafe(48)
    return s
