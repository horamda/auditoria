"""Ejecutor de escenarios web con Playwright.

Definición:
{
  "start_path": "/login",
  "allow_sensitive": false,
  "steps": [
    {"action": "fill", "selector": "#email", "value": "${USER}"},
    {"action": "click", "selector": "button[type=submit]", "label": "Ingresar"},
    {"action": "expect_text", "text": "Bienvenido"},
    {"action": "expect_url", "contains": "/dashboard"},
    {"action": "goto", "path": "/reportes"},
    {"action": "screenshot", "name": "reportes"}
  ],
  "cleanup": [ ...pasos de limpieza... ]
}
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.guard import SAFE_METHODS, PolicyViolation, check_sensitive_action, check_url
from app.workers.base import Cancelled, RunContext, resolve_secret_refs


def run_web_case(definition: dict, ctx: RunContext, case_slug: str) -> dict:
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "skipped", "error": "Playwright no está instalado en el worker"}
    if not ctx.base_url:
        return {"status": "error", "error": "El entorno no tiene base_url (panel web) configurada"}

    art_dir = Path(get_settings().artifacts_dir) / f"run_{ctx.run_id}"
    art_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []
    console_errors: list[str] = []
    blocked: list[str] = []
    timings: list[dict] = []
    allow_sensitive = bool(definition.get("allow_sensitive"))

    def shot(page, name: str) -> None:
        fname = f"{case_slug}_{name}_{int(time.time() * 1000)}.png"
        page.screenshot(path=str(art_dir / fname), full_page=True)
        artifacts.append({"kind": "screenshot", "path": f"run_{ctx.run_id}/{fname}"})

    def route_guard(route) -> None:  # noqa: ANN001
        req = route.request
        try:
            check_url(req.url, ctx.policy) if urlsplit(req.url).scheme in ("http", "https") else None
            if ctx.policy.is_production and req.method.upper() not in SAFE_METHODS:
                raise PolicyViolation(f"Producción: {req.method} bloqueado")
            if not ctx.policy.writes_allowed and req.method.upper() not in SAFE_METHODS and not definition.get("approved_write"):
                raise PolicyViolation(f"Escritura {req.method} no autorizada en este entorno")
        except PolicyViolation as exc:
            blocked.append(f"{req.method} {req.url} -> {exc}")
            route.abort("blockedbyclient")
            return
        route.continue_()

    t0 = time.perf_counter()
    status, error = "passed", ""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=definition.get("viewport", {"width": 1366, "height": 800}))
        context.route("**/*", route_guard)
        page = context.new_page()
        page.set_default_timeout(definition.get("step_timeout_ms", 10000))
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: console_errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        def url_for(path: str) -> str:
            return ctx.base_url.rstrip("/") + "/" + path.lstrip("/")

        def do(step: dict) -> None:
            ctx.checkpoint()
            act = step["action"]
            s0 = time.perf_counter()
            if act == "goto":
                target = url_for(step.get("path", "/")) if "path" in step else step["url"]
                check_url(target, ctx.policy)
                page.goto(target)
            elif act == "fill":
                page.fill(step["selector"], resolve_secret_refs(step.get("value", ""), ctx))
            elif act == "click":
                loc = page.locator(step["selector"]).first
                label = step.get("label") or (loc.inner_text(timeout=3000) if loc.count() else "")
                check_sensitive_action(f"{label} {step['selector']}", ctx.policy, allow_sensitive)
                loc.click()
            elif act == "select":
                page.select_option(step["selector"], step["value"])
            elif act == "press":
                page.keyboard.press(step["key"])
            elif act == "wait_for":
                page.wait_for_selector(step["selector"])
            elif act == "expect_text":
                page.get_by_text(step["text"]).first.wait_for()
            elif act == "expect_visible":
                page.locator(step["selector"]).first.wait_for(state="visible")
            elif act == "expect_url":
                page.wait_for_url(f"**{step['contains']}**")
            elif act == "screenshot":
                shot(page, step.get("name", "paso"))
            else:
                raise ValueError(f"Acción desconocida: {act}")
            timings.append({"action": act, "ms": round((time.perf_counter() - s0) * 1000, 1)})

        try:
            if definition.get("start_path") is not None:
                do({"action": "goto", "path": definition["start_path"]})
            for step in definition.get("steps", []):
                do(step)
            shot(page, "final")
        except Cancelled:
            raise
        except PolicyViolation as exc:
            status, error = "blocked", str(exc)
        except (PWError, ValueError, KeyError) as exc:
            status, error = "failed", f"{type(exc).__name__}: {str(exc).splitlines()[0][:400]}"
            try:
                shot(page, "error")
            except PWError:
                pass
        finally:
            for step in definition.get("cleanup", []):
                try:
                    do(step)
                except Exception as exc:  # noqa: BLE001 - la limpieza no debe ocultar el resultado
                    console_errors.append(f"cleanup: {exc}")
            context.close()
            browser.close()

    if status == "passed" and definition.get("fail_on_console_error") and console_errors:
        status, error = "failed", f"{len(console_errors)} errores de consola/JS"
    return {
        "status": status, "error": error, "duration_ms": (time.perf_counter() - t0) * 1000,
        "details": {"step_timings": timings, "console_errors": console_errors[:50], "blocked_requests": blocked[:50]},
        "artifacts": artifacts,
    }
