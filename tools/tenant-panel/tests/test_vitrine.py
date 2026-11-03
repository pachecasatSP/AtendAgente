"""Vitrine publica (/vitrine): layout lista/grade, chips de categoria, abas por tipo, estados e o contrato do pedido.
Cobre o redesenho de 2026-11-02 (antes: emojis na interface, preco 'R$ 990.00', descricao igual ao nome, caixa de rolagem em cada card)."""
import importlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


@pytest.fixture
def ctx(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app", None)
    import app
    importlib.reload(app)
    db = mongomock.MongoClient().db
    monkeypatch.setattr(app, "catalogo_col", db["catalogo_itens"])
    monkeypatch.setattr(app, "signups_col", db["signups"])
    monkeypatch.setattr(app, "_numero_whatsapp_tenant", lambda: None)
    db["signups"].insert_one({"tenant_id": "loja-teste", "status": "live", "config": {"nome_negocio": "Loja Teste"}})
    from starlette.testclient import TestClient
    return app, db, TestClient(app.app)


def novo(db, nome, preco, cat="Bolsas", tipo="produto", foto="https://cdn/x.jpg", descricao="", **extra):
    db["catalogo_itens"].insert_one({"tenant_id": "loja-teste", "nome": nome, "slug": nome.lower().replace(" ", "_"), "descricao": descricao, "preco": preco,
                                     "categoria": cat, "tipo": tipo, "ativo": True, "foto_url": foto, "data_evento": "", "horario_atendimento": "",
                                     "criado_em": datetime.now(timezone.utc), **extra})


def pagina(cli, qs=""):
    r = cli.get("/vitrine" + qs)
    assert r.status_code == 200
    return r.text


def test_preco_em_formato_brasileiro_e_sem_ponto_decimal(ctx):
    _, db, cli = ctx
    novo(db, "Mala", 990.0)
    novo(db, "Relogio", 1234.5, cat="Acessorios")
    html = pagina(cli)
    assert "R$ 990,00" in html and "R$ 1.234,50" in html
    assert "990.00" not in html and "R$ 990.0" not in html


def test_sem_emoji_na_interface(ctx):
    _, db, cli = ctx
    novo(db, "Sem foto", 10, foto=None)
    novo(db, "Show", 80, tipo="evento", data_evento="2026-12-24", cat="Eventos")
    novo(db, "Corte", 50, tipo="servico", horario_atendimento="seg a sex, 9h as 18h", cat="Servicos")
    assert not EMOJIS.search(pagina(cli))


def test_layout_padrao_e_lista_e_aceita_grade_pela_config_e_pela_query(ctx):
    app, db, cli = ctx
    novo(db, "Mala", 990.0)
    assert 'data-layout="lista"' in pagina(cli)
    assert 'data-layout="grade"' in pagina(cli, "?layout=grade")
    assert 'data-layout="lista"' in pagina(cli, "?layout=qualquer-coisa")
    db["signups"].update_one({"tenant_id": "loja-teste"}, {"$set": {"config.vitrine_layout": "grade"}})
    assert 'data-layout="grade"' in pagina(cli)
    assert 'data-layout="lista"' in pagina(cli, "?layout=lista")  # a query vence a config (so pra testar)


def test_chips_de_categoria_so_quando_ha_mais_de_uma(ctx):
    _, db, cli = ctx
    novo(db, "Mala", 990.0, cat="Bolsas")
    assert 'class="cats"' not in pagina(cli)
    novo(db, "Bota", 595.0, cat="Botas")
    html = pagina(cli)
    assert 'class="cats"' in html and ">Bolsas</a>" in html and ">Botas</a>" in html
    assert 'class="tipo com-cats"' in html


def test_abas_por_tipo_so_quando_ha_mais_de_um_tipo(ctx):
    _, db, cli = ctx
    novo(db, "Mala", 990.0)
    assert 'class="aba"' not in pagina(cli)
    novo(db, "Show", 80, tipo="evento", data_evento="2026-12-24", cat="Eventos")
    novo(db, "Corte", 50, tipo="servico", cat="Servicos")
    html = pagina(cli)
    assert html.count('class="aba"') == 3 and "Produtos<small>1</small>" in html and "Eventos<small>1</small>" in html and "Servi" in html


def test_servico_nao_tem_botao_de_pedido_e_produto_e_evento_tem(ctx):
    _, db, cli = ctx
    novo(db, "Mala", 990.0)
    novo(db, "Show", 80, tipo="evento", data_evento="2026-12-24", cat="Eventos")
    novo(db, "Corte", 50, tipo="servico", horario_atendimento="seg a sex, 9h as 18h", cat="Servicos")
    html = pagina(cli)
    assert html.count("data-item-id=") == 2
    assert 'data-item-nome="Mala"' in html and 'data-item-nome="Show"' in html and 'data-item-nome="Corte"' not in html
    assert "Data: 24/12/2026" in html and "Atendimento: seg a sex, 9h as 18h" in html


def test_descricao_igual_ao_nome_some_curta_aparece_e_longa_vira_detalhes(ctx):
    _, db, cli = ctx
    novo(db, "Mala Camel", 990.0, descricao="Mala Camel")
    novo(db, "Bolsa", 225.0, descricao="Couro legitimo, forro de algodao")
    novo(db, "Bota", 595.0, cat="Botas", descricao="Bota de couro. " * 12)
    html = pagina(cli)
    assert 'class="desc">Mala Camel' not in html
    assert 'class="desc">Couro legitimo, forro de algodao</p>' in html
    assert html.count("<details") == 1 and "Ver detalhes" in html


def test_item_sem_foto_mostra_iniciais_e_loja_sem_nenhuma_foto_nao_reserva_coluna(ctx):
    _, db, cli = ctx
    novo(db, "Mala Maternidade", 990.0)
    novo(db, "Bolsa Preta", 225.0, foto=None)
    html = pagina(cli)
    assert 'class="foto vazia"' in html and ">BP<" in html and "<img" in html
    assert 'class="sem-fotos"' not in html
    db["catalogo_itens"].update_many({}, {"$set": {"foto_url": None}})
    html = pagina(cli)
    assert 'class="sem-fotos"' in html and "<img" not in html.split("</style>")[1]


def test_loja_vazia_mostra_estado_e_so_oferece_whatsapp_quando_ha_numero(ctx, monkeypatch):
    app, db, cli = ctx
    html = pagina(cli)
    assert "Ainda não há produtos por aqui" in html and "wa.me" not in html
    monkeypatch.setattr(app, "_numero_whatsapp_tenant", lambda: "5511999990000")
    assert 'href="https://wa.me/5511999990000"' in pagina(cli)


def test_nome_com_html_e_escapado(ctx):
    _, db, cli = ctx
    novo(db, '<img src=x onerror=alert(1)>', 10.0)
    html = pagina(cli)
    assert "<img src=x onerror" not in html and "&lt;img src=x onerror" in html


def test_contrato_do_pedido_continua_igual(ctx):
    _, db, cli = ctx
    novo(db, "Mala", 990.0)
    html = pagina(cli)
    assert "const CARRINHO_KEY = 'atendpragente_carrinho_loja-teste'" in html  # mesma chave: carrinho de quem ja estava comprando nao se perde
    assert "fetch('/vitrine/api/pedido'" in html and "JSON.stringify({ item_ids:" in html and "data.wa_link" in html
    assert "alert(" not in html


def test_fontes_da_marca_sao_servidas_com_cache_e_so_as_da_lista(ctx):
    _, _, cli = ctx
    for nome in ("baloo2-800.woff2", "nunito-var.woff2"):
        r = cli.get(f"/vitrine/fonte/{nome}")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
        assert "immutable" in r.headers["cache-control"] and r.content[:4] == b"wOF2"
    for ruim in ("app.py", "..%2Fapp.py", "..%5Capp.py", "baloo2-800.woff", "%2e%2e%2fapp.py"):
        assert cli.get(f"/vitrine/fonte/{ruim}").status_code == 404, ruim


def test_formatadores(ctx):
    app, _, _ = ctx
    assert app.fmt_brl(0) == "R$ 0,00" and app.fmt_brl(None) == "R$ 0,00" and app.fmt_brl("abc") == "R$ 0,00"
    assert app.fmt_brl(1234567.891) == "R$ 1.234.567,89"
    assert app.iniciais("Mala Maternidade Camel") == "MM" and app.iniciais("A B") == "A" and app.iniciais("") == "?" and app.iniciais("Tênis de Couro") == "TC"
