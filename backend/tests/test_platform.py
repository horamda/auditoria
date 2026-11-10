"""Pruebas de extremo a extremo de la API contra un servicio objetivo real (local, sintético)."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import pytest

from app.services import executor


def _wait(client, h, rid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/runs/{rid}", headers=h).json()
        if r["status"] in ("passed", "failed", "error", "cancelled", "blocked"):
            return r
        time.sleep(0.3)
    raise AssertionError("timeout esperando la ejecución")


@pytest.fixture(scope="module")
def project(client, admin_headers, fixture_repo, target_url):
    h = admin_headers
    r = client.post("/api/projects", headers=h, json={
        "name": "Demo QA", "local_path": str(fixture_repo), "version": "1.0",
        # incluye todos los tipos de componente para validar la serialización de la respuesta
        "components": [{"kind": "backend", "name": "api"}, {"kind": "flutter_android", "name": "mobile"},
                       *[{"kind": k, "name": k} for k in ("android_native", "web_admin", "api", "mysql", "postgresql", "module")]]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    host = urlsplit(target_url).hostname
    dev = client.post(f"/api/projects/{pid}/environments", headers=h, json={
        "name": "dev", "kind": "development", "base_url": target_url, "api_url": target_url, "allowed_hosts": [host],
        "allow_private_network": True, "allow_load": True, "max_users": 20, "max_duration_s": 30,
        "credentials_env_prefix": "QA_TEST_"}).json()
    prod = client.post(f"/api/projects/{pid}/environments", headers=h, json={
        "name": "prod", "kind": "production", "base_url": target_url, "api_url": target_url, "allowed_hosts": [host],
        "allow_private_network": True, "allow_writes": True, "allow_load": True}).json()
    return {"id": pid, "dev": dev, "prod": prod}


def test_requiere_autenticacion(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@qa.local", "password": "mal"}).status_code == 401


def test_roles_viewer_no_puede_escribir(client, admin_headers):
    r = client.post("/api/auth/users", headers=admin_headers,
                    json={"email": "viewer@qa.local", "password": "viewer-pass-123", "role": "viewer"})
    assert r.status_code == 201
    tok = client.post("/api/auth/login", json={"email": "viewer@qa.local", "password": "viewer-pass-123"}).json()["access_token"]
    vh = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/projects", headers=vh).status_code == 200
    assert client.post("/api/projects", headers=vh, json={"name": "X"}).status_code == 403


def test_multiples_proyectos_y_produccion_forzada_segura(client, admin_headers, project):
    client.post("/api/projects", headers=admin_headers, json={"name": "Otro proyecto"})
    names = [p["name"] for p in client.get("/api/projects", headers=admin_headers).json()]
    assert {"Demo QA", "Otro proyecto"} <= set(names)
    prod = project["prod"]
    assert prod["allow_writes"] is False and prod["allow_load"] is False and prod["allow_private_network"] is False


def test_descubrimiento(client, admin_headers, project):
    pid = project["id"]
    s = client.post(f"/api/projects/{pid}/discover", headers=admin_headers).json()
    feats = client.get(f"/api/projects/{pid}/features", headers=admin_headers).json()
    names = {(f["layer"], f["kind"], f["name"]) for f in feats}
    assert ("backend", "endpoint", "POST /api/fichadas/registrar") in names
    assert ("backend", "endpoint", "GET /api/fichadas/listado") in names
    assert ("backend", "endpoint", "GET /v1/cargas") in names
    assert ("database", "table", "fichadas") in names and ("database", "table", "cargas") in names
    assert ("flutter", "screen", "mobile:LoginScreen") in names
    assert ("flutter", "route", "mobile:/login") in names
    assert ("flutter", "android_permission", "mobile:android.permission.CAMERA") in names
    assert ("flutter", "state_management", "mobile:Provider") in names
    assert ("web", "route", "/usuarios") in names and ("web", "api_call", "/api/usuarios") in names
    assert not any("ignorado" in n for _, _, n in names), "node_modules debe excluirse"
    reg = next(f for f in feats if f["name"] == "POST /api/fichadas/registrar")
    assert reg["details"]["auth_detected"] is True and reg["details"]["operation"] == "write"
    assert all(f["evidence_level"] == "static" for f in feats)
    # IDs estables entre ejecuciones
    s2 = client.post(f"/api/projects/{pid}/discover", headers=admin_headers).json()
    assert s2["new"] == 0 and s2["total"] == s["total"]


def test_descubrimiento_ruta_no_autorizada(client, admin_headers, tmp_path):
    pid = client.post("/api/projects", headers=admin_headers, json={"name": "Fuera", "local_path": str(tmp_path)}).json()["id"]
    r = client.post(f"/api/projects/{pid}/discover", headers=admin_headers)
    assert r.status_code == 400 and "no autorizada" in r.json()["detail"]


def _suite(client, h, pid, kind, cases, name=None):
    r = client.post(f"/api/projects/{pid}/suites", headers=h, json={"name": name or f"suite {kind}", "kind": kind, "cases": cases})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _run(client, h, pid, sid, eid, label=""):
    r = client.post(f"/api/projects/{pid}/runs", headers=h, json={"suite_id": sid, "environment_id": eid, "version_label": label})
    assert r.status_code == 202, r.text
    return _wait(client, h, r.json()["id"])


def test_pruebas_api_y_reporte(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    feats = client.get(f"/api/projects/{pid}/features?q=listado", headers=h).json()
    sid = _suite(client, h, pid, "api", [
        {"name": "health ok", "definition": {"method": "GET", "path": "/api/health", "expect_json": {"status": "ok"}, "max_ms": 2000},
         "feature_ids": [feats[0]["stable_id"]]},
        {"name": "crear item", "definition": {"method": "POST", "path": "/api/items", "json": {"n": 1}, "expect_status": 201}},
        {"name": "status inesperado", "definition": {"method": "GET", "path": "/api/items", "expect_status": 404}, "severity": "high"},
        {"name": "redirect a metadata", "definition": {"method": "GET", "path": "/api/redirect-out", "expect_status": 200}},
        {"name": "credencial faltante", "definition": {"method": "GET", "path": "/api/health", "headers": {"Authorization": "Bearer ${NOEXISTE}"}}},
    ])
    run = _run(client, h, pid, sid, project["dev"]["id"])
    st = {r["case_name"]: r for r in run["results"]}
    assert st["health ok"]["status"] == "passed"
    assert st["crear item"]["status"] == "blocked", "dev sin allow_writes no debe escribir"
    assert st["status inesperado"]["status"] == "failed" and "HTTP 200" in st["status inesperado"]["error"]
    assert st["redirect a metadata"]["status"] == "blocked" and "169.254.169.254" in st["redirect a metadata"]["error"]
    assert st["credencial faltante"]["status"] == "blocked" and "QA_TEST_NOEXISTE" in st["credencial faltante"]["error"]
    assert run["status"] == "failed"
    # la funcionalidad cubierta por una prueba aprobada pasa a "verified"
    f = client.get(f"/api/projects/{pid}/features?q=listado", headers=h).json()[0]
    assert f["evidence_level"] == "verified"
    rep = client.get(f"/api/runs/{run['id']}/report.html", headers=h)
    assert rep.status_code == 200 and "Limitaciones" in rep.text and "health ok" in rep.text


def test_produccion_bloquea_escrituras_y_carga(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "api", [{"name": "post prod", "definition": {
        "method": "POST", "path": "/api/items", "json": {}, "approved_write": True, "expect_status": 201}}])
    run = _run(client, h, pid, sid, project["prod"]["id"])
    assert run["results"][0]["status"] == "blocked"
    lsid = _suite(client, h, pid, "load", [{"name": "carga", "definition": {"users": 1, "duration_s": 2, "requests": [{"path": "/api/health"}]}}])
    r = client.post(f"/api/projects/{pid}/runs", headers=h, json={"suite_id": lsid, "environment_id": project["prod"]["id"]})
    assert r.status_code == 403


def test_regresion_detectada_entre_versiones(client, admin_headers, project):
    from tests.target_app import STATE

    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "api", [{"name": "health estable", "definition": {"path": "/api/health", "expect_json": {"status": "ok"}}}])
    base = _run(client, h, pid, sid, project["dev"]["id"], "1.0")
    STATE["broken"] = True
    try:
        head = _run(client, h, pid, sid, project["dev"]["id"], "1.1")
    finally:
        STATE["broken"] = False
    cmp = client.get(f"/api/runs/compare/{base['id']}/{head['id']}", headers=h).json()
    assert cmp["regressions"] == ["health estable"]
    assert "degraded" in head["results"][0]["error"]


def test_kill_switch(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "api", [{"name": "x", "definition": {"path": "/api/health"}}])
    client.post("/api/system/kill-switch?active=true", headers=h)
    try:
        r = client.post(f"/api/projects/{pid}/runs", headers=h, json={"suite_id": sid, "environment_id": project["dev"]["id"]})
        assert r.status_code == 423
    finally:
        client.post("/api/system/kill-switch?active=false", headers=h)
    assert any(a["action"] == "kill_switch" for a in client.get("/api/audit", headers=h).json())


def test_web_playwright(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "web", [
        {"name": "login ok", "definition": {"start_path": "/", "steps": [
            {"action": "fill", "selector": "#user", "value": "${USER}"},
            {"action": "fill", "selector": "#pass", "value": "x"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "expect_url", "contains": "/panel"},
            {"action": "expect_text", "text": "Bienvenido"},
            {"action": "fill", "selector": "#buscar", "value": "fila"},
            {"action": "screenshot", "name": "panel"}]}},
        {"name": "login inválido muestra error", "definition": {"start_path": "/", "steps": [
            {"action": "fill", "selector": "#user", "value": "otro"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "expect_text", "text": "Error de login"}]}},
        {"name": "botón eliminar bloqueado", "definition": {"start_path": "/panel", "steps": [
            {"action": "click", "selector": "#eliminar"}]}},
        {"name": "texto inexistente falla", "definition": {"start_path": "/panel", "step_timeout_ms": 1500, "steps": [
            {"action": "expect_text", "text": "NO EXISTE"}]}},
    ])
    run = _run(client, h, pid, sid, project["dev"]["id"])
    st = {r["case_name"]: r for r in run["results"]}
    assert st["login ok"]["status"] == "passed", st["login ok"]["error"]
    assert len(st["login ok"]["artifacts"]) == 2  # captura "panel" + "final"
    assert st["login inválido muestra error"]["status"] == "passed"
    assert st["botón eliminar bloqueado"]["status"] == "blocked"
    assert st["texto inexistente falla"]["status"] == "failed"
    art = st["login ok"]["artifacts"][0]
    img = client.get(f"/api/artifacts/{art['id']}", headers=h)
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"


def test_carga_locust(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "load", [{"name": "carga básica", "definition": {
        "users": 5, "spawn_rate": 5, "duration_s": 6, "wait_min_s": 0.1, "wait_max_s": 0.3,
        "requests": [{"path": "/api/health", "weight": 3, "name": "health"}, {"path": "/api/items", "weight": 1, "name": "items"}],
        "thresholds": {"error_rate": 0.01, "p95_ms": 2000}}}])
    run = _run(client, h, pid, sid, project["dev"]["id"])
    res = run["results"][0]
    assert res["status"] == "passed", res["error"]
    metrics = {m["name"]: m for m in client.get(f"/api/runs/{run['id']}/metrics", headers=h).json()}
    assert metrics["Aggregated"]["requests"] > 10 and metrics["Aggregated"]["failures"] == 0
    assert {"health", "items"} <= set(metrics)
    # exceder el límite del entorno se bloquea
    sid2 = _suite(client, h, pid, "load", [{"name": "excede", "definition": {"users": 500, "duration_s": 5, "requests": [{"path": "/"}]}}])
    run2 = _run(client, h, pid, sid2, project["dev"]["id"])
    assert run2["results"][0]["status"] == "blocked"


def test_dashboard(client, admin_headers, project):
    d = client.get("/api/dashboard", headers=admin_headers).json()
    assert d["projects"] >= 2 and d["runs"] >= 1 and "verified" in d["coverage"]


def test_cancelacion(client, admin_headers, project):
    pid, h = project["id"], admin_headers
    sid = _suite(client, h, pid, "load", [{"name": "larga", "definition": {"users": 1, "duration_s": 25, "requests": [{"path": "/api/health"}]}}])
    rid = client.post(f"/api/projects/{pid}/runs", headers=h, json={"suite_id": sid, "environment_id": project["dev"]["id"]}).json()["id"]
    time.sleep(3)
    client.post(f"/api/runs/{rid}/cancel", headers=h)
    t0 = time.time()
    run = _wait(client, h, rid, timeout=30)
    assert run["status"] == "cancelled" and time.time() - t0 < 15
    assert executor._cancel.get(rid) is None
