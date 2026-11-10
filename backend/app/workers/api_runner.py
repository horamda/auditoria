"""Ejecutor de pruebas API.

Definición de un caso:
{
  "method": "GET", "path": "/api/health",
  "headers": {"Authorization": "Bearer ${TOKEN}"},   # ${X} -> env <prefix>X
  "json": {...}, "params": {...},
  "expect_status": 200 | [200, 201],
  "expect_json": {"status": "ok"},                    # subconjunto esperado
  "expect_body_contains": "texto",
  "max_ms": 1500,
  "approved_write": false
}
"""
from __future__ import annotations

import time

import httpx

from app.core.guard import PolicyViolation
from app.workers.base import Cancelled, RunContext, resolve_secret_refs, safe_request


def _subset(expected, actual, path="$") -> list[str]:
    errs = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: se esperaba objeto"]
        for k, v in expected.items():
            if k not in actual:
                errs.append(f"{path}.{k}: falta")
            else:
                errs += _subset(v, actual[k], f"{path}.{k}")
    elif expected != actual:
        errs.append(f"{path}: esperado {expected!r}, obtenido {actual!r}")
    return errs


def run_api_case(definition: dict, ctx: RunContext) -> dict:
    base = ctx.api_url or ctx.base_url
    if not base:
        return {"status": "error", "error": "El entorno no tiene api_url configurada"}
    url = base.rstrip("/") + "/" + definition.get("path", "/").lstrip("/")
    method = definition.get("method", "GET").upper()
    ctx.approved_write = bool(definition.get("approved_write")) and not ctx.policy.is_production
    t0 = time.perf_counter()
    try:
        headers = resolve_secret_refs(definition.get("headers", {}), ctx)
        body = resolve_secret_refs(definition.get("json"), ctx) if "json" in definition else None
        with httpx.Client(timeout=definition.get("timeout_s", 20)) as client:
            kw = {"headers": headers, "params": definition.get("params")}
            if body is not None:
                kw["json"] = body
            resp = safe_request(client, ctx, method, url, **kw)
    except PolicyViolation as exc:
        return {"status": "blocked", "error": str(exc), "duration_ms": 0}
    except Cancelled:
        raise
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"Error de conexión: {type(exc).__name__}: {exc}",
                "duration_ms": (time.perf_counter() - t0) * 1000}
    finally:
        ctx.approved_write = False
    ms = (time.perf_counter() - t0) * 1000
    errors: list[str] = []
    exp = definition.get("expect_status", 200)
    exp_list = exp if isinstance(exp, list) else [exp]
    if resp.status_code not in exp_list:
        errors.append(f"HTTP {resp.status_code}, esperado {exp_list}")
    if "expect_json" in definition:
        try:
            errors += _subset(definition["expect_json"], resp.json())
        except ValueError:
            errors.append("La respuesta no es JSON")
    if (needle := definition.get("expect_body_contains")) and needle not in resp.text:
        errors.append(f"El cuerpo no contiene {needle!r}")
    if (max_ms := definition.get("max_ms")) and ms > max_ms:
        errors.append(f"Latencia {ms:.0f} ms supera el máximo {max_ms} ms")
    return {
        "status": "failed" if errors else "passed",
        "error": "; ".join(errors),
        "duration_ms": ms,
        "details": {"method": method, "url": url, "http_status": resp.status_code,
                    "response_excerpt": resp.text[:500]},
    }
