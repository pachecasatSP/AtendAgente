"""Motor de comissão (store.py): faixa travada na atribuição e atribuição única por tenant."""
import pytest

from app import store


def novo_vendedor(nome="Ana"):
    return store.create_vendedor(nome, f"{nome}@x.com", "", nome.lower(), "senha-de-teste-123")


def test_atribuicao_trava_taxa_da_faixa_e_normaliza_o_plano(db):
    vid = novo_vendedor()
    doc = store.atribuir_tenant(vid, "loja-a", "comecando", 157.90)

    assert doc["tenant_id"] == "loja-a"
    assert doc["plano_na_atribuicao"] == "Começando"  # nome de tabela, não a chave do onboarding
    assert doc["taxa_travada"] == 0.12
    assert doc["tier_na_atribuicao"] == "Início"
    assert doc["comissao_mensal"] == 18.95  # 157,90 * 12%
    assert doc["ativo"] is True
    assert db["atribuicoes_comissao"].count_documents({}) == 1
    assert db["atribuicoes_comissao"].find_one({"tenant_id": "loja-a"})["vendedor_id"] == vid


def test_mesmo_tenant_nao_e_atribuido_duas_vezes(db):
    vid = novo_vendedor()
    primeira = store.atribuir_tenant(vid, "loja-a", "comecando", 157.90)
    segunda = store.atribuir_tenant(vid, "loja-a", "comecando", 157.90)

    assert primeira is not None
    assert segunda is None
    assert db["atribuicoes_comissao"].count_documents({"tenant_id": "loja-a"}) == 1
    assert store.comissao_do_mes(vid)["total"] == 18.95  # sem comissão em dobro


def test_primeiro_toque_vale_entre_vendedores(db):
    ana, beto = novo_vendedor("Ana"), novo_vendedor("Beto")
    assert store.atribuir_tenant(ana, "loja-a", "entrada", 84.90) is not None
    assert store.atribuir_tenant(beto, "loja-a", "entrada", 84.90) is None
    assert store.get_atribuicao("loja-a")["vendedor_id"] == ana
    assert store.contar_clientes_ativos(beto) == 0


def test_atribuicao_encerrada_continua_bloqueando_nova_atribuicao(db):
    """Cliente que cancelou e voltou não vira comissão nova de outro vendedor por acaso."""
    ana, beto = novo_vendedor("Ana"), novo_vendedor("Beto")
    store.atribuir_tenant(ana, "loja-a", "entrada", 84.90)
    store.encerrar_atribuicao("loja-a")
    assert store.atribuir_tenant(beto, "loja-a", "entrada", 84.90) is None


def test_taxa_travada_nao_retroage_quando_o_vendedor_sobe_de_faixa(db):
    vid = novo_vendedor()
    for i in range(10):
        store.atribuir_tenant(vid, f"loja-{i}", "comecando", 157.90)
    decimo_primeiro = store.atribuir_tenant(vid, "loja-10", "comecando", 157.90)

    assert decimo_primeiro["tier_na_atribuicao"] == "Crescimento"
    assert decimo_primeiro["taxa_travada"] == 0.17
    antigos = db["atribuicoes_comissao"].find({"tenant_id": {"$ne": "loja-10"}})
    assert {a["taxa_travada"] for a in antigos} == {0.12}  # os 10 primeiros continuam em 12%


def test_desfazer_atribuicao_libera_o_tenant(db):
    vid = novo_vendedor()
    doc = store.atribuir_tenant(vid, "loja-a", "comecando", 157.90)
    store.desfazer_atribuicao(doc["_id"])

    assert store.get_atribuicao("loja-a") is None
    assert store.contar_clientes_ativos(vid) == 0
    assert store.atribuir_tenant(vid, "loja-a", "comecando", 157.90) is not None


@pytest.mark.parametrize(
    "qtd, tier, taxa",
    [(1, "Início", 0.12), (10, "Início", 0.12), (11, "Crescimento", 0.17), (15, "Crescimento", 0.17),
     (16, "Consolidado", 0.28), (49, "Consolidado", 0.28), (50, "Parceiro-chave", 0.27)],
)
def test_faixas_de_comissao(qtd, tier, taxa):
    assert store.faixa_para(qtd)["tier"] == tier
    assert store.faixa_para(qtd)["taxa"] == taxa
