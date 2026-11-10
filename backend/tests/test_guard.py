import pytest

from app.core.guard import EnvPolicy, PolicyViolation, check_load, check_method, check_sensitive_action, check_url

PUBLIC = EnvPolicy(kind="staging", allowed_hosts=["example.com", "*.example.org"])


@pytest.mark.parametrize("url", [
    "ftp://example.com/x", "file:///etc/passwd", "http://user:pw@example.com/", "http://evil.com/",
    "http://example.com.evil.com/", "http://example.org/",  # el comodín no cubre el dominio raíz
])
def test_urls_rechazadas(url):
    with pytest.raises(PolicyViolation):
        check_url(url, PUBLIC, resolve=False)


def test_allowlist_y_comodin():
    assert check_url("https://example.com/a", PUBLIC, resolve=False)
    assert check_url("https://api.example.org/a", PUBLIC, resolve=False)


def test_allowlist_vacia_bloquea_todo():
    with pytest.raises(PolicyViolation, match="allowlist"):
        check_url("https://example.com", EnvPolicy(kind="development"), resolve=False)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "192.168.1.10"])
def test_ssrf_redes_internas_bloqueadas(host):
    pol = EnvPolicy(kind="staging", allowed_hosts=[host])
    with pytest.raises(PolicyViolation, match="interna"):
        check_url(f"http://{host}/", pol)


def test_red_privada_solo_si_habilitada_y_nunca_en_produccion():
    dev = EnvPolicy(kind="development", allowed_hosts=["127.0.0.1"], allow_private_network=True)
    assert check_url("http://127.0.0.1:8000/", dev)
    prod = EnvPolicy(kind="production", allowed_hosts=["127.0.0.1"], allow_private_network=True)
    with pytest.raises(PolicyViolation):
        check_url("http://127.0.0.1/", prod)


def test_produccion_solo_lectura_aunque_se_configure_escritura():
    prod = EnvPolicy(kind="production", allowed_hosts=["x.com"], allow_writes=True)
    check_method("GET", prod)
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(PolicyViolation):
            check_method(m, prod, approved_write=True)


def test_escritura_en_staging_requiere_permiso():
    st = EnvPolicy(kind="staging", allowed_hosts=["x.com"])
    with pytest.raises(PolicyViolation):
        check_method("POST", st)
    check_method("POST", st, approved_write=True)
    check_method("POST", EnvPolicy(kind="staging", allowed_hosts=["x.com"], allow_writes=True))


def test_acciones_sensibles():
    st = EnvPolicy(kind="staging", allowed_hosts=["x.com"])
    check_sensitive_action("Guardar", st, False)
    for label in ("Eliminar todo", "Liberar camión", "Confirmar pago", "Borrar registro"):
        with pytest.raises(PolicyViolation):
            check_sensitive_action(label, st, False)
    check_sensitive_action("Eliminar", st, True)
    with pytest.raises(PolicyViolation):
        check_sensitive_action("Eliminar", EnvPolicy(kind="production"), True)


def test_limites_de_carga():
    st = EnvPolicy(kind="staging", allowed_hosts=["x.com"], allow_load=True, max_users=100, max_duration_s=60, max_rps=50)
    check_load(100, 60, 50, st)
    for args in ((101, 10, None), (10, 61, None), (10, 10, 51), (0, 10, None)):
        with pytest.raises(PolicyViolation):
            check_load(*args, st)
    with pytest.raises(PolicyViolation):
        check_load(10, 10, None, EnvPolicy(kind="staging", allow_load=False))
    with pytest.raises(PolicyViolation, match="producción"):
        check_load(1, 10, None, EnvPolicy(kind="production", allow_load=True))
