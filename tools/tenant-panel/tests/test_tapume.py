"""Atendimento pausado (WHATSAPP_CLOUD_ENABLED=false): tapume.html.
Cobre o redesenho de 2026-11-04 (antes: emoji 🚧, faixa listrada, fonte do sistema, "desse tenant" e, na vitrine, o recado de reativar que e do dono
aparecia para o cliente final da loja)."""
import importlib
import re
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⚠]")


@pytest.fixture
def cli(monkeypatch):
    for k, v in {**ENV, "WHATSAPP_CLOUD_ENABLED": "false"}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    for nome in dir(app):
        if nome.endswith("_col"):
            monkeypatch.setattr(app, nome, db[getattr(app, nome).name])
    db["signups"].insert_one({"tenant_id": app.TENANT_ID, "status": "live", "config": {"nome_negocio": "Loja <Teste> & Cia"}})
    from starlette.testclient import TestClient
    return TestClient(app.app, follow_redirects=False)


@pytest.mark.parametrize("rota", ["/painel/login", "/painel", "/agenda", "/pedidos", "/configuracoes"])
def test_telas_do_dono_mostram_a_pausa_com_o_recado_de_reativar(cli, rota):
    html = cli.get(rota).text
    assert "<title>Atendimento pausado — Loja &lt;Teste&gt; &amp; Cia</title>" in html and "<Teste>" not in html
    assert "Fale com a AtendPraGente para reativar." in html and "desse tenant" not in html
    assert re.search(r'href="https://wa\.me/5511920081743\?text=[^"]*loja-teste', html)
    assert "Código da loja: loja-teste" in html


def test_vitrine_mostra_recado_para_o_cliente_final_e_nao_o_do_dono(cli):
    html = cli.get("/vitrine").text
    assert "Loja temporariamente indisponível" in html and "Tente de novo mais tarde" in html
    assert "AtendPraGente para reativar" not in html and "wa.me" not in html and "Código da loja" not in html


@pytest.mark.parametrize("rota", ["/painel/login", "/vitrine"])
def test_pausa_usa_a_base_comum_sem_emoji_listra_nem_fonte_embutida(cli, rota):
    html = cli.get(rota).text
    assert not EMOJIS.search(html) and "base64" not in html and "<style" not in html and "repeating-linear-gradient" not in html
    assert re.search(r'href="/painel/estilo/plataforma\.css\?v=[0-9a-f]{10}"', html) and re.search(r'href="/painel/estilo/acesso\.css\?v=[0-9a-f]{10}"', html)
    assert 'name="robots" content="noindex"' in html
