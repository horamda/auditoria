"""Avisos de resultado de ejecuciones automáticas (programadas o disparadas por deploy)."""
from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import AuditLog, Environment, Project, TestRun, TestSuite

AUTO_PREFIXES = ("schedule:", "hook:")
BAD = {"failed", "error", "blocked"}


def _message(db, run: TestRun, recovered: bool) -> str:
    proj = db.get(Project, run.project_id)
    suite = db.get(TestSuite, run.suite_id) if run.suite_id else None
    env = db.get(Environment, run.environment_id) if run.environment_id else None
    head = "RECUPERADO" if recovered else f"FALLO ({run.status})"
    s = run.summary or {}
    return (f"QA Control Center - {head}: {proj.name if proj else '?'} / {suite.name if suite else '?'} "
            f"en {env.name if env else '?'} (ejecución #{run.id}). "
            f"Aprobados {s.get('passed', 0)}, fallidos {s.get('failed', 0)}, "
            f"errores {s.get('error', 0)}, bloqueados {s.get('blocked', 0)}.")


def should_notify(db, run: TestRun) -> tuple[bool, bool]:
    """(notificar, es_recuperación). Notifica fallos y la primera ejecución sana tras un fallo."""
    if not (run.triggered_by or "").startswith(AUTO_PREFIXES):
        return False, False
    if run.status in BAD:
        return True, False
    if run.status == "passed":
        prev = db.scalar(select(TestRun).where(
            TestRun.suite_id == run.suite_id, TestRun.environment_id == run.environment_id,
            TestRun.id < run.id, TestRun.status.in_(["passed", "failed", "error", "blocked"]),
        ).order_by(TestRun.id.desc()).limit(1))
        if prev and prev.status in BAD:
            return True, True
    return False, False


def notify_run_finished(run_id: int) -> None:
    """Nunca propaga excepciones: un aviso fallido no debe afectar a la ejecución."""
    url = get_settings().notify_webhook_url
    db = SessionLocal()
    try:
        run = db.get(TestRun, run_id)
        if not run:
            return
        send, recovered = should_notify(db, run)
        if not send:
            return
        text = _message(db, run, recovered)
        if not url.startswith("https://"):
            db.add(AuditLog(actor="system", action="notify_skipped", target=f"run:{run_id}",
                            details={"reason": "QA_NOTIFY_WEBHOOK_URL no configurado (debe ser https)"}))
            db.commit()
            return
        try:
            r = httpx.post(url, json={"text": text, "content": text}, timeout=10, follow_redirects=False)
            ok, detail = r.status_code < 300, f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            ok, detail = False, type(exc).__name__
        db.add(AuditLog(actor="system", action="notify_sent" if ok else "notify_failed",
                        target=f"run:{run_id}", details={"result": detail}))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()
