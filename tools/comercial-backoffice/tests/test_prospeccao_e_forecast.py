"""Prospecção (captura atômica, contagem, busca em segundo plano), forecast e criação do admin."""
import io

import pytest

from app import main as app_main
from app import places_client, prospeccao, store
import criar_admin


def semear(place_id="p1", negocio="Barbearia do Zé", telefone="(22) 99876-5432"):
    store.upsert_prospeccao(place_id, negocio, "Rua A", "Cabo Frio", "barbearia", telefone, None)


# -------------------------------------------------------------- captura atômica

def test_so_o_primeiro_vendedor_captura_o_candidato(novo_cliente, db):
    ana, ana_id = novo_cliente("ana")
    beto, beto_id = novo_cliente("beto")
    semear()

    primeira = ana.post("/prospeccao/p1/converter", data={"cidade": "Cabo Frio"})
    segunda = beto.post("/prospeccao/p1/converter", data={"cidade": "Cabo Frio"})

    assert (primeira.status_code, segunda.status_code) == (303, 404)
    leads = list(db["leads"].find({}))
    assert len(leads) == 1 and leads[0]["vendedor_id"] == ana_id
    assert store.get_prospeccao("p1")["convertido_lead_id"] == leads[0]["_id"]
    assert leads[0]["segmento"] == "Comércio e serviço do dia a dia"


def test_reivindicar_prospeccao_e_atomico(db):
    semear()
    assert store.reivindicar_prospeccao("p1", "lead-1") is True
    assert store.reivindicar_prospeccao("p1", "lead-2") is False
    assert store.get_prospeccao("p1")["convertido_lead_id"] == "lead-1"
    assert store.reivindicar_prospeccao("nao-existe", "lead-3") is False


def test_create_lead_aceita_id_pre_alocado(db):
    vid = store.create_vendedor("Ana", "a@x.com", "", "ana", "senha-de-teste-123")
    assert store.create_lead("L", "1", "Cabo Frio", "teste", vid, lead_id="fixo-123") == "fixo-123"
    assert store.get_lead("fixo-123")["negocio"] == "L"


# --------------------------------------------------------------- buscar_lote

def lugar(place_id, nome="Loja"):
    return {"id": place_id, "displayName": {"text": nome}, "formattedAddress": "Rua", "nationalPhoneNumber": "(22) 1"}


def test_buscar_lote_separa_novos_de_atualizados(db, monkeypatch):
    monkeypatch.setattr(places_client, "buscar", lambda query: [lugar("p1"), lugar("p2")])

    primeira = prospeccao.buscar_lote(1)
    segunda = prospeccao.buscar_lote(1)

    assert (primeira["novos"], primeira["atualizados"]) == (2, 0)
    assert (segunda["novos"], segunda["atualizados"]) == (0, 2)
    assert db["prospeccoes"].count_documents({}) == 2


def test_buscar_lote_nao_conta_duas_vezes_o_mesmo_lugar_na_rodada(db, monkeypatch):
    monkeypatch.setattr(places_client, "buscar", lambda query: [lugar("p1")])

    resultado = prospeccao.buscar_lote(3)  # 3 termos devolvendo o mesmo estabelecimento

    assert (resultado["novos"], resultado["atualizados"]) == (1, 0)


def test_buscar_lote_segue_apos_erro_e_avanca_o_cursor(db, monkeypatch):
    chamadas = []

    def buscar(query):
        chamadas.append(query)
        if len(chamadas) == 1:
            raise RuntimeError("cota estourada")
        return [lugar("p1")]

    monkeypatch.setattr(places_client, "buscar", buscar)
    resultado = prospeccao.buscar_lote(2)

    assert resultado["chamadas"] == 2 and resultado["novos"] == 1
    assert len(resultado["erros"]) == 1 and "cota estourada" in resultado["erros"][0]
    assert store.get_prospeccao_cursor() == 2


# ------------------------------------------------------ busca manual (segundo plano)

@pytest.fixture
def busca_falsa(monkeypatch):
    chamadas = []
    monkeypatch.setattr(prospeccao, "buscar_lote", lambda n: chamadas.append(n) or {})
    return chamadas


def test_buscar_mais_roda_em_segundo_plano_e_libera_a_trava(novo_cliente, db, busca_falsa):
    admin, _ = novo_cliente("chefe", role="admin")

    resposta = admin.post("/admin/prospeccao/buscar-mais", data={"pin": "1234"})

    assert resposta.status_code == 303
    assert busca_falsa == [app_main.PROSPECCAO_CHAMADAS_POR_LOTE]
    assert app_main._busca_em_andamento.acquire(blocking=False)  # trava liberada ao terminar
    app_main._busca_em_andamento.release()


def test_buscar_mais_com_pin_errado_nao_gasta_cota(novo_cliente, db, busca_falsa):
    admin, _ = novo_cliente("chefe", role="admin")
    assert admin.post("/admin/prospeccao/buscar-mais", data={"pin": "0000"}).status_code == 403
    assert busca_falsa == []


def test_buscar_mais_so_para_admin(novo_cliente, db, busca_falsa):
    ana, _ = novo_cliente("ana")
    assert ana.post("/admin/prospeccao/buscar-mais", data={"pin": "1234"}).status_code == 403
    assert busca_falsa == []


def test_buscar_mais_nao_dispara_segunda_busca_enquanto_uma_roda(novo_cliente, db, busca_falsa):
    admin, _ = novo_cliente("chefe", role="admin")
    assert app_main._busca_em_andamento.acquire(blocking=False)  # simula busca em andamento
    try:
        resposta = admin.post("/admin/prospeccao/buscar-mais", data={"pin": "1234"})
        assert resposta.status_code == 303
        assert busca_falsa == []
    finally:
        app_main._busca_em_andamento.release()


def test_falha_na_busca_em_segundo_plano_libera_a_trava(novo_cliente, db, monkeypatch):
    def quebra(_):
        raise RuntimeError("google fora do ar")

    monkeypatch.setattr(prospeccao, "buscar_lote", quebra)
    admin, _ = novo_cliente("chefe", role="admin")

    assert admin.post("/admin/prospeccao/buscar-mais", data={"pin": "1234"}).status_code == 303
    assert app_main._busca_em_andamento.acquire(blocking=False)
    app_main._busca_em_andamento.release()


# ------------------------------------------------------------------- forecast

def test_taxa_de_conversao_ignora_fechado_sem_reuniao(db):
    vid = store.create_vendedor("Ana", "a@x.com", "", "ana", "senha-de-teste-123")
    sem_reuniao = store.create_lead("A", "1", "Cabo Frio", "link", vid)
    store.update_lead_status(sem_reuniao, "fechado")
    com_reuniao_fechada = store.create_lead("B", "2", "Cabo Frio", "visita", vid)
    store.marcar_reuniao(com_reuniao_fechada, "2026-11-01")
    store.update_lead_status(com_reuniao_fechada, "fechado")
    com_reuniao_aberta = store.create_lead("C", "3", "Cabo Frio", "visita", vid)
    store.marcar_reuniao(com_reuniao_aberta, "2026-11-02")

    previsao = store.forecast_mes(vid)

    assert previsao["reunioes_feitas"] == 2
    assert previsao["fechados"] == 2  # o total continua sendo informado
    assert previsao["taxa_conversao_reuniao"] == 0.5  # antes dava 2/2 = 100%
    assert previsao["fechamentos_esperados"] == 0.5


def test_forecast_sem_reunioes_nao_divide_por_zero(db):
    vid = store.create_vendedor("Ana", "a@x.com", "", "ana", "senha-de-teste-123")
    assert store.forecast_mes(vid)["taxa_conversao_reuniao"] == 0.0


# ----------------------------------------------------------------- criar_admin

def test_criar_admin_cria_com_papel_admin_e_senha_valida(db):
    codigo = criar_admin.main(
        ["--nome", "Fulano", "--email", "f@x.com", "--username", "fulano"], entrada=io.StringIO("senha-forte-123\r\n")
    )

    assert codigo == 0
    admin = store.get_vendedor_by_username("fulano")
    assert admin["role"] == "admin" and admin["status"] == "ativo"
    assert store.verify_password("senha-forte-123", admin)  # o \r do heredoc foi descartado


def test_criar_admin_recusa_username_repetido(db, capsys):
    argv = ["--nome", "Fulano", "--email", "f@x.com", "--username", "fulano"]
    assert criar_admin.main(argv, entrada=io.StringIO("senha-forte-123\n")) == 0
    assert criar_admin.main(argv, entrada=io.StringIO("outra-senha-123\n")) == 1
    assert "Já existe" in capsys.readouterr().err
    assert db["vendedores"].count_documents({}) == 1


def test_criar_admin_recusa_senha_curta_sem_gravar(db, capsys):
    codigo = criar_admin.main(["--nome", "F", "--email", "f@x.com", "--username", "fulano"], entrada=io.StringIO("curta\n"))

    assert codigo == 2
    assert "pelo menos" in capsys.readouterr().err
    assert db["vendedores"].count_documents({}) == 0


def test_criar_admin_sem_senha_na_entrada(db):
    assert criar_admin.main(["--nome", "F", "--email", "f@x.com", "--username", "fulano"], entrada=io.StringIO("")) == 2
