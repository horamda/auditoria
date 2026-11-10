"""Política obligatoria de entornos y protección de destinos.

Toda petición saliente de un worker DEBE pasar por `check_url` y `check_method`.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Textos/selectores que nunca se accionan sin autorización explícita (y jamás en producción)
SENSITIVE_PATTERN = re.compile(
    r"(elimin|borr|delete|remove|destroy|liberar|liberaci[oó]n|pag(ar|o)|transfer|confirmar\s+pago|"
    r"anular|dar\s+de\s+baja|reset|purge|drop)",
    re.IGNORECASE,
)


class PolicyViolation(Exception):
    """Operación bloqueada por la política de seguridad."""


@dataclass
class EnvPolicy:
    kind: str  # development | staging | production
    allowed_hosts: list[str] = field(default_factory=list)
    allow_private_network: bool = False
    allow_writes: bool = False
    allow_load: bool = False
    max_users: int = 50
    max_rps: float = 20.0
    max_duration_s: int = 300

    @classmethod
    def from_env(cls, env) -> EnvPolicy:  # noqa: ANN001
        return cls(
            kind=env.kind,
            allowed_hosts=[h.lower().strip() for h in (env.allowed_hosts or []) if h.strip()],
            allow_private_network=env.allow_private_network,
            allow_writes=env.allow_writes,
            allow_load=env.allow_load,
            max_users=env.max_users,
            max_rps=env.max_rps,
            max_duration_s=env.max_duration_s,
        )

    @property
    def is_production(self) -> bool:
        return self.kind == "production"

    @property
    def writes_allowed(self) -> bool:
        # En producción las escrituras NUNCA se habilitan por configuración del entorno.
        return self.allow_writes and not self.is_production


def kill_switch_active() -> bool:
    if get_settings().kill_switch:
        return True
    from app.core.db import SessionLocal
    from app.models import SystemFlag

    with SessionLocal() as db:
        flag = db.get(SystemFlag, "kill_switch")
        return bool(flag and flag.value == "1")


def _host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pat in allowed:
        if pat.startswith("*."):
            if host.endswith(pat[1:]) and host != pat[2:]:
                return True
        elif host == pat:
            return True
    return False


def _is_internal(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None and _is_internal(ip.ipv4_mapped))
    )


def check_url(url: str, policy: EnvPolicy, resolve: bool = True) -> str:
    """Valida esquema, allowlist de hosts y bloqueo de redes internas (SSRF)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PolicyViolation(f"Esquema no permitido: {parts.scheme!r}")
    if parts.username or parts.password:
        raise PolicyViolation("No se permiten credenciales embebidas en la URL")
    host = parts.hostname
    if not host:
        raise PolicyViolation("URL sin host")
    if not policy.allowed_hosts:
        raise PolicyViolation("El entorno no tiene hosts autorizados (allowlist vacía)")
    if not _host_allowed(host, policy.allowed_hosts):
        raise PolicyViolation(f"Host fuera de la allowlist: {host}")
    if policy.allow_private_network and policy.is_production:
        raise PolicyViolation("Producción no puede habilitar red privada")
    if resolve and not policy.allow_private_network:
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise PolicyViolation(f"No se pudo resolver {host}: {exc}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
            if _is_internal(ip):
                raise PolicyViolation(f"{host} resuelve a dirección interna {ip} (bloqueado)")
    return url


def check_method(method: str, policy: EnvPolicy, approved_write: bool = False) -> None:
    m = method.upper()
    if m in SAFE_METHODS:
        return
    if policy.is_production:
        raise PolicyViolation(f"Producción: método {m} bloqueado (solo lectura)")
    if not (policy.writes_allowed or approved_write):
        raise PolicyViolation(f"El entorno no permite escrituras ({m})")


def check_sensitive_action(label: str, policy: EnvPolicy, allow_sensitive: bool) -> None:
    if not label or not SENSITIVE_PATTERN.search(label):
        return
    if policy.is_production:
        raise PolicyViolation(f"Acción sensible bloqueada en producción: {label!r}")
    if not allow_sensitive:
        raise PolicyViolation(f"Acción sensible requiere autorización explícita en el escenario: {label!r}")


def check_load(users: int, duration_s: int, rps: float | None, policy: EnvPolicy) -> None:
    s = get_settings()
    if policy.is_production:
        raise PolicyViolation("Pruebas de carga sobre producción bloqueadas por defecto")
    if not policy.allow_load:
        raise PolicyViolation("El entorno no tiene habilitadas las pruebas de carga")
    if users < 1 or users > min(policy.max_users, s.max_virtual_users):
        raise PolicyViolation(f"Usuarios {users} fuera del límite ({min(policy.max_users, s.max_virtual_users)})")
    if duration_s < 1 or duration_s > min(policy.max_duration_s, s.max_load_duration_s):
        raise PolicyViolation(f"Duración {duration_s}s fuera del límite")
    if rps is not None and rps > policy.max_rps:
        raise PolicyViolation(f"RPS objetivo {rps} supera el máximo del entorno ({policy.max_rps})")
