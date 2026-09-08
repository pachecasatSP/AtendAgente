"""CRUD fino sobre o Mongo PRÓPRIO do backoffice (namespace `hermes`,
servidor 2.28.15.6 — não o Mongo compartilhado do `atendagente`, que só
é alcançável de dentro daquele outro cluster; ver
specs/comissao-vendedores.md e o plano de deploy). Coleções:
`vendedores`, `leads`, `atribuicoes_comissao`.

Regra de comissão implementada aqui (specs/comissao-vendedores.md,
seção "Faixas de clientes"): taxa CHEIA sobre o MRR do cliente, travada
no momento da atribuição pela faixa de clientes ativos que o vendedor
tinha NAQUELE momento — não recalculada retroativamente quando o
vendedor sobe (ou desce) de faixa depois.
"""
import hashlib
import math
import os
import secrets
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
_client = MongoClient(MONGO_URI)
_db = _client.get_default_database()
_vendedores = _db["vendedores"]
_leads = _db["leads"]
_atribuicoes = _db["atribuicoes_comissao"]

# Régua crescente oficial (specs/comissao-vendedores.md) — faixas de
# CONTAGEM de clientes ativos, taxa cheia sobre o MRR da carteira toda.
# Consolidado vai até 49 de propósito (não 20): impede que cruzar de 20
# pra 21 clientes derrube a taxa de 28% pra 27%.
FAIXAS_COMISSAO = [
    {"tier": "Início", "min": 1, "max": 10, "taxa": 0.12},
    {"tier": "Crescimento", "min": 11, "max": 15, "taxa": 0.17},
    {"tier": "Consolidado", "min": 16, "max": 49, "taxa": 0.28},
    {"tier": "Parceiro-chave", "min": 50, "max": None, "taxa": 0.27},
]

# Preços de tabela (specs/comissao-vendedores.md, "Planos de
# referência") — o onboarding-service devolve o NOME do plano no
# `/api/admin/tenants/{id}/usage`, não o preço; mapeamos aqui porque é
# o backoffice quem precisa do MRR pra travar a comissão.
PRECO_POR_PLANO = {
    "Entrada": 84.90,
    "Começando": 157.90,
    "Crescendo": 717.90,
}

# Meta de clientes por tier (specs/comissao-vendedores.md, "Mix-alvo
# por tier") — usada pro gatilho de 75% do bônus de rampa e pra regra
# de saída por baixa performance.
META_CLIENTES_POR_TIER = {"Início": 10, "Crescimento": 15, "Consolidado": 20, "Parceiro-chave": 50}

BONUS_RAMPA_VALOR = 500.00
BONUS_RAMPA_GATILHO_PCT = 0.75


def faixa_para(qtd_clientes_ativos: int) -> dict:
    """Faixa (tier/taxa) que corresponde a essa contagem de clientes
    ativos — usada tanto pra travar a taxa de um novo cliente quanto
    pra saber em que tier o vendedor está agora."""
    for faixa in FAIXAS_COMISSAO:
        if qtd_clientes_ativos >= faixa["min"] and (faixa["max"] is None or qtd_clientes_ativos <= faixa["max"]):
            return faixa
    return FAIXAS_COMISSAO[0]


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return digest.hex(), salt.hex()


def verify_password(password: str, doc: dict) -> bool:
    salt = bytes.fromhex(doc["password_salt"])
    digest_hex, _ = _hash_password(password, salt)
    return secrets.compare_digest(digest_hex, doc["password_hash"])


# ---------------------------------------------------------------- vendedores

def create_vendedor(
    nome: str,
    email: str,
    telefone: str,
    username: str,
    password: str,
    role: str = "vendedor",
    cidade_designada: str | None = None,
) -> str:
    digest_hex, salt_hex = _hash_password(password)
    vendedor_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    _vendedores.insert_one(
        {
            "_id": vendedor_id,
            "nome": nome,
            "email": email,
            "telefone": telefone,
            "username": username,
            "password_hash": digest_hex,
            "password_salt": salt_hex,
            "role": role,
            "cidade_designada": cidade_designada,
            "status": "ativo",
            "meses_abaixo_meta": 0,
            "ultima_advertencia_em": None,
            "criado_em": now,
        }
    )
    return vendedor_id


def get_vendedor(vendedor_id: str) -> dict | None:
    return _vendedores.find_one({"_id": vendedor_id})


def get_vendedor_by_username(username: str) -> dict | None:
    return _vendedores.find_one({"username": username})


def list_vendedores(status: str | None = "ativo") -> list[dict]:
    query = {"status": status} if status else {}
    return list(_vendedores.find(query))


def update_vendedor(vendedor_id: str, **fields) -> None:
    _vendedores.update_one({"_id": vendedor_id}, {"$set": fields})


def registrar_mes_performance(vendedor_id: str, bateu_75_pct: bool) -> dict:
    """Atualiza o contador de meses consecutivos abaixo da meta (regra
    de saída, specs/comissao-vendedores.md). Retorna o estado
    resultante pra quem chamar decidir se precisa de advertência/saída."""
    vendedor = get_vendedor(vendedor_id)
    if not vendedor:
        raise ValueError("vendedor não encontrado")
    if bateu_75_pct:
        update_vendedor(vendedor_id, meses_abaixo_meta=0)
        return {"meses_abaixo_meta": 0, "acao": None}
    meses = vendedor.get("meses_abaixo_meta", 0) + 1
    acao = None
    if meses == 1:
        acao = "alerta_informal"
    elif meses == 2:
        acao = "advertencia_formal"
        update_vendedor(vendedor_id, ultima_advertencia_em=datetime.now(timezone.utc))
    elif meses >= 3:
        acao = "desligamento"
    update_vendedor(vendedor_id, meses_abaixo_meta=meses)
    return {"meses_abaixo_meta": meses, "acao": acao}


# --------------------------------------------------------------------- leads

def create_lead(negocio: str, contato: str, cidade: str, origem: str, vendedor_id: str, plano_alvo: str | None = None) -> str:
    lead_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    _leads.insert_one(
        {
            "_id": lead_id,
            "negocio": negocio,
            "contato": contato,
            "cidade": cidade,
            "origem": origem,
            "vendedor_id": vendedor_id,
            "status": "novo",
            "plano_alvo": plano_alvo,
            "criado_em": now,
            "atualizado_em": now,
        }
    )
    return lead_id


def list_leads_by_vendedor(vendedor_id: str) -> list[dict]:
    return list(_leads.find({"vendedor_id": vendedor_id}).sort("criado_em", -1))


def get_lead(lead_id: str) -> dict | None:
    return _leads.find_one({"_id": lead_id})


def update_lead_status(lead_id: str, status: str) -> None:
    _leads.update_one({"_id": lead_id}, {"$set": {"status": status, "atualizado_em": datetime.now(timezone.utc)}})


# --------------------------------------------------------- atribuicoes_comissao

def contar_clientes_ativos(vendedor_id: str) -> int:
    return _atribuicoes.count_documents({"vendedor_id": vendedor_id, "ativo": True})


def atribuir_tenant(vendedor_id: str, tenant_id: str, plano: str, mrr: float) -> dict:
    """Cria a atribuição de comissão travando a taxa vigente na faixa
    ATUAL do vendedor (contagem de clientes ativos antes desse novo
    cliente entrar) — não retroage sobre atribuições já existentes."""
    qtd_atual = contar_clientes_ativos(vendedor_id)
    faixa = faixa_para(qtd_atual + 1)  # a faixa que esse novo cliente ocupa
    comissao_mensal = round(mrr * faixa["taxa"], 2)
    doc = {
        "_id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "vendedor_id": vendedor_id,
        "plano_na_atribuicao": plano,
        "mrr_na_atribuicao": mrr,
        "taxa_travada": faixa["taxa"],
        "tier_na_atribuicao": faixa["tier"],
        "comissao_mensal": comissao_mensal,
        "ativo": True,
        "atribuido_em": datetime.now(timezone.utc),
        "encerrado_em": None,
    }
    _atribuicoes.insert_one(doc)
    return doc


def encerrar_atribuicao(tenant_id: str) -> None:
    """Cliente cancelou (ou passou do clawback de 60 dias sem pagar) —
    para de contar comissão e de contar como cliente ativo do vendedor."""
    _atribuicoes.update_one(
        {"tenant_id": tenant_id, "ativo": True},
        {"$set": {"ativo": False, "encerrado_em": datetime.now(timezone.utc)}},
    )


def list_atribuicoes_ativas(vendedor_id: str) -> list[dict]:
    return list(_atribuicoes.find({"vendedor_id": vendedor_id, "ativo": True}))


def comissao_do_mes(vendedor_id: str) -> dict:
    """Soma a comissão mensal de todas as atribuições ativas — cada uma
    já carrega a taxa travada no momento em que foi fechada (não
    recalcula pela faixa atual do vendedor)."""
    ativas = list_atribuicoes_ativas(vendedor_id)
    total = round(sum(a["comissao_mensal"] for a in ativas), 2)
    qtd = len(ativas)
    faixa_atual = faixa_para(qtd) if qtd else FAIXAS_COMISSAO[0]
    return {"total": total, "qtd_clientes_ativos": qtd, "tier_atual": faixa_atual["tier"], "taxa_atual": faixa_atual["taxa"]}


def bate_gatilho_bonus_rampa(qtd_clientes_ativos: int) -> bool:
    meta_minima = META_CLIENTES_POR_TIER["Início"] * BONUS_RAMPA_GATILHO_PCT
    return qtd_clientes_ativos >= math.ceil(meta_minima)
