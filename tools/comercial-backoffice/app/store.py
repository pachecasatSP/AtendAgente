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
_prospeccoes = _db["prospeccoes"]
_prospeccao_estado = _db["prospeccao_estado"]

LEAD_STATUS = ["novo", "em_conversa", "proposta", "fechado", "perdido"]

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

# Pra classificar o lead (plano_alvo), não só pra travar comissão —
# "Sem limite" entra aqui mesmo sem preço de tabela (é sob consulta),
# a atribuição de comissão desse plano continua manual (ver rota
# atribuir-tenant, que rejeita plano sem PRECO_POR_PLANO).
PLANOS_DISPONIVEIS = [*PRECO_POR_PLANO.keys(), "Sem limite"]

# ── Script de abordagem (specs/comissao-vendedores.md, "Script de
# abordagem: agendamento e demonstração") ─────────────────────────────
#
# Regionalidade aqui não é sotaque, é mix de negócio — por isso a
# segmentação é por tipo de negócio, não por cidade. Mesma categoria
# usada na busca automática (app/prospeccao.py::CATEGORIAS), mapeada
# pro segmento do script.

SEGMENTOS_DISPONIVEIS = ["Comércio e serviço do dia a dia", "Turismo e hospitalidade"]

SEGMENTO_POR_CATEGORIA = {
    "loja de roupas": "Comércio e serviço do dia a dia",
    "salão de beleza": "Comércio e serviço do dia a dia",
    "barbearia": "Comércio e serviço do dia a dia",
    "pet shop": "Comércio e serviço do dia a dia",
    "farmácia": "Comércio e serviço do dia a dia",
    "ótica": "Comércio e serviço do dia a dia",
    "papelaria": "Comércio e serviço do dia a dia",
    "loja de conveniência": "Comércio e serviço do dia a dia",
    "oficina mecânica": "Comércio e serviço do dia a dia",
    "pousada": "Turismo e hospitalidade",
    "restaurante": "Turismo e hospitalidade",
    "buffet de festas": "Turismo e hospitalidade",
    "passeio de barco": "Turismo e hospitalidade",
}

# Etapa 1 — só marcar 10-15 min, sem falar de preço/produto ainda.
ROTEIRO_AGENDAMENTO = {
    "Comércio e serviço do dia a dia": (
        "\"Oi, tudo bem? Sou [seu nome], da AtendPraGente — a gente atende "
        "[negócio parecido aqui perto]. Seu WhatsApp deve tocar o dia inteiro "
        "com gente perguntando horário e preço, né? Isso aqui responde "
        "sozinho na hora e só te chama quando é venda de verdade. Passo "
        "amanhã de manhã ou você prefere à tarde, uns 10 minutinhos?\""
    ),
    "Turismo e hospitalidade": (
        "\"Oi, tudo bem? Sou [seu nome], da AtendPraGente. Na alta temporada "
        "o WhatsApp de vocês deve travar de gente perguntando disponibilidade "
        "ao mesmo tempo — a gente responde na hora, 24h, e só chama vocês "
        "quando é reserva fechando de verdade. Mostro funcionando em 10 "
        "minutos — hoje à tarde ou amanhã de manhã?\""
    ),
}

ROTEIRO_AGENDAMENTO_OBJECAO = (
    "\"Não tenho tempo agora\" → nunca insiste no motivo, reforça o tamanho "
    "pequeno do pedido e volta pra alternativa dupla: \"Sem problema, nem é "
    "pra fechar nada hoje, só 10 minutos pra mostrar funcionando. Amanhã de "
    "manhã ou à tarde?\""
)

# Etapa 2 — já com a reunião marcada: provar (não descrever) e fechar
# assumindo que a pessoa vai contratar, só decidindo o plano.
ROTEIRO_DEMONSTRACAO_PASSOS = [
    "Diagnóstico rápido: \"Quem responde o WhatsApp hoje?\" · \"Já perdeu venda por demora?\" · \"Quantas mensagens por dia, mais ou menos?\"",
    "Demo ao vivo no celular da pessoa — manda uma mensagem de teste na hora, não descreve a funcionalidade.",
    "Ancoragem em valor: compara a mensalidade com o custo de perder 1 venda, nunca o preço isolado.",
    "Fechamento assumido: \"Posso já deixar configurado hoje — em qual desses planos você se vê?\" (nunca \"quer contratar?\").",
]

ROTEIRO_DEMONSTRACAO_OBJECOES = {
    "Comércio e serviço do dia a dia": (
        "\"Meu cliente gosta de falar comigo, não quero parecer frio\" → "
        "Ele continua falando com você — isso só responde o repetitivo e te "
        "chama quando precisa de verdade. Você não some, aparece só na hora "
        "que importa."
    ),
    "Turismo e hospitalidade": (
        "\"Minha temporada é curta, não compensa pagar o ano inteiro\" → "
        "É mensalidade, não fidelidade — paga só enquanto usa (clawback de "
        "60 dias) e cancela quando quiser."
    ),
}

ROTEIRO_DEMONSTRACAO_OBJECAO_PRECO = (
    "\"Achei caro\" → compara com o custo de 1 funcionário respondendo "
    "WhatsApp em tempo integral, não com \"nada\"."
)


def segmento_para_categoria(categoria: str) -> str | None:
    return SEGMENTO_POR_CATEGORIA.get(categoria)

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

def _slugify(nome: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in nome.lower().strip())
    while "--" in base:
        base = base.replace("--", "-")
    return base.strip("-") or "vendedor"


def _slug_unico(nome: str) -> str:
    """Slug curto e estável pro link pessoal de convite do vendedor
    (?vendedor=<slug> em /signup, ver specs/comissao-vendedores.md) —
    gerado uma vez na criação, nunca muda depois."""
    base = _slugify(nome)
    slug = base
    while _vendedores.find_one({"slug": slug}):
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


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
            "slug": _slug_unico(nome),
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


def get_vendedor_by_slug(slug: str) -> dict | None:
    return _vendedores.find_one({"slug": slug})


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

def create_lead(
    negocio: str,
    contato: str,
    cidade: str,
    origem: str,
    vendedor_id: str,
    plano_alvo: str | None = None,
    segmento: str | None = None,
) -> str:
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
            "segmento": segmento,
            "reunioes": [],
            "comentarios": [],
            "criado_em": now,
            "atualizado_em": now,
        }
    )
    return lead_id


def list_leads_by_vendedor(vendedor_id: str) -> list[dict]:
    return list(_leads.find({"vendedor_id": vendedor_id}).sort("criado_em", -1))


def get_lead(lead_id: str) -> dict | None:
    return _leads.find_one({"_id": lead_id})


def marcar_reuniao(lead_id: str, data: str) -> None:
    """Reunião é um evento (lead pode ter mais de uma) — guardada como
    lista, não um booleano, pra permitir o cruzamento reunião×fechamento
    do forecast (quantas reuniões viraram fechamento de verdade)."""
    _leads.update_one(
        {"_id": lead_id},
        {"$push": {"reunioes": data}, "$set": {"atualizado_em": datetime.now(timezone.utc)}},
    )


def update_lead_plano(lead_id: str, plano_alvo: str | None) -> None:
    _leads.update_one(
        {"_id": lead_id},
        {"$set": {"plano_alvo": plano_alvo, "atualizado_em": datetime.now(timezone.utc)}},
    )


def update_lead_segmento(lead_id: str, segmento: str | None) -> None:
    _leads.update_one(
        {"_id": lead_id},
        {"$set": {"segmento": segmento, "atualizado_em": datetime.now(timezone.utc)}},
    )


def adicionar_comentario(lead_id: str, texto: str) -> None:
    """Anotação livre do vendedor sobre o andamento do lead (objeção
    ouvida, combinado feito, etc.) — histórico, não sobrescreve."""
    comentario = {"texto": texto, "criado_em": datetime.now(timezone.utc)}
    _leads.update_one(
        {"_id": lead_id},
        {"$push": {"comentarios": comentario}, "$set": {"atualizado_em": datetime.now(timezone.utc)}},
    )


def pipeline_por_vendedor() -> list[dict]:
    """Contagem de leads por etapa, por vendedor — base da visão
    agregada de pipeline do admin (progresso de prospecção por
    vendedor/cidade, specs/comissao-vendedores.md seção 11)."""
    contagens = {}
    for lead in _leads.find({}, {"vendedor_id": 1, "status": 1}):
        contagens.setdefault(lead["vendedor_id"], {s: 0 for s in LEAD_STATUS})
        contagens[lead["vendedor_id"]][lead["status"]] += 1
    resultado = []
    for vendedor_id, por_status in contagens.items():
        vendedor = get_vendedor(vendedor_id)
        if not vendedor:
            continue
        resultado.append({"vendedor": vendedor, "por_status": por_status, "total": sum(por_status.values())})
    resultado.sort(key=lambda r: r["vendedor"]["nome"])
    return resultado


def funil_por_vendedor(vendedor_id: str) -> dict:
    """Contagem por etapa só desse vendedor — pro gráfico de funil no
    próprio painel dele (versão de 1 linha do que pipeline_por_vendedor
    faz pra todo mundo)."""
    contagem = {s: 0 for s in LEAD_STATUS}
    for lead in _leads.find({"vendedor_id": vendedor_id}, {"status": 1}):
        contagem[lead["status"]] += 1
    return contagem


def forecast_mes(vendedor_id: str) -> dict:
    """Cruza reunião×fechamento (taxa de conversão histórica do próprio
    vendedor) com o pipeline aberto pra projetar clientes e comissão até
    o fim do mês. Comissão nova projetada usa a taxa do TIER ATUAL (não
    recalcula banda — é uma estimativa de painel, não a apuração oficial
    de comissao_do_mes, que continua sendo a travada por atribuição)."""
    leads = list(_leads.find({"vendedor_id": vendedor_id}))
    fechados = sum(1 for l in leads if l["status"] == "fechado")
    com_reuniao = [l for l in leads if l.get("reunioes")]
    reunioes_feitas = len(com_reuniao)
    taxa_conversao = (fechados / reunioes_feitas) if reunioes_feitas else 0.0

    em_aberto_com_reuniao = [l for l in com_reuniao if l["status"] not in ("fechado", "perdido")]
    fechamentos_esperados = round(len(em_aberto_com_reuniao) * taxa_conversao, 1)

    ativas = list_atribuicoes_ativas(vendedor_id)
    ticket_medio = (
        sum(a["mrr_na_atribuicao"] for a in ativas) / len(ativas)
        if ativas
        else sum(PRECO_POR_PLANO.values()) / len(PRECO_POR_PLANO)
    )
    comissao_atual = comissao_do_mes(vendedor_id)
    comissao_extra_estimada = round(fechamentos_esperados * ticket_medio * comissao_atual["taxa_atual"], 2)

    return {
        "reunioes_feitas": reunioes_feitas,
        "fechados": fechados,
        "taxa_conversao_reuniao": taxa_conversao,
        "leads_em_pipeline_com_reuniao": len(em_aberto_com_reuniao),
        "fechamentos_esperados": fechamentos_esperados,
        "comissao_atual": comissao_atual["total"],
        "comissao_extra_estimada": comissao_extra_estimada,
        "comissao_projetada": round(comissao_atual["total"] + comissao_extra_estimada, 2),
    }


def update_lead_status(lead_id: str, status: str) -> None:
    _leads.update_one({"_id": lead_id}, {"$set": {"status": status, "atualizado_em": datetime.now(timezone.utc)}})


# --------------------------------------------------------- atribuicoes_comissao

def contar_clientes_ativos(vendedor_id: str) -> int:
    return _atribuicoes.count_documents({"vendedor_id": vendedor_id, "ativo": True})


def existe_atribuicao(tenant_id: str) -> bool:
    return _atribuicoes.find_one({"tenant_id": tenant_id}) is not None


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


# ---------------------------------------------------------- prospecção (base de consulta)
#
# Candidatos a lead vindos da busca automática no Google Places (ver
# app/prospeccao.py) — ficam aqui até um vendedor/admin "converter" um
# em lead de verdade. Não confundir com `leads`: isso é a lista fria
# ainda não trabalhada por ninguém.

def get_prospeccao_cursor() -> int:
    """Posição na matriz cidade×categoria (app/prospeccao.py) onde a
    última rodada parou — cada rodada continua dali, não repete a
    matriz inteira do zero (orçamento é limitado, 50 chamadas/dia)."""
    doc = _prospeccao_estado.find_one({"_id": "cursor"})
    return doc["valor"] if doc else 0


def set_prospeccao_cursor(valor: int) -> None:
    _prospeccao_estado.update_one({"_id": "cursor"}, {"$set": {"valor": valor}}, upsert=True)


def upsert_prospeccao(
    place_id: str,
    negocio: str,
    endereco: str,
    cidade: str,
    categoria: str,
    telefone: str | None,
    site: str | None,
) -> None:
    """Prioridade = tem telefone (WhatsApp costuma ser o mesmo número em
    negócio pequeno no Brasil) — é o critério pedido pra ordenar quem
    vale a pena abordar primeiro."""
    now = datetime.now(timezone.utc)
    prioridade = 2 if telefone else (1 if site else 0)
    _prospeccoes.update_one(
        {"_id": place_id},
        {
            "$set": {
                "negocio": negocio,
                "endereco": endereco,
                "cidade": cidade,
                "categoria": categoria,
                "telefone": telefone,
                "site": site,
                "prioridade": prioridade,
                "visto_em": now,
            },
            "$setOnInsert": {"descoberto_em": now, "convertido_lead_id": None, "desqualificado": False, "desqualificado_em": None},
        },
        upsert=True,
    )


def get_prospeccao(place_id: str) -> dict | None:
    return _prospeccoes.find_one({"_id": place_id})


PROSPECCAO_PAGE_SIZE = 25


def _prospeccao_query(convertido: bool) -> dict:
    return {"convertido_lead_id": None} if not convertido else {"convertido_lead_id": {"$ne": None}}


def count_prospeccoes(convertido: bool = False) -> int:
    return _prospeccoes.count_documents(_prospeccao_query(convertido))


def list_prospeccoes(convertido: bool = False, pagina: int = 1) -> list[dict]:
    """Paginado a 25 por página, sempre ordenado por prioridade (quem
    tem telefone primeiro, desqualificado sempre por último) e sempre
    só os NÃO capturados por padrão (convertido_lead_id=None) — um
    candidato só some da lista quando alguém converte ele em lead de
    verdade, nunca por paginação nem por desqualificação."""
    skip = max(pagina - 1, 0) * PROSPECCAO_PAGE_SIZE
    return list(
        _prospeccoes.find(_prospeccao_query(convertido))
        .sort([("desqualificado", 1), ("prioridade", -1), ("descoberto_em", -1)])
        .skip(skip)
        .limit(PROSPECCAO_PAGE_SIZE)
    )


def marcar_prospeccao_desqualificada(place_id: str, motivo: str = "") -> None:
    _prospeccoes.update_one(
        {"_id": place_id},
        {"$set": {"desqualificado": True, "desqualificado_em": datetime.now(timezone.utc), "desqualificado_motivo": motivo}},
    )


def marcar_prospeccao_convertida(place_id: str, lead_id: str) -> None:
    _prospeccoes.update_one({"_id": place_id}, {"$set": {"convertido_lead_id": lead_id}})
