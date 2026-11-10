"""Utilidades compartidas por los workers: contexto de ejecución, credenciales y HTTP seguro."""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from app.core.guard import EnvPolicy, PolicyViolation, check_method, check_url, kill_switch_active


class Cancelled(Exception):
    pass


@dataclass
class RunContext:
    run_id: int
    policy: EnvPolicy
    base_url: str | None
    api_url: str | None
    credentials_env_prefix: str
    deadline: float
    cancel_event: threading.Event = field(default_factory=threading.Event)
    approved_write: bool = False
    log_lines: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def checkpoint(self) -> None:
        """Punto de control: cancelación, timeout y kill switch."""
        if self.cancel_event.is_set():
            raise Cancelled("Ejecución cancelada por el usuario")
        if time.monotonic() > self.deadline:
            raise Cancelled("Timeout global de la ejecución alcanzado")
        if kill_switch_active():
            raise Cancelled("Kill switch global activo")


_VAR = re.compile(r"\$\{(\w+)\}")


def resolve_secret_refs(value, ctx: RunContext):
    """Sustituye ${NOMBRE} por la variable de entorno <prefix><NOMBRE>. Los valores nunca se registran."""
    if isinstance(value, str):
        def rep(m: re.Match) -> str:
            var = f"{ctx.credentials_env_prefix}{m.group(1)}"
            if var not in os.environ:
                raise PolicyViolation(f"Credencial no configurada: variable de entorno {var}")
            return os.environ[var]
        return _VAR.sub(rep, value)
    if isinstance(value, dict):
        return {k: resolve_secret_refs(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secret_refs(v, ctx) for v in value]
    return value


def safe_request(client: httpx.Client, ctx: RunContext, method: str, url: str, max_redirects: int = 5, **kw) -> httpx.Response:
    """Petición con validación de política en el destino inicial y en CADA redirección."""
    check_method(method, ctx.policy, ctx.approved_write)
    for _ in range(max_redirects + 1):
        ctx.checkpoint()
        check_url(url, ctx.policy)
        resp = client.request(method, url, follow_redirects=False, **kw)
        if resp.is_redirect and "location" in resp.headers:
            url = urljoin(url, resp.headers["location"])
            if resp.status_code in (301, 302, 303):
                method, kw = "GET", {k: v for k, v in kw.items() if k not in ("json", "data", "content")}
            continue
        return resp
    raise PolicyViolation("Demasiadas redirecciones")
