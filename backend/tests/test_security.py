from __future__ import annotations

import hashlib
import shutil
import subprocess
import time

import pytest

from app.workers import security_runner as sr
from tests.conftest import REPOS


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


def _tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return h.hexdigest(), sum(1 for _ in root.rglob("*"))


@pytest.fixture(scope="module")
def sec_repo():
    root = REPOS / "sec_app"
    (root / "api").mkdir(parents=True)
    (root / "api/requirements.txt").write_text("flask==0.12\nrequests>=2.0\n# comentario\n", encoding="utf-8")
    (root / "api/app.py").write_text(
        "import subprocess\nfrom flask import Flask\napp = Flask(__name__)\n"
        "def f(x):\n    subprocess.call(x, shell=True)\n\napp.run(debug=True)\n", encoding="utf-8")
    return root


def test_pip_audit_sin_instalar_y_reporta_no_fijadas(sec_repo, tmp_path):
    findings, stats = sr.run_pip_audit(sec_repo, sec_repo, tmp_path, 120, lambda: None)
    assert stats == {"files": 1, "pinned": 1, "unpinned_skipped": 1}
    assert findings and all(f["package"].lower() == "flask" and f["installed_version"] == "0.12" for f in findings)
    assert all(f["file"] == "api/requirements.txt" for f in findings)
    assert not list(sec_repo.rglob("pinned_*")), "la copia filtrada nunca se escribe en el repo"


def test_unifica_duplicados_entre_herramientas():
    a = {"tool": "osv-scanner", "category": "dependencia", "package": "Flask", "installed_version": "0.12",
         "vuln_ids": ["GHSA-1", "PYSEC-1"], "severity": "high", "fixed_version": "0.12.3"}
    b = {"tool": "pip-audit", "category": "dependencia", "package": "flask", "installed_version": "0.12",
         "vuln_ids": ["PYSEC-1"], "severity": "medium", "fixed_version": ""}
    c = {"tool": "semgrep", "category": "codigo", "severity": "high"}
    out = sr.merge_dependency_duplicates([a, b, c])
    assert len(out) == 2
    assert out[0]["tools"] == ["osv-scanner", "pip-audit"] and out[0]["severity"] == "high"


def test_cvss_a_severidad():
    assert [sr._cvss_to_sev(x) for x in ("9.8", 7.5, "5", "2", None, "")] == ["critical", "high", "medium", "low", "medium", "medium"]


@pytest.mark.skipif(not _docker_ok(), reason="Docker no disponible")
def test_escaneo_completo_via_api(client, admin_headers, sec_repo):
    h = admin_headers
    before = _tree_hash(sec_repo)
    pid = client.post("/api/projects", headers=h, json={"name": "Sec demo", "local_path": str(sec_repo)}).json()["id"]

    def scan():
        r = client.post(f"/api/projects/{pid}/security-scans", headers=h, json={})
        assert r.status_code == 202, r.text
        rid = r.json()["id"]
        for _ in range(600):
            run = client.get(f"/api/runs/{rid}", headers=h).json()
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(1)
        raise AssertionError("timeout")

    run = scan()
    res = {r["case_name"]: r for r in run["results"]}
    assert set(res) == {"Seguridad: semgrep", "Seguridad: osv-scanner", "Seguridad: pip-audit"}
    assert all(r["status"] in ("passed", "failed") for r in res.values()), [(k, r["error"]) for k, r in res.items()]
    assert "sin versión fija" in res["Seguridad: pip-audit"]["details"]["limitacion"]

    data = client.get(f"/api/projects/{pid}/security-findings", headers=h).json()
    f = data["findings"]
    rules = {x["rule_id"] for x in f if x["tool"] == "semgrep"}
    assert any("subprocess-shell-true" in r for r in rules), rules
    flask = [x for x in f if x["category"] == "dependencia" and x["package"].lower() == "flask"]
    assert flask and any("pip-audit" in x["details"]["tools"] and "osv-scanner" in x["details"]["tools"] for x in flask)
    assert all(x["file"].startswith("api/") for x in f)
    assert all(x["status"] == "primer análisis" for x in f)

    # Segundo escaneo tras desplazar líneas: mismas huellas -> "existente"
    app = sec_repo / "api/app.py"
    app.write_text("# línea nueva\n" + app.read_text(encoding="utf-8"), encoding="utf-8")
    before = _tree_hash(sec_repo)
    scan()
    data2 = client.get(f"/api/projects/{pid}/security-findings", headers=h).json()
    assert data2["previous_run"] == run["id"]
    assert all(x["status"] == "existente" for x in data2["findings"]), [x["title"] for x in data2["findings"] if x["status"] != "existente"]
    assert data2["resolved"] == []
    assert _tree_hash(sec_repo) == before, "el análisis no debe modificar el repositorio"

    rep = client.get(f"/api/runs/{run['id']}/report.html", headers=h).text
    assert "Hallazgos de seguridad" in rep and "falsos positivos" in rep

    # Triage: exige motivo, persiste entre escaneos y se registra en auditoría
    target = next(x for x in data2["findings"] if x["tool"] == "semgrep")
    url = f"/api/projects/{pid}/security-triage"
    assert client.put(url, headers=h, json={"fingerprint": target["fingerprint"], "status": "false_positive"}).status_code == 422
    assert client.put(url, headers=h, json={"fingerprint": "no-existe-123", "status": "in_progress"}).status_code == 404
    ok = client.put(url, headers=h, json={"fingerprint": target["fingerprint"], "status": "false_positive", "note": "Entrada controlada, sin datos de usuario"})
    assert ok.status_code == 200
    scan()
    data3 = client.get(f"/api/projects/{pid}/security-findings", headers=h).json()
    same = next(x for x in data3["findings"] if x["fingerprint"] == target["fingerprint"])
    assert same["triage"]["status"] == "false_positive" and same["triage"]["by"] == "admin@qa.local"
    assert all(x["triage"] is None for x in data3["findings"] if x["fingerprint"] != target["fingerprint"])
    client.put(url, headers=h, json={"fingerprint": target["fingerprint"], "status": "open"})
    data4 = client.get(f"/api/projects/{pid}/security-findings", headers=h).json()
    assert next(x for x in data4["findings"] if x["fingerprint"] == target["fingerprint"])["triage"] is None
    assert any(a["action"] == "finding_triage" for a in client.get("/api/audit", headers=h).json())
