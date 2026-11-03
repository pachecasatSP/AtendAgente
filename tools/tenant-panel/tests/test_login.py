"""Tela de login (/painel/login): fontes por arquivo, nome da loja, erro acessivel, campos corretos.
Cobre o redesenho de 2026-11-03 (antes: 77 KB de fonte em base64 por pagina, contorno no texto da marca, nome da loja como slug,
`?erro=` da URL escrito dentro da caixa de erro, campos sem autocomplete e com fonte de 15 px)."""
import importlib
import re
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}


def carregar(monkeypatch, com_usuario=True, com_signup=True, **env):
    for k, v in {**ENV, **env}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    monkeypatch.setattr(app, "auth_col", db["panel_auth"])
    monkeypatch.setattr(app, "signups_col", db["signups"])
    if com_signup:
        db["signups"].insert_one({"tenant_id": app.TENANT_ID, "status": "live", "config": {"nome_negocio": "Loja <Teste> & Cia"}})
    if com_usuario:
        h, s = app._hash_password("senha-de-teste-123")
        db["panel_auth"].insert_one({"_id": app.TENANT_ID, "username": "ana", "password_hash": h, "password_salt": s})
    from starlette.testclient import TestClient
    return app, TestClient(app.app, follow_redirects=False)


@pytest.fixture
def cli(monkeypatch):
    return carregar(monkeypatch)[1]


def test_fontes_vao_por_arquivo_e_nao_embutidas(cli):
    html = cli.get("/painel/login").text
    assert "base64" not in html and len(html) < 20_000
    assert "/painel/fonte/baloo2-800.woff2" in html and "/painel/fonte/nunito-var.woff2" in html


def test_rota_de_fontes_do_login(cli):
    r = cli.get("/painel/fonte/baloo2-800.woff2")
    assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
    assert "immutable" in r.headers["cache-control"] and len(r.content) > 5_000
    for ruim in ("passwd", "..%2f..%2fapp.py", "%2e%2e%2fapp.py", "login.html"):
        assert cli.get(f"/painel/fonte/{ruim}").status_code == 404, ruim


def test_fonte_do_login_funciona_no_modo_admin_da_duda(monkeypatch):
    _, cli = carregar(monkeypatch, TENANT_ID="duda", PANEL_MODE="admin")
    assert cli.get("/painel/fonte/nunito-var.woff2").status_code == 200
    assert cli.get("/vitrine/fonte/nunito-var.woff2").status_code == 404  # /vitrine segue bloqueado no modo admin
    assert "/painel/fonte/" in cli.get("/painel/login").text


def test_marca_mostra_o_nome_da_loja_escapado(cli):
    html = cli.get("/painel/login").text
    assert "Loja &lt;Teste&gt; &amp; Cia" in html and "<Teste>" not in html
    assert "<title>Entrar — Loja &lt;Teste&gt; &amp; Cia</title>" in html


def test_sem_cadastro_a_marca_cai_no_tenant_id(monkeypatch):
    _, cli = carregar(monkeypatch, com_signup=False)
    assert 'class="loja">loja-teste<' in cli.get("/painel/login").text


def css_da_pagina(cli, html):
    """Todos os CSS que a pagina referencia, juntos (plataforma.css = tokens, acesso.css = telas de acesso)."""
    hrefs = re.findall(r'href="(/painel/estilo/[a-z]+\.css\?v=[0-9a-f]{10})"', html)
    assert len(hrefs) == 2
    out = ""
    for h in hrefs:
        r = cli.get(h)
        assert r.status_code == 200
        out += r.text
    return out


def test_sem_contorno_no_texto_e_sem_botao_amarelo_com_brilho(cli):
    css = css_da_pagina(cli, cli.get("/painel/login").text)
    assert "text-stroke" not in css
    assert "var(--acao)" in css and "255,201,60" not in css


def test_campos_com_autocomplete_e_fonte_de_16px(cli):
    html = cli.get("/painel/login").text
    assert 'autocomplete="username"' in html and 'autocomplete="current-password"' in html
    assert 'autocapitalize="none"' in html and 'spellcheck="false"' in html
    assert re.search(r"input\s*\{[^}]*font-size:\s*1rem", css_da_pagina(cli, html))
    assert 'for="username"' in html and 'for="password"' in html


def test_erro_na_url_nao_vira_texto_da_pagina(cli):
    html = cli.get("/painel/login", params={"erro": "Sua conta foi bloqueada, ligue 0800-FALSO"}).text
    assert "0800-FALSO" not in html and 'role="alert"' not in html


def test_senha_errada_mostra_alerta_e_devolve_o_usuario(cli):
    r = cli.post("/painel/login", data={"username": " ana ", "password": "errada"})
    assert r.status_code == 400
    assert 'role="alert"' in r.text and "Usuário ou senha incorretos." in r.text
    assert 'value="ana"' in r.text
    assert re.search(r'id="password"[^>]*autofocus', r.text)  # o cursor volta pra senha


def test_usuario_devolvido_e_escapado(cli):
    r = cli.post("/painel/login", data={"username": '"><script>alert(1)</script>', "password": "x"})
    assert "<script>alert(1)" not in r.text and "&lt;script&gt;" in r.text


def test_login_correto_redireciona_pro_painel(cli):
    r = cli.post("/painel/login", data={"username": "ana", "password": "senha-de-teste-123"})
    assert r.status_code == 303 and r.headers["location"] == "/painel"


def test_painel_sem_usuario_mostra_aviso_e_nao_alerta(monkeypatch):
    _, cli = carregar(monkeypatch, com_usuario=False)
    html = cli.get("/painel/login").text
    assert 'role="status"' in html and "link de configuração" in html and 'role="alert"' not in html


def test_botao_mostrar_senha_so_aparece_com_js(cli):
    html = cli.get("/painel/login").text
    assert re.search(r'data-ver-senha="password"[^>]*\bhidden\b', html)  # sem JS fica escondido e o formulario funciona igual
