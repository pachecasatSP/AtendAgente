"""Limite de tentativas (login e PIN), comparação do PIN e cookie de sessão seguro."""
import importlib

from starlette.testclient import TestClient

from app import onboarding_client, ratelimit, store
from conftest import SENHA


# ------------------------------------------------------------------ ratelimit

def test_bloqueia_apos_o_maximo_de_falhas_e_libera_com_o_tempo():
    for i in range(ratelimit.MAX_FALHAS):
        assert not ratelimit.bloqueado("k", agora=100.0 + i)
        ratelimit.registrar_falha("k", agora=100.0 + i)
    assert ratelimit.bloqueado("k", agora=110.0)
    assert not ratelimit.bloqueado("outra-chave", agora=110.0)
    assert not ratelimit.bloqueado("k", agora=100.0 + ratelimit.JANELA_SEGUNDOS + ratelimit.MAX_FALHAS)


def test_zerar_limpa_as_falhas():
    for _ in range(ratelimit.MAX_FALHAS):
        ratelimit.registrar_falha("k")
    assert ratelimit.bloqueado("k")
    ratelimit.zerar("k")
    assert not ratelimit.bloqueado("k")


# ---------------------------------------------------------------------- login

def cliente_anonimo():
    from app.main import app

    return TestClient(app, follow_redirects=False)


def tentar_login(cliente, username, senha):
    return cliente.post("/login", data={"username": username, "password": senha})


def test_login_bloqueia_apos_tentativas_erradas_mesmo_com_a_senha_certa(novo_cliente, db):
    novo_cliente("ana")
    anonimo = cliente_anonimo()
    for _ in range(ratelimit.MAX_FALHAS):
        assert tentar_login(anonimo, "ana", "errada").status_code == 400

    assert tentar_login(anonimo, "ana", SENHA).status_code == 429


def test_login_de_outro_usuario_nao_e_afetado(novo_cliente, db):
    novo_cliente("ana")
    novo_cliente("beto")
    anonimo = cliente_anonimo()
    for _ in range(ratelimit.MAX_FALHAS):
        tentar_login(anonimo, "ana", "errada")

    assert tentar_login(anonimo, "beto", SENHA).status_code == 303


def test_login_certo_zera_as_falhas(novo_cliente, db):
    novo_cliente("ana")
    anonimo = cliente_anonimo()
    for _ in range(ratelimit.MAX_FALHAS - 1):
        tentar_login(anonimo, "ana", "errada")
    assert tentar_login(anonimo, "ana", SENHA).status_code == 303

    for _ in range(ratelimit.MAX_FALHAS - 1):  # recomeça do zero
        assert tentar_login(anonimo, "ana", "errada").status_code == 400


# ------------------------------------------------------------------------ PIN

URL_ATIVAR = "/admin/tenants/loja-a/ativar"


def ativar(cliente, pin):
    return cliente.post(URL_ATIVAR, data={"enabled": "false", "pin": pin})


def test_pin_correto_chama_o_onboarding(novo_cliente, db, monkeypatch):
    chamadas = []
    monkeypatch.setattr(onboarding_client, "ativar_tenant", lambda t, e: chamadas.append((t, e)))
    admin, _ = novo_cliente("chefe", role="admin")

    assert ativar(admin, "1234").status_code == 303
    assert chamadas == [("loja-a", False)]


def test_pin_errado_e_recusado_sem_chamar_o_onboarding(novo_cliente, db, monkeypatch):
    chamadas = []
    monkeypatch.setattr(onboarding_client, "ativar_tenant", lambda t, e: chamadas.append((t, e)))
    admin, _ = novo_cliente("chefe", role="admin")

    assert ativar(admin, "0000").status_code == 403
    assert chamadas == []


def test_pin_com_acento_da_403_e_nao_500(novo_cliente, db, monkeypatch):
    """compare_digest com str não-ASCII levantava TypeError."""
    monkeypatch.setattr(onboarding_client, "ativar_tenant", lambda t, e: None)
    admin, _ = novo_cliente("chefe", role="admin")

    assert ativar(admin, "ação").status_code == 403


def test_pin_bloqueia_apos_tentativas_erradas_mesmo_com_o_pin_certo(novo_cliente, db, monkeypatch):
    chamadas = []
    monkeypatch.setattr(onboarding_client, "ativar_tenant", lambda t, e: chamadas.append((t, e)))
    admin, _ = novo_cliente("chefe", role="admin")
    for _ in range(ratelimit.MAX_FALHAS):
        assert ativar(admin, "0000").status_code == 403

    assert ativar(admin, "1234").status_code == 429
    assert chamadas == []


def test_pin_certo_zera_as_falhas(novo_cliente, db, monkeypatch):
    monkeypatch.setattr(onboarding_client, "ativar_tenant", lambda t, e: None)
    admin, _ = novo_cliente("chefe", role="admin")
    for _ in range(ratelimit.MAX_FALHAS - 1):
        ativar(admin, "0000")
    assert ativar(admin, "1234").status_code == 303

    for _ in range(ratelimit.MAX_FALHAS - 1):
        assert ativar(admin, "0000").status_code == 403


def test_vendedor_comum_nao_chega_nem_a_tentar_o_pin(novo_cliente, db):
    cliente, _ = novo_cliente("ana")
    assert ativar(cliente, "1234").status_code == 403


# --------------------------------------------------------------------- cookie

def test_cookie_de_sessao_e_secure_por_padrao(db, monkeypatch):
    import app.main as main

    monkeypatch.setenv("SESSION_HTTPS_ONLY", "1")
    try:
        recarregado = importlib.reload(main)
        store.create_vendedor("Ana", "a@x.com", "", "ana", SENHA)
        cliente = TestClient(recarregado.app, base_url="https://testserver", follow_redirects=False)
        resposta = tentar_login(cliente, "ana", SENHA)
        cookie = resposta.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    finally:
        monkeypatch.setenv("SESSION_HTTPS_ONLY", "0")
        importlib.reload(main)
