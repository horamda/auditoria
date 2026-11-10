from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="qacc_test_"))
REPOS = TMP / "repos"
REPOS.mkdir()
os.environ.update({
    # QA_TEST_DATABASE_URL permite correr la suite contra una MySQL de prueba vacía (nunca producción)
    "QA_DATABASE_URL": os.environ.get("QA_TEST_DATABASE_URL", f"sqlite:///{(TMP / 'test.db').as_posix()}"),
    "QA_SECRET_KEY": "test-secret-key-no-usar-en-produccion",
    "QA_ALLOWED_REPO_ROOTS": str(REPOS),
    "QA_ARTIFACTS_DIR": str(TMP / "artifacts"),
    "QA_REPORTS_DIR": str(TMP / "reports"),
    "QA_TEST_USER": "qa",
})


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(cfg, "head")  # valida también la migración


@pytest.fixture(scope="session")
def target_url():
    import uvicorn

    from tests.target_app import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.cli import main as cli
    from app.main import app

    os.environ["QA_ADMIN_PASSWORD"] = "admin-password-123"
    cli(["create-admin", "--email", "admin@qa.local"])
    return TestClient(app)


@pytest.fixture(scope="session")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"email": "admin@qa.local", "password": "admin-password-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def fixture_repo():
    """Repositorio de prueba sintético con Flask, FastAPI, Flutter, React y SQL."""
    root = REPOS / "demo_app"
    (root / "backend/routes").mkdir(parents=True)
    (root / "backend/routes/fichadas.py").write_text(
        "from flask import Blueprint\n"
        "fichadas_bp = Blueprint('fichadas', __name__, url_prefix='/api/fichadas')\n"
        "@fichadas_bp.route('/registrar', methods=['POST'])\n@jwt_required()\ndef registrar():\n    pass\n"
        "@fichadas_bp.route('/listado')\ndef listado():\n    pass\n", encoding="utf-8")
    (root / "backend/models.py").write_text(
        "class Fichada(Base):\n    __tablename__ = 'fichadas'\n    emp = Column(ForeignKey('empleados.id'))\n", encoding="utf-8")
    (root / "api").mkdir()
    (root / "api/main.py").write_text(
        "router = APIRouter(prefix='/v1')\n@router.get('/cargas')\nasync def cargas(u=Depends(get_current_user)):\n    pass\n", encoding="utf-8")
    (root / "db.sql").write_text("CREATE TABLE IF NOT EXISTS `cargas` (id int);\nCREATE UNIQUE INDEX ux_c ON cargas (id);\n", encoding="utf-8")
    app = root / "mobile"
    (app / "lib/screens").mkdir(parents=True)
    (app / "pubspec.yaml").write_text("name: demo\ndependencies:\n  flutter:\n    sdk: flutter\n  http: ^1.0.0\n  provider: ^6.0.0\n"
                                      "dev_dependencies:\n  flutter_test:\n    sdk: flutter\n", encoding="utf-8")
    (app / "lib/screens/login_screen.dart").write_text(
        "class LoginScreen extends StatefulWidget {}\nclass _S { build() => Form(child: TextFormField(key: Key('login_user'))); }\n"
        "final r = {'/login': (c) => LoginScreen()};\nvoid f() { http.post(Uri.parse('$base/api/auth/login')); }\n", encoding="utf-8")
    (app / "android/app/src/main").mkdir(parents=True)
    (app / "android/app/src/main/AndroidManifest.xml").write_text(
        '<manifest><uses-permission android:name="android.permission.CAMERA"/></manifest>', encoding="utf-8")
    (app / "test").mkdir()
    (app / "test/login_test.dart").write_text("void main() {}", encoding="utf-8")
    web = root / "panel/src"
    web.mkdir(parents=True)
    (web / "App.tsx").write_text("<Route path=\"/usuarios\" element={<U/>} />\nfetch('/api/usuarios')\n<button>Guardar</button>\n<table></table>", encoding="utf-8")
    nat = root / "android/app/src"
    (nat / "main").mkdir(parents=True)
    (nat / "main/AndroidManifest.xml").write_text(
        '<manifest><uses-permission android:name="android.permission.RECORD_AUDIO"/>'
        '<application><activity android:name=".PickingActivity"/></application></manifest>', encoding="utf-8")
    (nat / "androidTest/java").mkdir(parents=True)
    (nat / "androidTest/java/VozTest.kt").write_text("class VozTest { @Test fun reconoce() {} }", encoding="utf-8")
    (root / "node_modules/x").mkdir(parents=True)
    (root / "node_modules/x/a.js").write_text("fetch('/ignorado')", encoding="utf-8")
    return root
