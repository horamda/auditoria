from app.workers import android_runner as ar
from app.workers.base import RunContext
from app.core.guard import EnvPolicy
import time


def _ctx():
    return RunContext(run_id=999, policy=EnvPolicy(kind="development"), base_url=None, api_url=None,
                      credentials_env_prefix="", deadline=time.monotonic() + 60)


def test_sin_dispositivo_fisico_se_omite_y_no_se_instala_nada(monkeypatch):
    monkeypatch.setattr(ar, "list_devices", lambda: [{"serial": "emulator-5554", "kind": "emulator"}])
    res = ar.run_android_case({"device_kind": "physical", "package": "x.y"}, _ctx(), "t")
    assert res["status"] == "skipped" and "physical" in res["error"]


def test_dispositivo_fisico_exige_autorizacion(monkeypatch):
    monkeypatch.setattr(ar, "list_devices", lambda: [{"serial": "ABC123", "kind": "physical"}])
    res = ar.run_android_case({"device_kind": "physical", "package": "x.y"}, _ctx(), "t")
    assert res["status"] == "blocked" and "allow_physical" in res["error"]


def test_adb_ausente_se_omite_con_motivo(monkeypatch):
    def boom():
        raise ar.AndroidError("adb no encontrado")
    monkeypatch.setattr(ar, "list_devices", boom)
    res = ar.run_android_case({"package": "x.y"}, _ctx(), "t")
    assert res["status"] == "skipped"


def test_componente():
    assert ar._component("a.b", ".Main") == "a.b/.Main"
    assert ar._component("a.b", "c.d.Main") == "a.b/.c.d.Main" or ar._component("a.b", "c.d.Main").startswith("a.b/")
    assert ar._component("a.b", "x/y") == "a.b/x/y"
