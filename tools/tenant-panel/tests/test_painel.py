"""Painel (/painel): caixa de conversas.
Cobre o redesenho de 2026-11-03 (antes: 77 KB de fonte em base64 por pagina, slug da loja com contorno de texto, emojis como icones,
alert()/confirm(), conversas como <div> sem teclado, lista refeita a cada 5 s, bolha com indentacao do HTML virando linhas em branco)."""
import importlib
import re
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ESTATICO = Path(__file__).resolve().parent.parent / "static"

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿⚠]")


def carregar(monkeypatch, config=None, **env):
    for k, v in {**ENV, **env}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    monkeypatch.setattr(app, "auth_col", db["panel_auth"])
    monkeypatch.setattr(app, "signups_col", db["signups"])
    db["signups"].insert_one({"tenant_id": app.TENANT_ID, "status": "live",
                              "config": {"nome_negocio": "Loja <Teste> & Cia", "agendamento_ativo": True, "pagamento_pix_ativo": True, **(config or {})}})
    h, s = app._hash_password("senha-de-teste-123")
    db["panel_auth"].insert_one({"_id": app.TENANT_ID, "username": "ana", "password_hash": h, "password_salt": s})
    from starlette.testclient import TestClient
    cli = TestClient(app.app, follow_redirects=False)
    assert cli.post("/painel/login", data={"username": "ana", "password": "senha-de-teste-123"}).status_code == 303
    return app, cli


@pytest.fixture
def cli(monkeypatch):
    return carregar(monkeypatch)[1]


def pagina(cli):
    r = cli.get("/painel")
    assert r.status_code == 200
    return r.text


def test_sem_sessao_vai_pro_login(monkeypatch):
    app, _ = carregar(monkeypatch)
    from starlette.testclient import TestClient
    r = TestClient(app.app, follow_redirects=False).get("/painel")
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/painel/login"


def test_pagina_enxuta_sem_fonte_nem_css_nem_js_embutidos(cli):
    html = pagina(cli)
    assert "base64" not in html and len(html) < 14_000
    assert "<style" not in html and not re.search(r"<script(?![^>]*\bsrc=)", html)
    for nome in ("plataforma.css", "painel.css", "painel.js"):
        assert re.search(rf'/painel/estilo/{re.escape(nome)}\?v=[0-9a-f]{{10}}', html), nome


def test_marca_com_o_nome_da_loja_escapado_e_sem_contorno(cli):
    html = pagina(cli)
    assert 'class="nome-loja">Loja &lt;Teste&gt; &amp; Cia<' in html and "<Teste>" not in html
    assert "<title>Conversas — Loja &lt;Teste&gt; &amp; Cia</title>" in html
    assert "text-stroke" not in (ESTATICO / "painel.css").read_text(encoding="utf-8")


def test_navegacao_do_tenant_tem_nome_acessivel_em_cada_icone(cli):
    html = pagina(cli)
    nav = re.search(r"<nav.*?</nav>", html, re.S).group(0)
    assert 'aria-label="Seções do painel"' in html
    for rotulo in ("Ver vitrine", "Agenda", "Pedidos", "Configurações"):
        assert f'aria-label="{rotulo}' in nav, rotulo
    assert 'href="/painel/logout"' in nav


def test_navegacao_respeita_o_que_a_loja_tem_ativo(monkeypatch):
    _, cli = carregar(monkeypatch, config={"agendamento_ativo": False, "pagamento_pix_ativo": False})
    nav = re.search(r"<nav.*?</nav>", pagina(cli), re.S).group(0)
    assert 'href="/agenda"' not in nav and 'href="/pedidos"' not in nav and 'href="/vitrine"' in nav


def test_painel_da_duda_nao_mostra_vitrine_nem_configuracoes(monkeypatch):
    _, cli = carregar(monkeypatch, TENANT_ID="duda", PANEL_MODE="admin")
    nav = re.search(r"<nav.*?</nav>", pagina(cli), re.S).group(0)
    assert 'href="/vitrine"' not in nav and 'href="/configuracoes"' not in nav and 'href="/painel/logout"' in nav


def test_sem_emoji_alert_nem_confirm_na_interface(cli):
    html = pagina(cli)
    css = (ESTATICO / "painel.css").read_text(encoding="utf-8")
    js = (ESTATICO / "painel.js").read_text(encoding="utf-8")
    for nome, texto in (("html", html), ("css", css), ("js", js)):
        assert not EMOJIS.search(texto), nome
    assert not re.search(r"\b(alert|confirm)\(", js)
    assert 'id="dlg-excluir"' in html and "showModal()" in js and 'role="alert"' in html


def test_partes_acessiveis_da_caixa_de_conversas(cli):
    html = pagina(cli)
    js = (ESTATICO / "painel.js").read_text(encoding="utf-8")
    assert 'id="conv-list" class="lista"' in html and 'role="progressbar"' in html and 'role="log"' in html
    assert 'aria-label="Voltar para a lista"' in html and 'aria-label="Excluir conversa"' in html and 'aria-label="Anexar imagem"' in html
    assert '<button type="button" class="conv' in js and "aria-current" in js      # conversa e botao (teclado), nao <div> clicavel
    assert re.search(r'composicao input\[type=text\]\s*\{[^}]*font-size:\s*1rem', (ESTATICO / "painel.css").read_text(encoding="utf-8"))


def test_contrato_com_a_api_e_com_o_localstorage(cli):
    js = (ESTATICO / "painel.js").read_text(encoding="utf-8")
    for trecho in ("/painel/api/sessions", "/painel/api/messages/", "/handoff", "/resolve", "/send-image", "/send'", "/painel/api/usage",
                   "painel-seen-", "/painel/login"):
        assert trecho in js, trecho


def test_lista_so_e_refeita_quando_muda_e_rolagem_respeita_quem_subiu(cli):
    js = (ESTATICO / "painel.js").read_text(encoding="utf-8")
    assert "lastListSignature" in js and "assinatura !== lastListSignature" in js
    assert "noFim" in js                       # so volta ao fim se a pessoa ja estava no fim


def test_balao_sem_pre_wrap_no_container_e_com_hora_colada_no_texto():
    css = (ESTATICO / "painel.css").read_text(encoding="utf-8")
    msg = re.search(r"\n\.msg \{([^}]*)\}", css).group(1)
    assert "pre-wrap" not in msg and "position: absolute" not in msg      # a indentacao do HTML nao vira linha em branco
    assert re.search(r"\.texto \{[^}]*pre-wrap", css)                       # so o texto da mensagem preserva as quebras de linha
    assert re.search(r"\.msg-rodape \{[^}]*margin-top:\s*0", css)         # pedido do dono: hora logo abaixo da mensagem


def test_arquivos_do_painel_servidos_com_cache_longo(cli):
    for nome, tipo in (("painel.css", "text/css"), ("painel.js", "text/javascript"), ("acesso.css", "text/css")):
        r = cli.get(f"/painel/estilo/{nome}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(tipo) and "immutable" in r.headers["cache-control"], nome


def test_nenhuma_pagina_depende_do_watermark_nem_de_fonte_base64():
    for f in ESTATICO.glob("*.css"):
        t = f.read_text(encoding="utf-8")
        assert "thread-watermark" not in t and "base64" not in t, f.name
