"""Churn: a comissão acompanha o ciclo de vida do tenant (cancelou → para; restaurou → volta)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import churn, store
from conftest import tenant


def vendedor(nome="ana"):
    return store.create_vendedor(nome.title(), f"{nome}@x.com", "", nome, "senha-de-teste-123")


def atribuir(vid, tenant_id="loja-a", plano="comecando", dias_atras=0):
    atribuicao = store.atribuir_tenant(vid, tenant_id, plano, store.preco_do_plano(plano))
    if dias_atras:
        store._atribuicoes.update_one(
            {"_id": atribuicao["_id"]}, {"$set": {"atribuido_em": datetime.now(timezone.utc) - timedelta(days=dias_atras)}}
        )
    return atribuicao


# ------------------------------------------------------------ store.encerrar_atribuicao

def test_encerrar_registra_churn_e_para_de_contar(db):
    vid = vendedor()
    atribuir(vid)
    assert store.comissao_do_mes(vid)["total"] == 18.95

    encerrada = store.encerrar_atribuicao("loja-a", "cancelado")

    assert encerrada["ativo"] is False
    assert encerrada["motivo_encerramento"] == "cancelado"
    assert encerrada["dentro_clawback"] is True
    assert store.comissao_do_mes(vid) == {"total": 0.0, "qtd_clientes_ativos": 0, "tier_atual": "Início", "taxa_atual": 0.12}
    assert store.contar_clientes_ativos(vid) == 0


@pytest.mark.parametrize("dias, dentro", [(0, True), (59, True), (60, False), (200, False)])
def test_clawback_vale_so_antes_de_60_dias(db, dias, dentro):
    vid = vendedor()
    atribuir(vid, dias_atras=dias)
    assert store.encerrar_atribuicao("loja-a")["dentro_clawback"] is dentro


def test_encerrar_e_idempotente_e_ignora_tenant_sem_atribuicao(db):
    vid = vendedor()
    atribuir(vid)
    assert store.encerrar_atribuicao("loja-a") is not None
    assert store.encerrar_atribuicao("loja-a") is None  # já encerrada
    assert store.encerrar_atribuicao("nunca-existiu") is None


def test_motivo_invalido_e_recusado(db):
    vid = vendedor()
    atribuir(vid)
    with pytest.raises(ValueError):
        store.encerrar_atribuicao("loja-a", "porque-sim")
    assert store.contar_clientes_ativos(vid) == 1


def test_churn_do_vendedor(db):
    vid = vendedor()
    atribuir(vid, "a")
    atribuir(vid, "b", dias_atras=100)
    atribuir(vid, "c")
    store.encerrar_atribuicao("a")  # dentro do clawback
    store.encerrar_atribuicao("b")  # depois dos 60 dias

    resumo = store.churn_do_vendedor(vid)
    assert resumo["encerradas"] == 2
    assert resumo["clawbacks"] == 1
    assert resumo["taxa_churn"] == pytest.approx(2 / 3)
    assert store.churn_do_vendedor("sem-atribuicoes") == {"encerradas": 0, "clawbacks": 0, "taxa_churn": 0.0}


# ------------------------------------------------------------------- sincronizar

def test_sincronizar_encerra_tenant_cancelado(db, onboarding):
    vid = vendedor()
    atribuir(vid, "loja-a")
    atribuir(vid, "loja-b")
    onboarding.tenants = [tenant("loja-b")]
    onboarding.cancelados = ["loja-a"]

    resultado = churn.sincronizar()

    assert resultado == {"encerradas": ["loja-a"], "reabertas": []}
    assert store.get_atribuicao("loja-a")["motivo_encerramento"] == "cancelado"
    assert store.get_atribuicao("loja-b")["ativo"] is True
    assert store.comissao_do_mes(vid)["qtd_clientes_ativos"] == 1


def test_sincronizar_encerra_tenant_excluido(db, onboarding):
    vid = vendedor()
    atribuir(vid)
    onboarding.excluidos = ["loja-a"]

    churn.sincronizar()

    assert store.get_atribuicao("loja-a")["motivo_encerramento"] == "excluido"


def test_resposta_vazia_do_onboarding_nao_encerra_nada(db, onboarding):
    """Sem sinal explícito (tenant na lixeira), ausência de lista não é cancelamento."""
    vid = vendedor()
    atribuir(vid, "loja-a")
    atribuir(vid, "loja-b")
    onboarding.tenants = []
    onboarding.cancelados = []

    assert churn.sincronizar() == {"encerradas": [], "reabertas": []}
    assert store.contar_clientes_ativos(vid) == 2


def test_tenant_vivo_nunca_e_encerrado(db, onboarding):
    vid = vendedor()
    atribuir(vid)
    onboarding.tenants = [tenant("loja-a")]
    onboarding.cancelados = ["loja-a"]  # sinal contraditório: vivo vence

    assert churn.sincronizar()["encerradas"] == []
    assert store.get_atribuicao("loja-a")["ativo"] is True


def test_sincronizar_e_idempotente(db, onboarding):
    vid = vendedor()
    atribuir(vid)
    onboarding.cancelados = ["loja-a"]
    assert churn.sincronizar()["encerradas"] == ["loja-a"]
    assert churn.sincronizar() == {"encerradas": [], "reabertas": []}


def test_tenant_restaurado_volta_a_gerar_comissao_na_mesma_taxa(db, onboarding):
    vid = vendedor()
    for i in range(10):  # o 11º cliente entra em Crescimento (17%)
        atribuir(vid, f"base-{i}")
    original = atribuir(vid, "loja-a")
    assert original["taxa_travada"] == 0.17
    onboarding.cancelados = ["loja-a"]
    onboarding.tenants = [tenant(f"base-{i}") for i in range(10)]
    churn.sincronizar()
    assert store.contar_clientes_ativos(vid) == 10

    # restaurada dentro da janela da lixeira: volta a ser live
    onboarding.cancelados = []
    onboarding.tenants.append(tenant("loja-a"))
    resultado = churn.sincronizar()

    assert resultado["reabertas"] == ["loja-a"]
    reaberta = store.get_atribuicao("loja-a")
    assert reaberta["ativo"] is True and reaberta["taxa_travada"] == 0.17
    assert "motivo_encerramento" not in reaberta and "dentro_clawback" not in reaberta
    assert store.contar_clientes_ativos(vid) == 11


def test_tenant_excluido_nao_e_reaberto(db, onboarding):
    vid = vendedor()
    atribuir(vid)
    onboarding.excluidos = ["loja-a"]
    churn.sincronizar()
    onboarding.excluidos = []
    onboarding.tenants = [tenant("loja-a")]  # mesmo id recriado do zero: é outro cliente

    assert churn.sincronizar()["reabertas"] == []
    assert store.get_atribuicao("loja-a")["ativo"] is False


def test_onboarding_fora_do_ar_nao_altera_nada(db, onboarding):
    vid = vendedor()
    atribuir(vid)
    onboarding.cancelados = ["loja-a"]
    onboarding.erro_leitura = httpx.ConnectError("fora do ar")

    with pytest.raises(httpx.HTTPError):
        churn.sincronizar()
    assert churn.sincronizar_se_necessario() is None  # a versão segura engole o erro
    assert store.get_atribuicao("loja-a")["ativo"] is True

    onboarding.erro_leitura = None
    assert churn.sincronizar_se_necessario()["encerradas"] == ["loja-a"]  # falha não consome o intervalo


def test_intervalo_minimo_entre_sincronizacoes(db, onboarding):
    assert churn.sincronizar_se_necessario(agora=1000.0) is not None
    assert churn.sincronizar_se_necessario(agora=1000.0 + churn.INTERVALO_MINIMO_SEGUNDOS - 1) is None
    assert churn.sincronizar_se_necessario(agora=1000.0 + churn.INTERVALO_MINIMO_SEGUNDOS) is not None


# ---------------------------------------------------------------------- nas telas

def test_dashboard_ja_nao_soma_cliente_cancelado(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    atribuir(vid, "loja-a")
    atribuir(vid, "loja-b")
    onboarding.tenants = [tenant("loja-b")]
    onboarding.cancelados = ["loja-a"]

    pagina = cliente.get("/")

    assert pagina.status_code == 200
    assert store.comissao_do_mes(vid)["qtd_clientes_ativos"] == 1
    assert store.get_atribuicao("loja-a")["ativo"] is False


def test_dashboard_abre_mesmo_com_onboarding_fora_do_ar(novo_cliente, onboarding, db):
    cliente, vid = novo_cliente("ana")
    atribuir(vid)
    onboarding.erro_leitura = httpx.ConnectError("fora do ar")

    assert cliente.get("/").status_code == 200
    assert store.get_atribuicao("loja-a")["ativo"] is True


def test_tela_de_vendedores_mostra_churn_e_clawback(novo_cliente, onboarding, db):
    admin, _ = novo_cliente("chefe", role="admin")
    _, vid = novo_cliente("ana")
    atribuir(vid, "loja-a")
    onboarding.cancelados = ["loja-a"]

    pagina = admin.get("/admin/vendedores")

    assert pagina.status_code == 200
    assert "1 cancelado(s)" in pagina.text
    assert "1 em clawback" in pagina.text
