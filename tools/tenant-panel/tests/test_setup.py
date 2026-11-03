"""Criacao de usuario (/painel/setup) e base comum das telas de acesso (/painel/estilo).
Cobre o redesenho de 2026-11-03 (antes: 77 KB de fonte em base64, marca 'AtendPraGente' sem a loja, placeholder como unica instrucao,
campos sem autocomplete 'new-password', sem dica nem checagem da confirmacao, usuario perdido no erro, `?erro=` na URL)."""
import importlib
import re
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "tok-valido",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}


def carregar(monkeypatch, **env):
    for k, v in {**ENV, **env}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    monkeypatch.setattr(app, "auth_col", db["panel_auth"])
    monkeypatch.setattr(app, "signups_col", db["signups"])
    db["signups"].insert_one({"tenant_id": app.TENANT_ID, "status": "live", "config": {"nome_negocio": "Loja <Teste> & Cia"}})
    from starlette.testclient import TestClient
    return app, db, TestClient(app.app, follow_redirects=False)


@pytest.fixture
def ctx(monkeypatch):
    return carregar(monkeypatch)


def pagina(cli, token="tok-valido"):
    r = cli.get("/painel/setup", params={"token": token})
    assert r.status_code == 200
    return r.text


def test_sem_fonte_embutida_e_com_a_marca_da_loja(ctx):
    _, _, cli = ctx
    html = pagina(cli)
    assert "base64" not in html and len(html) < 20_000
    assert "Loja &lt;Teste&gt; &amp; Cia" in html and "<Teste>" not in html
    assert "<title>Criar acesso — Loja &lt;Teste&gt; &amp; Cia</title>" in html


def test_campos_de_criacao_de_senha(ctx):
    _, _, cli = ctx
    html = pagina(cli)
    assert 'autocomplete="username"' in html and html.count('autocomplete="new-password"') == 2
    assert 'for="username"' in html and 'for="password"' in html and 'for="password_confirm"' in html
    assert 'minlength="3"' in html and html.count('minlength="8"') == 2
    assert "Pelo menos 8 caracteres." in html and 'aria-describedby="dica-senha"' in html
    assert 'data-confirma="password"' in html and 'name="token" value="tok-valido"' in html
    assert len(re.findall(r'data-ver-senha="[a-z_]+"[^>]*\bhidden\b', html)) == 2


def test_nao_deixa_o_token_vazar_por_referrer_nem_indexar(ctx):
    _, _, cli = ctx
    html = pagina(cli)
    assert '<meta name="referrer" content="no-referrer">' in html and 'name="robots" content="noindex"' in html


def test_token_errado_continua_403_e_com_usuario_redireciona_pro_login(ctx):
    app, db, cli = ctx
    assert cli.get("/painel/setup", params={"token": "outro"}).status_code == 403
    h, s = app._hash_password("senha-de-teste-123")
    db["panel_auth"].insert_one({"_id": app.TENANT_ID, "username": "ana", "password_hash": h, "password_salt": s})
    r = cli.get("/painel/setup", params={"token": "tok-valido"})
    assert r.status_code == 307 and r.headers["location"] == "/painel/login"


def test_erro_na_url_nao_vira_texto_da_pagina(ctx):
    _, _, cli = ctx
    r = cli.get("/painel/setup", params={"token": "tok-valido", "erro": "Ligue 0800-FALSO"})
    assert "0800-FALSO" not in r.text and 'role="alert"' not in r.text


@pytest.mark.parametrize("dados,msg", [
    ({"username": "ab", "password": "senha-longa-1", "password_confirm": "senha-longa-1"}, "Usuário precisa ter pelo menos 3 caracteres."),
    ({"username": "ana", "password": "curta", "password_confirm": "curta"}, "Senha precisa ter pelo menos 8 caracteres."),
    ({"username": "ana", "password": "senha-longa-1", "password_confirm": "senha-longa-2"}, "As senhas não são iguais."),
])
def test_erro_de_validacao_volta_com_alerta_e_usuario(ctx, dados, msg):
    _, db, cli = ctx
    r = cli.post("/painel/setup", data={"token": "tok-valido", **dados})
    assert r.status_code == 400 and 'role="alert"' in r.text and msg in r.text
    assert f'value="{dados["username"]}"' in r.text and 'name="token" value="tok-valido"' in r.text
    assert re.search(r'id="password"[^>]*autofocus', r.text)
    assert db["panel_auth"].count_documents({}) == 0


def test_usuario_devolvido_e_escapado(ctx):
    _, _, cli = ctx
    r = cli.post("/painel/setup", data={"token": "tok-valido", "username": '"><script>x</script>', "password": "curta", "password_confirm": "curta"})
    assert "<script>x" not in r.text and "&lt;script&gt;" in r.text


def test_criar_acesso_grava_e_entra_no_painel(ctx):
    _, db, cli = ctx
    r = cli.post("/painel/setup", data={"token": "tok-valido", "username": " ana ", "password": "senha-longa-1", "password_confirm": "senha-longa-1"})
    assert r.status_code == 303 and r.headers["location"] == "/painel"
    doc = db["panel_auth"].find_one({"_id": "loja-teste"})
    assert doc["username"] == "ana" and "senha-longa-1" not in str(doc)


def test_estilos_publicos_so_a_lista_fixa_e_com_cache_longo(ctx):
    _, _, cli = ctx
    for nome, tipo in (("plataforma.css", "text/css"), ("plataforma.js", "text/javascript")):
        r = cli.get(f"/painel/estilo/{nome}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(tipo) and "immutable" in r.headers["cache-control"]
    for ruim in ("app.py", "..%2fapp.py", "%2e%2e%2fapp.py", "fontes", "login.html"):
        assert cli.get(f"/painel/estilo/{ruim}").status_code == 404, ruim


def test_endereco_do_css_leva_o_hash_do_conteudo(ctx):
    app, _, _ = ctx
    assert re.fullmatch(r"/painel/estilo/plataforma\.css\?v=[0-9a-f]{10}", app.url_estilo("plataforma.css"))
    assert app.url_estilo("plataforma.css").split("=")[1] != app.url_estilo("plataforma.js").split("=")[1]


def test_estilos_funcionam_no_modo_admin_da_duda(monkeypatch):
    _, _, cli = carregar(monkeypatch, TENANT_ID="duda", PANEL_MODE="admin")
    assert cli.get("/painel/estilo/plataforma.css").status_code == 200
    assert "/painel/estilo/plataforma.css" in cli.get("/painel/login").text
