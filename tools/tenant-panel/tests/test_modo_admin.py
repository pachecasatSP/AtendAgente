"""PANEL_MODE=admin (painel da Duda): só conversas e agenda; catálogo/vitrine/pedidos/configurações somem."""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_ENV = {"TENANT_ID": "duda", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
            "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t"}
BLOQUEADAS = [("GET", "/vitrine"), ("GET", "/configuracoes"), ("GET", "/pedidos"), ("GET", "/painel/api/catalogo-status"),
              ("POST", "/painel/api/catalogo"), ("GET", "/painel/api/pedidos"), ("POST", "/painel/api/contatos/import"),
              ("POST", "/painel/api/configuracoes"), ("POST", "/painel/api/ai-assist"), ("GET", "/painel/api/soul-status"),
              ("GET", "/painel/api/whatsapp/numeros"), ("POST", "/painel/api/cancelar-assinatura"), ("POST", "/painel/api/agenda")]
PERMITIDAS = [("GET", "/health"), ("GET", "/painel/api/sessions"), ("GET", "/painel/api/agenda/eventos"), ("GET", "/agenda")]


def carregar(modo, monkeypatch):
    for k, v in {**BASE_ENV, "PANEL_MODE": modo}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    from starlette.testclient import TestClient
    return app, TestClient(app.app, follow_redirects=False)


def chamar(cli, metodo, caminho):
    return cli.request(metodo, caminho, **({"json": {}} if metodo == "POST" else {})).status_code


def test_modo_admin_bloqueia_o_que_e_de_tenant(monkeypatch):
    _, cli = carregar("admin", monkeypatch)
    assert {(m, p): chamar(cli, m, p) for m, p in BLOQUEADAS} == {(m, p): 404 for m, p in BLOQUEADAS}


def test_modo_admin_mantem_conversas_e_agenda(monkeypatch):
    app, _ = carregar("admin", monkeypatch)
    for _m, p in PERMITIDAS:
        assert not app.rota_bloqueada_no_modo_admin(p), p
    for _m, p in BLOQUEADAS:
        assert app.rota_bloqueada_no_modo_admin(p), p


def test_modo_tenant_nao_bloqueia_nada(monkeypatch):
    # No modo tenant o middleware nem consulta a lista (ADMIN_MODE falso): as rotas seguem para os handlers, que falam com o Mongo.
    app, _ = carregar("tenant", monkeypatch)
    assert app.ADMIN_MODE is False


def test_modo_invalido_falha_alto(monkeypatch):
    with pytest.raises(RuntimeError, match="PANEL_MODE"):
        carregar("qualquer", monkeypatch)


def renderizar(app, **ctx):
    return app.templates.get_template("index.html").render(tenant_id="duda", agenda_disponivel=True, pedidos_disponivel=False, **ctx)


def test_menu_do_admin_so_tem_agenda_e_sair(monkeypatch):
    app, _ = carregar("admin", monkeypatch)
    html = renderizar(app, modo_admin=True)
    assert 'href="/agenda"' in html and 'href="/painel/logout"' in html
    for h in ('href="/vitrine"', 'href="/configuracoes"', 'href="/pedidos"'):
        assert h not in html


def test_menu_do_tenant_segue_completo(monkeypatch):
    app, _ = carregar("tenant", monkeypatch)
    html = renderizar(app, modo_admin=False)
    assert 'href="/vitrine"' in html and 'href="/configuracoes"' in html and 'href="/agenda"' in html
