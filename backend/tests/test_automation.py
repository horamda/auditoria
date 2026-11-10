"""Automatización: programaciones, avisos, hook de deploy y suite de humo autogenerada."""
from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import AuditLog, DiscoveredFeature, Schedule, TestRun, utcnow
from app.services import notifier, scheduler


def _wait(client, h, rid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/runs/{rid}", headers=h).json()
        if r["status"] in ("passed", "failed", "error", "cancelled", "blocked"):
            return r
        time.sleep(0.3)
    raise AssertionError("timeout esperando la ejecución")


def _until(cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def auto(client, admin_headers, target_url):
    h = admin_headers
    pid = client.post("/api/projects", headers=h, json={"name": "Auto QA"}).json()["id"]
    host = urlsplit(target_url).hostname
    env = client.post(f"/api/projects/{pid}/environments", headers=h, json={
        "name": "dev", "kind": "development", "base_url": target_url, "api_url": target_url,
        "allowed_hosts": [host], "allow_private_network": True}).json()
    prod = client.post(f"/api/projects/{pid}/environments", headers=h, json={
        "name": "prod", "kind": "production", "base_url": target_url, "api_url": target_url,
        "allowed_hosts": [host], "allow_private_network": True}).json()

    def suite(name, kind, cases):
        return client.post(f"/api/projects/{pid}/suites", headers=h, json={"name": name, "kind": kind, "cases": cases}).json()["id"]

    ok = suite("Sano", "api", [{"name": "health", "definition": {"method": "GET", "path": "/api/health", "expect_status": 200}}])
    bad = suite("Roto", "api", [{"name": "falla", "definition": {"method": "GET", "path": "/api/health", "expect_status": 404}}])
    load = suite("Carga", "load", [{"name": "c", "definition": {"profile": "smoke"}}])
    return {"id": pid, "env": env["id"], "prod": prod["id"], "ok": ok, "bad": bad, "load": load}


def test_autosmoke_sin_descubrimiento(client, admin_headers, auto):
    r = client.post(f"/api/projects/{auto['id']}/suites/auto-smoke", headers=admin_headers, json={})
    assert r.status_code == 422


def test_autosmoke_genera_solo_get_sin_auth_ni_parametros(client, admin_headers, auto):
    def feat(sid, method, path, auth):
        return DiscoveredFeature(project_id=auto["id"], stable_id=sid, layer="backend", kind="endpoint", name=f"{method} {path}",
                                 location="x.py:1", details={"method": method, "path": path, "auth_detected": auth})
    with SessionLocal() as db:
        db.add_all([feat("s1", "GET", "/api/health", False), feat("s2", "GET", "/api/items", False),
                    feat("s3", "GET", "/api/secreto", True), feat("s4", "POST", "/api/items", False),
                    feat("s5", "GET", "/api/items/<int:id>", False), feat("s6", "GET", "/api/{x}", False)])
        db.commit()
    r = client.post(f"/api/projects/{auto['id']}/suites/auto-smoke", headers=admin_headers, json={})
    assert r.status_code == 201 and r.json()["cases"] == 2
    again = client.post(f"/api/projects/{auto['id']}/suites/auto-smoke", headers=admin_headers, json={})
    assert again.json()["suite_id"] == r.json()["suite_id"] and again.json()["cases"] == 2  # actualiza, no duplica
    run = client.post(f"/api/projects/{auto['id']}/runs", headers=admin_headers,
                      json={"suite_id": r.json()["suite_id"], "environment_id": auto["env"]}).json()
    final = _wait(client, admin_headers, run["id"])
    assert final["status"] == "passed", final


def test_programacion_validaciones(client, admin_headers, auto):
    base = f"/api/projects/{auto['id']}/schedules"
    assert client.post(base, headers=admin_headers, json={"suite_id": auto["ok"], "environment_id": auto["env"], "interval_minutes": 1}).status_code == 422
    assert client.post(base, headers=admin_headers, json={"suite_id": auto["load"], "environment_id": auto["env"]}).status_code == 422
    assert client.post(base, headers=admin_headers, json={"suite_id": auto["load"], "environment_id": auto["prod"]}).status_code == 403
    assert client.post(base, headers=admin_headers, json={"suite_id": 9999, "environment_id": auto["env"]}).status_code == 404


def test_planificador_lanza_una_vez_y_respeta_kill_switch(client, admin_headers, auto):
    r = client.post(f"/api/projects/{auto['id']}/schedules", headers=admin_headers,
                    json={"suite_id": auto["ok"], "environment_id": auto["env"], "interval_minutes": 5})
    assert r.status_code == 201
    sid = r.json()["id"]
    with SessionLocal() as db:  # lo hacemos vencer
        db.get(Schedule, sid).next_run_at = utcnow() - timedelta(minutes=1)
        db.commit()
    created = scheduler.tick()
    assert len(created) == 1
    assert scheduler.tick() == [], "no debe lanzar dos veces: next_run_at ya avanzó"
    assert _wait(client, admin_headers, created[0])["status"] == "passed"
    with SessionLocal() as db:
        s = db.get(Schedule, sid)
        assert s.last_run_id == created[0] and s.next_run_at > utcnow() + timedelta(minutes=3)
        assert db.get(TestRun, created[0]).triggered_by == f"schedule:{sid}"
        s.next_run_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.post("/api/system/kill-switch?active=true", headers=admin_headers).status_code in (200, 204)
    try:
        assert scheduler.tick() == [], "con kill switch no se lanza nada"
    finally:
        client.post("/api/system/kill-switch?active=false", headers=admin_headers)
    assert client.delete(f"/api/projects/{auto['id']}/schedules/{sid}", headers=admin_headers).status_code == 204


def test_aviso_en_fallo_y_recuperacion(client, admin_headers, auto, monkeypatch):
    sent: list[str] = []

    class R:
        status_code = 200

    monkeypatch.setattr(notifier.httpx, "post", lambda url, json=None, **kw: (sent.append(json["text"]), R())[1])
    monkeypatch.setattr(get_settings(), "notify_webhook_url", "https://hooks.example.test/x")
    pid = auto["id"]

    def trigger(suite_id, who):
        with SessionLocal() as db:
            run = TestRun(project_id=pid, suite_id=suite_id, environment_id=auto["env"], kind="api", triggered_by=who,
                          config={"timeout_s": 60, "environment_kind": "development"})
            db.add(run)
            db.commit()
            rid = run.id
        from app.services import executor
        executor.execute_sync(rid)
        return rid

    trigger(auto["bad"], "schedule:77")
    assert len(sent) == 1 and "FALLO" in sent[0] and "Roto" in sent[0]
    trigger(auto["bad"], "admin@qa.local")  # manual: no avisa
    assert len(sent) == 1
    trigger(auto["ok"], "schedule:77")  # sano tras sano: no avisa (la anterior de ESTA suite no falló)
    assert len(sent) == 1
    # recuperación: falla y luego pasa la misma suite
    with SessionLocal() as db:
        suite_id = auto["bad"]
        from app.models import TestCase
        case = db.scalars(select(TestCase).where(TestCase.suite_id == suite_id)).first()
        case.definition = {"method": "GET", "path": "/api/health", "expect_status": 200}
        db.commit()
    trigger(auto["bad"], "hook:deploy")
    assert len(sent) == 2 and "RECUPERADO" in sent[1]
    with SessionLocal() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.action.like("notify_%")))]
    assert actions.count("notify_sent") == 2


def test_aviso_sin_webhook_no_rompe(auto, monkeypatch):
    monkeypatch.setattr(get_settings(), "notify_webhook_url", "")
    with SessionLocal() as db:
        run = TestRun(project_id=auto["id"], suite_id=auto["bad"], environment_id=auto["env"], kind="api",
                      triggered_by="schedule:1", status="failed", summary={})
        db.add(run)
        db.commit()
        rid = run.id
    notifier.notify_run_finished(rid)
    with SessionLocal() as db:
        assert db.scalars(select(AuditLog).where(AuditLog.action == "notify_skipped", AuditLog.target == f"run:{rid}")).first()


def test_hook_deploy(client, admin_headers, auto, monkeypatch):
    body = {"project": "Auto QA", "environment": "dev", "version_label": "v9"}
    monkeypatch.setattr(get_settings(), "deploy_hook_token", "")
    assert client.post("/api/hooks/deploy", json=body).status_code == 404, "deshabilitado sin token configurado"
    monkeypatch.setattr(get_settings(), "deploy_hook_token", "token-de-prueba-largo")
    assert client.post("/api/hooks/deploy", json=body).status_code == 401
    assert client.post("/api/hooks/deploy", json=body, headers={"X-QA-Token": "otro"}).status_code == 401
    assert client.post("/api/hooks/deploy", json={**body, "environment": "nope"}, headers={"X-QA-Token": "token-de-prueba-largo"}).status_code == 404
    r = client.post("/api/hooks/deploy", json=body, headers={"X-QA-Token": "token-de-prueba-largo"})
    assert r.status_code == 202
    runs = r.json()["runs"]
    assert len(runs) >= 2 and auto["load"] not in runs  # nunca lanza suites de carga
    tok = {"X-QA-Token": "token-de-prueba-largo"}
    assert _until(lambda: all(client.get(f"/api/hooks/runs/{i}", headers=tok).json()["status"] in ("passed", "failed") for i in runs))
    assert client.get(f"/api/hooks/runs/{runs[0]}").status_code == 401
    # el estado del hook solo expone ejecuciones del hook
    with SessionLocal() as db:
        manual = TestRun(project_id=auto["id"], suite_id=auto["ok"], environment_id=auto["env"], kind="api", triggered_by="x@y.z")
        db.add(manual)
        db.commit()
        mid = manual.id
    assert client.get(f"/api/hooks/runs/{mid}", headers=tok).status_code == 404
    with SessionLocal() as db:
        assert all(db.get(TestRun, i).version_label == "v9" for i in runs)


def test_hook_respeta_kill_switch(client, admin_headers, auto, monkeypatch):
    monkeypatch.setattr(get_settings(), "deploy_hook_token", "token-de-prueba-largo")
    client.post("/api/system/kill-switch?active=true", headers=admin_headers)
    try:
        r = client.post("/api/hooks/deploy", json={"project": "Auto QA", "environment": "dev"}, headers={"X-QA-Token": "token-de-prueba-largo"})
        assert r.status_code == 423
    finally:
        client.post("/api/system/kill-switch?active=false", headers=admin_headers)
