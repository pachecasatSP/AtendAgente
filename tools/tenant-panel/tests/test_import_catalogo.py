"""Reimportar a planilha do catalogo NAO duplica: a linha cujo nome ja existe atualiza o item.
Caso real (linda-ana-calcados, 2026-11-02): reimportar a mesma planilha criou 10 itens novos (slug -2, sem foto) ao lado dos 10 que ja tinham foto."""
import importlib
import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {"TENANT_ID": "loja-teste", "MONGO_URI": "mongodb://127.0.0.1:1/teste", "PANEL_SETUP_TOKEN": "x",
       "PANEL_SESSION_SECRET": "0123456789abcdef0123456789abcdef", "CALENDAR_MCP_TOKEN": "t", "PANEL_MODE": "tenant"}
CSV = ("nome,descricao,preco,categoria,tipo\n"
       "Sandália Salto Médio Preta,Sandália preta,220,Sandálias,produto\n"
       "Bolsa Matelassê Shoulder,Bolsa de ombro,\"225,50\",Bolsas,produto\n")


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
    monkeypatch.setattr(app, "require_session", lambda request: "admin")
    db["signups"].insert_one({"tenant_id": "loja-teste", "status": "live", "config": {"catalogo_ativo": True}})
    from starlette.testclient import TestClient
    return db, TestClient(app.app)


def importar(cli, texto):
    return cli.post("/painel/api/catalogo/import", files={"file": ("catalogo.csv", texto.encode("utf-8"), "text/csv")})


def pendente(db):
    return bool(db["signups"].find_one({"tenant_id": "loja-teste"}).get("catalogo_pending"))


def itens(db):
    return list(db["catalogo_itens"].find({"tenant_id": "loja-teste"}).sort("criado_em", 1))


def test_primeira_importacao_cria_os_itens(ctx):
    db, cli = ctx
    r = importar(cli, CSV)
    assert r.status_code == 200
    j = r.json()
    assert [c["slug"] for c in j["criados"]] == ["sandalia_salto_medio_preta", "bolsa_matelasse_shoulder"]
    assert j["atualizados"] == [] and j["sem_alteracao"] == []
    assert len(itens(db)) == 2 and pendente(db)
    assert itens(db)[1]["preco"] == 225.5


def test_reimportar_a_mesma_planilha_nao_duplica_nem_republica(ctx):
    db, cli = ctx
    importar(cli, CSV)
    db["signups"].update_one({"tenant_id": "loja-teste"}, {"$set": {"catalogo_pending": False}})
    j = importar(cli, CSV).json()
    assert j["criados"] == [] and j["atualizados"] == []
    assert [c["slug"] for c in j["sem_alteracao"]] == ["sandalia_salto_medio_preta", "bolsa_matelasse_shoulder"]
    assert len(itens(db)) == 2
    assert not pendente(db), "nada mudou: nao ha o que republicar"


def test_caso_real_dez_itens_com_foto_reimportados_continuam_dez_com_foto(ctx):
    db, cli = ctx
    nomes = [f"Produto {i}" for i in range(10)]
    planilha = "nome,preco\n" + "".join(f"{n},{100 + i}\n" for i, n in enumerate(nomes))
    importar(cli, planilha)
    for it in itens(db):
        db["catalogo_itens"].update_one({"_id": it["_id"]}, {"$set": {"foto_url": f"https://cdn/{it['slug']}.jpg"}})
    j = importar(cli, planilha).json()
    assert len(j["sem_alteracao"]) == 10 and j["criados"] == []
    todos = itens(db)
    assert len(todos) == 10
    assert all(it["foto_url"] for it in todos) and not any(it["slug"].endswith("-2") for it in todos)


def test_preco_novo_atualiza_o_mesmo_item_e_preserva_slug_foto_e_ativo(ctx):
    db, cli = ctx
    importar(cli, CSV)
    antes = itens(db)[0]
    db["catalogo_itens"].update_one({"_id": antes["_id"]}, {"$set": {"foto_url": "https://cdn/x.jpg", "ativo": False}})
    db["signups"].update_one({"tenant_id": "loja-teste"}, {"$set": {"catalogo_pending": False}})
    j = importar(cli, CSV.replace(",220,", ",239,")).json()  # preco 220 -> 239
    assert len(j["atualizados"]) == 1 and j["atualizados"][0]["slug"] == "sandalia_salto_medio_preta"
    depois = itens(db)[0]
    assert depois["_id"] == antes["_id"] and len(itens(db)) == 2
    assert depois["preco"] == 239.0
    assert depois["slug"] == antes["slug"] and depois["foto_url"] == "https://cdn/x.jpg" and depois["ativo"] is False
    assert depois["atualizado_em"] >= antes["atualizado_em"] and pendente(db)


def test_nome_com_outra_caixa_e_acento_casa_com_o_existente(ctx):
    db, cli = ctx
    importar(cli, CSV)
    j = importar(cli, "nome,preco\nSANDALIA SALTO MEDIO PRETA,220\n").json()
    assert j["criados"] == [] and len(j["atualizados"]) == 1  # so o nome muda (grafia da planilha)
    assert len(itens(db)) == 2 and itens(db)[0]["nome"] == "SANDALIA SALTO MEDIO PRETA"
    assert itens(db)[0]["slug"] == "sandalia_salto_medio_preta"


def test_planilha_so_com_nome_e_preco_nao_apaga_descricao_nem_categoria(ctx):
    db, cli = ctx
    importar(cli, CSV)
    importar(cli, "nome,preco\nSandália Salto Médio Preta,250\n")
    it = itens(db)[0]
    assert it["preco"] == 250.0 and it["descricao"] == "Sandália preta" and it["categoria"] == "Sandálias"


def test_nomes_repetidos_dentro_da_planilha_continuam_sendo_itens_distintos(ctx):
    db, cli = ctx
    j = importar(cli, "nome,preco\nCamiseta,50\nCamiseta,60\n").json()
    assert [c["slug"] for c in j["criados"]] == ["camiseta", "camiseta-2"]
    # ja existindo 1 "Camiseta": a 1a linha atualiza, a 2a cria o segundo
    j2 = importar(cli, "nome,preco\nCamiseta,55\nCamiseta,65\nCamiseta,75\n").json()
    assert len(j2["atualizados"]) == 2 and [c["slug"] for c in j2["criados"]] == ["camiseta-3"]
    assert len(itens(db)) == 3


def test_troca_de_tipo_recalcula_os_campos_do_tipo(ctx):
    db, cli = ctx
    importar(cli, "nome,preco,tipo,data_evento\nFeira,10,evento,2026-12-24\n")
    assert itens(db)[0]["data_evento"] == "2026-12-24"
    importar(cli, "nome,preco,tipo\nFeira,10,produto\n")
    it = itens(db)[0]
    assert it["tipo"] == "produto" and it["data_evento"] == ""


def test_planilha_sem_linha_valida_continua_dando_400(ctx):
    _, cli = ctx
    assert importar(cli, "nome,preco\n,10\n").status_code == 400
