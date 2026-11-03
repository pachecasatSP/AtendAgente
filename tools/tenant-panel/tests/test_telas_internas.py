"""Agenda, pedidos e configuracoes (telas internas): base comum plataforma.css + interno.css, sem alert()/confirm(), sem emoji, sem fonte embutida.
Cobre o redesenho de 2026-11-03 (antes: CSS inline de ~170 linhas em cada uma, `var(--font-display)` sem @font-face -> fonte do sistema, 15 alert()/confirm(),
emojis como icones, preco 'R$ 1215,00' sem milhar, 'hoje' calculado em UTC, evento agrupado pelo dia UTC, botao dentro de link na agenda)."""
import importlib
import re
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ESTATICO = Path(__file__).resolve().parent.parent / "static"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⚠]")
PAGINAS = {"/agenda": "agenda", "/pedidos": "pedidos", "/configuracoes": "configuracoes"}


def ler(nome):
    return (ESTATICO / nome).read_text(encoding="utf-8")


def carregar(monkeypatch, **env):
    for k, v in {**ENV, **env}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    for nome in dir(app):
        if nome.endswith("_col"):
            monkeypatch.setattr(app, nome, db[getattr(app, nome).name])
    db["signups"].insert_one({"tenant_id": app.TENANT_ID, "status": "live",
                              "config": {"nome_negocio": "Loja <Teste> & Cia", "agendamento_ativo": True, "pagamento_pix_ativo": True}})
    h, s = app._hash_password("senha-de-teste-123")
    db["panel_auth"].insert_one({"_id": app.TENANT_ID, "username": "ana", "password_hash": h, "password_salt": s})
    db["catalogo_itens"].insert_one({"tenant_id": app.TENANT_ID, "nome": "Sandália Dourada", "slug": "sandalia", "descricao": "", "preco": 1215.5, "categoria": "Calçados",
                                     "tipo": "produto", "ativo": True, "foto_url": None, "data_evento": "", "horario_atendimento": ""})
    db["catalogo_itens"].insert_one({"tenant_id": app.TENANT_ID, "nome": "Feira", "slug": "feira", "descricao": "", "preco": 40.0, "categoria": "Eventos",
                                     "tipo": "evento", "ativo": True, "foto_url": None, "data_evento": "2026-12-12", "horario_atendimento": ""})
    from starlette.testclient import TestClient
    cli = TestClient(app.app, follow_redirects=False)
    assert cli.post("/painel/login", data={"username": "ana", "password": "senha-de-teste-123"}).status_code == 303
    return app, cli


@pytest.fixture
def cli(monkeypatch):
    return carregar(monkeypatch)[1]


@pytest.mark.parametrize("rota", list(PAGINAS))
def test_sem_sessao_vai_pro_login(monkeypatch, rota):
    app, _ = carregar(monkeypatch)
    from starlette.testclient import TestClient
    r = TestClient(app.app, follow_redirects=False).get(rota)
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/painel/login"


@pytest.mark.parametrize("rota,nome", list(PAGINAS.items()))
def test_usa_a_base_comum_e_nao_embute_nada(cli, rota, nome):
    html = cli.get(rota).text
    assert "base64" not in html and "<style" not in html
    for css in ("plataforma.css", "interno.css", f"{nome}.css"):
        href = re.search(rf'href="(/painel/estilo/{re.escape(css)}\?v=[0-9a-f]{{10}})"', html)
        assert href, css
        assert cli.get(href.group(1)).status_code == 200
    assert 'class="interno"' in html
    assert "<title>" in html and "Loja &lt;Teste&gt; &amp; Cia</title>" in html and "<Teste>" not in html
    assert 'class="topo-loja">Loja &lt;Teste&gt; &amp; Cia<' in html and 'href="/painel"' in html
    assert "brand-word" not in html and "text-stroke" not in html


@pytest.mark.parametrize("rota,nome", list(PAGINAS.items()))
def test_sem_emoji_alert_nem_confirm(cli, rota, nome):
    html = cli.get(rota).text
    js = ler(f"{nome}.js") if (ESTATICO / f"{nome}.js").exists() else ""
    css = ler(f"{nome}.css")
    for onde, texto in (("html", html), ("js", js), ("css", css), ("interno.css", ler("interno.css"))):
        assert not EMOJIS.search(texto), (rota, onde, EMOJIS.search(texto).group(0))
        assert not re.search(r"(?<![\w.])(alert|confirm)\(", texto), (rota, onde)


def test_fontes_da_marca_carregam_nas_telas_internas(cli):
    for rota in PAGINAS:
        html = cli.get(rota).text
        assert "/painel/fonte/baloo2-800.woff2" in html and "/painel/fonte/nunito-var.woff2" in html, rota
    base = ler("plataforma.css")
    assert "@font-face" in base and "/painel/fonte/baloo2-800.woff2" in base


def test_helper_de_aviso_e_confirmacao():
    js = ler("interno.js")
    assert "window.avisar" in js and "window.confirmar" in js and "showModal()" in js and "'role', 'alert'" in js
    assert "cancelar.autofocus = true" in js           # o foco inicial fica em "Cancelar", nunca na acao destrutiva


def test_arquivos_novos_servidos_com_cache_longo(cli):
    for nome in ("interno.css", "interno.js", "agenda.css", "agenda.js", "pedidos.css", "pedidos.js", "configuracoes.css"):
        r = cli.get(f"/painel/estilo/{nome}")
        assert r.status_code == 200 and "immutable" in r.headers["cache-control"], nome
    assert cli.get("/painel/estilo/configuracoes.js").status_code == 404           # so o que esta na lista fixa


def test_agenda_agrupa_pelo_dia_de_sao_paulo_e_sem_botao_dentro_de_link():
    js = ler("agenda.js")
    assert "fmtDiaSP.format(new Date(ev.inicio))" in js                  # 22h em Brasilia chega como 01h UTC do dia seguinte
    assert ".toISOString(" not in js and "ev.inicio.slice" not in js and "localeCompare" not in js   # nada de dia por texto UTC
    assert "createElement('a')" in js and "'/painel/api/agenda/confirmar'" in js and "/painel/api/agenda/eventos?semana=" in js
    assert "evento-link" in js and "chip.appendChild" not in js
    css = ler("agenda.css")
    assert "repeat(7, minmax(0, 1fr))" in css and "min-width: 910px" not in css      # sem rolagem horizontal; celular = um dia por linha


def test_pedidos_contrato_e_formato_brasileiro():
    js = ler("pedidos.js")
    for trecho in ("/painel/api/pedidos?offset=", "/rastreio-visto", "/avancar-rastreio", "/endereco", "/comprovante", "data-acao=\"pago\"", "data-acao=\"cancelar\"",
                   "IntersectionObserver", "setInterval(verificarAtualizacoes, 15000)", "Intl.NumberFormat('pt-BR'", "currency: 'BRL'"):
        assert trecho in js, trecho
    assert "toFixed" not in js and "showModal()" in js


def test_configuracoes_secoes_miniaturas_e_preco(cli):
    html = cli.get("/configuracoes").text
    for sec in ("whatsapp", "atendente", "catalogo", "contatos", "agenda", "assinatura"):
        assert f'id="{sec}"' in html and f'href="#{sec}"' in html, sec
    assert "R$ 1.215,50" in html and "1215.50" not in html and "R$ 1215" not in html
    assert 'class="produto-thumb" aria-hidden="true">SD<' in html                       # iniciais no lugar do emoji da miniatura
    assert "Data: 12/12/2026" in html and "tenant-tag" not in html
    assert html.count("Ajudar a escrever</button>") == 3 and "✨" not in html


def test_configuracoes_js_usa_o_helper_e_escapa_os_numeros(cli):
    html = cli.get("/configuracoes").text
    assert "await confirmar(" in html and html.count("await confirmar(") == 3 and "avisar(err.message)" in html
    assert "perigo: true" in html                                                         # excluir e cancelar assinatura sao acoes destrutivas
    assert "escapeHtml(n.display_phone_number" in html and "style.cssText" not in html
    for rota in ("/painel/api/configuracoes", "/painel/api/catalogo", "/painel/api/whatsapp/confirmar-troca", "/painel/api/cancelar-assinatura", "/painel/api/agenda"):
        assert rota in html, rota


def test_seletor_de_arquivo_continua_acessivel_pelo_teclado():
    css = ler("configuracoes.css")
    assert not re.search(r"type=file\]\s*\{[^}]*display:\s*none", css)                   # display:none tirava o campo do alcance do teclado
    assert "focus-within" in css and "opacity: 0" in css


def test_campos_de_16px_e_botao_padrao_com_especificidade_zero():
    css = ler("interno.css")
    assert re.search(r"input\[type=text\][^{]*\{[^}]*font-size:\s*1rem", css)
    assert ":where(body.interno) button" in css and "text-stroke" not in css


def test_telas_internas_funcionam_no_painel_da_duda(monkeypatch):
    _, cli = carregar(monkeypatch, TENANT_ID="duda", PANEL_MODE="admin")
    assert cli.get("/agenda").status_code == 200
    assert cli.get("/painel/estilo/agenda.js").status_code == 200
