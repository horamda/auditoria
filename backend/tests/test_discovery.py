import pytest

from app.workers.discovery import discover


def _names(feats):
    return {(f.layer, f.kind, f.name) for f in feats}


def test_android_nativo(fixture_repo):
    n = _names(discover(fixture_repo))
    assert ("android", "android_permission", "android/app:android.permission.RECORD_AUDIO") in n
    assert ("android", "activity", "android/app:.PickingActivity") in n
    assert ("android", "existing_test", "android/app/src/androidTest/java/VozTest.kt") in n
    # el manifest de la app Flutter no se duplica como Android nativo
    assert not any(l == "android" and "CAMERA" in name for l, _, name in n)


def test_acotado_a_componentes_con_ids_estables(fixture_repo):
    full = {f.stable_id: f for f in discover(fixture_repo)}
    only_mobile = discover(fixture_repo, ["mobile"])
    assert only_mobile and all(f.location.startswith("mobile/") for f in only_mobile)
    assert all(f.stable_id in full for f in only_mobile), "mismos IDs que el análisis completo"
    assert not any(f.layer == "backend" for f in only_mobile)


def test_subcarpeta_fuera_del_proyecto(fixture_repo):
    with pytest.raises(ValueError):
        discover(fixture_repo, ["../.."])
