"""Planificador de ejecuciones automáticas (hilo en proceso).

Cada tick busca programaciones vencidas y lanza la suite. Reglas:
- respeta el kill switch (no lanza nada y reprograma),
- no encola si la ejecución anterior de la misma programación sigue en curso,
- reclama cada programación con un UPDATE condicional (seguro con más de una réplica),
- aplica las mismas políticas que una ejecución manual (carga sobre producción bloqueada).
"""
from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.guard import kill_switch_active
from app.models import Environment, Schedule, TestRun, TestSuite, utcnow
from app.services import executor

TICK_S = 30
_stop = threading.Event()
_thread: threading.Thread | None = None


def tick() -> list[int]:
    """Lanza lo vencido; devuelve los ids de ejecuciones creadas."""
    created: list[int] = []
    db = SessionLocal()
    try:
        now = utcnow()
        due = list(db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)))
        for s in due:
            nxt = now + timedelta(minutes=max(5, s.interval_minutes))
            claimed = db.execute(update(Schedule).where(Schedule.id == s.id, Schedule.next_run_at == s.next_run_at)
                                 .values(next_run_at=nxt)).rowcount
            db.commit()
            if claimed != 1 or kill_switch_active():
                continue
            suite, env = db.get(TestSuite, s.suite_id), db.get(Environment, s.environment_id)
            if not suite or not env or (suite.kind == "load" and env.kind == "production"):
                continue
            busy = db.scalar(select(TestRun.id).where(TestRun.triggered_by == f"schedule:{s.id}",
                                                      TestRun.status.in_(["queued", "running"])).limit(1))
            if busy:
                continue
            run = TestRun(project_id=s.project_id, suite_id=suite.id, environment_id=env.id, kind=suite.kind,
                          triggered_by=f"schedule:{s.id}", version_label="",
                          config={"timeout_s": 900, "environment_kind": env.kind})
            db.add(run)
            db.flush()
            s.last_run_id = run.id
            db.commit()
            executor.submit_run(run.id)
            created.append(run.id)
    finally:
        db.close()
    return created


def _loop() -> None:
    while not _stop.wait(TICK_S):
        try:
            tick()
        except Exception:  # noqa: BLE001  el planificador nunca debe morir
            pass


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="qa-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
