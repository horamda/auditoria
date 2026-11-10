"""Configuración central. Todos los secretos provienen de variables de entorno."""
from __future__ import annotations

import secrets
import warnings
from functools import lru_cache
from pathlib import Path

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
