"""Rotas de atribuição de tenant (manual e por link de vendedor) ponta a ponta,
com o onboarding-service de mentira (conftest.OnboardingFalso)."""
import httpx

from app import store
from conftest import tenant


def lead_do(vendedor_id, negocio="Loja A"):
    return store.create_lead(negocio, "22999990000", "Cabo Frio", "teste", vendedor_id)


def atribuir(cliente, lead_id, tenant_id="loja-a"):
    return cliente.post(f"/leads/{lead_id}/atribuir-tenant", data={"tenant_id": tenant_id})


# ------------------------------------------------------------ atribuição manual

def test_atribuicao_manual_com_plano_em_minusculas_funciona(novo_cliente, onboarding, db):
    """Regressão: signups.plano = 'comecando' nunca achava preço e devolvia 400."""
    cliente, vid = novo_cliente("ana")
    onboarding.tenants = [tenant("loja-a", "comecando")]
    lead = lead_do(vid)

    resposta = atribuir(cliente, lead)

    assert resposta.status_code == 303
    atribuicao = store.get_atribuicao("loja-a")
    assert atribuicao["vendedor_id"] == vid
    assert atribuicao["comissao_mensal"] == 18.95
    assert store.get_lead(lead)["status"] == "fechado"
    assert onboarding.atribuicoes == [("loja-a", vid, f"lead:{lead}")]


def test_plano_sem_preco_nao_grava_nada_nem_marca_o_onboarding(novo_cliente, onboarding, db):
    """Antes o onboarding era marcado ANTES da checagem de preço e o tenant ficava preso a meio caminho."""
    cliente, vid = novo_cliente("ana")
    onboarding.tenants = [tenant("loja-a", "gratuito", gratuito=True)]
    lead = lead_do(vid)

    resposta = atribuir(cliente, lead)

    assert resposta.status_code == 400
    assert store.get_atribuicao("loja-a") is None
    assert onboarding.atribuicoes == []
    assert store.get_lead(lead)["status"] == "novo"


def test_clique_duplo_nao_duplica_comissao(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    onboarding.tenants = [tenant("loja-a", "comecando")]
    lead = lead_do(vid)

    primeira = atribuir(cliente, lead)
    segunda = atribuir(cliente, lead)

    assert (primeira.status_code, segunda.status_code) == (303, 303)
    assert db["atribuicoes_comissao"].count_documents({"tenant_id": "loja-a"}) == 1
    assert store.comissao_do_mes(vid)["total"] == 18.95
    assert len(onboarding.atribuicoes) == 1  # o onboarding só é chamado na primeira vez


def test_tenant_de_outro_vendedor_e_recusado(novo_cliente, onboarding, db):
    ana, ana_id = novo_cliente("ana")
    beto, beto_id = novo_cliente("beto")
    onboarding.tenants = [tenant("loja-a", "comecando")]
    assert atribuir(ana, lead_do(ana_id)).status_code == 303

    lead_beto = lead_do(beto_id, "Loja do Beto")
    resposta = atribuir(beto, lead_beto)

    assert resposta.status_code == 409
    assert store.get_atribuicao("loja-a")["vendedor_id"] == ana_id
    assert store.get_lead(lead_beto)["status"] == "novo"
    assert store.contar_clientes_ativos(beto_id) == 0


def test_tenant_ja_marcado_para_outro_vendedor_no_onboarding_e_recusado(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_id="outro-vendedor")]

    resposta = atribuir(cliente, lead_do(vid))

    assert resposta.status_code == 409
    assert store.get_atribuicao("loja-a") is None
    assert onboarding.atribuicoes == []


def test_onboarding_fora_do_ar_desfaz_a_atribuicao_e_permite_tentar_de_novo(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    onboarding.tenants = [tenant("loja-a", "comecando")]
    lead = lead_do(vid)
    onboarding.erro = httpx.ConnectError("fora do ar")

    falha = atribuir(cliente, lead)

    assert falha.status_code == 502
    assert store.get_atribuicao("loja-a") is None  # nada de comissão sem vendedor registrado lá
    assert store.get_lead(lead)["status"] == "novo"

    onboarding.erro = None
    assert atribuir(cliente, lead).status_code == 303
    assert store.get_atribuicao("loja-a")["vendedor_id"] == vid
    assert store.get_lead(lead)["status"] == "fechado"


def test_lead_de_outro_vendedor_nao_pode_ser_usado(novo_cliente, onboarding, db):
    ana, ana_id = novo_cliente("ana")
    beto, _ = novo_cliente("beto")
    onboarding.tenants = [tenant("loja-a", "comecando")]

    assert atribuir(beto, lead_do(ana_id)).status_code == 404
    assert store.get_atribuicao("loja-a") is None


def test_tenant_inexistente_no_onboarding(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    assert atribuir(cliente, lead_do(vid), "nao-existe").status_code == 404


def test_atribuicao_pelo_link_e_depois_pelo_lead_fecha_o_lead_sem_comissao_nova(novo_cliente, onboarding, db):
    """O link pessoal atribuiu antes; o vendedor ainda fecha o lead na mão — vale, sem pagar duas vezes."""
    admin, _ = novo_cliente("chefe", role="admin")
    cliente, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_slug_hint=slug)]
    assert admin.get("/admin/tenants").status_code == 200  # reconciliação atribui

    lead = lead_do(vid)
    assert atribuir(cliente, lead).status_code == 303

    assert db["atribuicoes_comissao"].count_documents({"tenant_id": "loja-a"}) == 1
    assert store.get_lead(lead)["status"] == "fechado"


# ------------------------------------------------- atribuição por link de vendedor

def test_reconciliacao_atribui_tenant_do_link_com_plano_em_minusculas(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_slug_hint=slug)]

    pagina = admin.get("/admin/tenants")

    assert pagina.status_code == 200
    assert "atribuído(s) automaticamente" in pagina.text
    assert store.get_atribuicao("loja-a")["vendedor_id"] == vid
    assert onboarding.atribuicoes == [("loja-a", vid, "link-vendedor")]


def test_reconciliacao_nao_duplica_ao_recarregar(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_slug_hint=slug)]

    admin.get("/admin/tenants")
    admin.get("/admin/tenants")  # o onboarding de mentira não marca vendedor_id: o store é que segura

    assert db["atribuicoes_comissao"].count_documents({}) == 1
    assert len(onboarding.atribuicoes) == 1


def test_reconciliacao_pula_plano_sem_preco_e_slug_desconhecido(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [
        tenant("gratis", "gratuito", gratuito=True, vendedor_slug_hint=slug),
        tenant("sem-dono", "comecando", vendedor_slug_hint="slug-que-nao-existe"),
    ]

    assert admin.get("/admin/tenants").status_code == 200
    assert db["atribuicoes_comissao"].count_documents({}) == 0
    assert onboarding.atribuicoes == []


def test_reconciliacao_com_onboarding_fora_do_ar_nao_derruba_a_pagina_e_tenta_de_novo(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_slug_hint=slug)]
    onboarding.erro = httpx.ConnectError("fora do ar")

    assert admin.get("/admin/tenants").status_code == 200
    assert db["atribuicoes_comissao"].count_documents({}) == 0  # desfeita

    onboarding.erro = None
    assert admin.get("/admin/tenants").status_code == 200
    assert store.get_atribuicao("loja-a")["vendedor_id"] == vid


def test_reconciliacao_ignora_tenant_que_ja_tem_vendedor(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    slug = store.get_vendedor(vid)["slug"]
    onboarding.tenants = [tenant("loja-a", "comecando", vendedor_slug_hint=slug, vendedor_id="outro")]

    admin.get("/admin/tenants")

    assert db["atribuicoes_comissao"].count_documents({}) == 0
    assert onboarding.atribuicoes == []
