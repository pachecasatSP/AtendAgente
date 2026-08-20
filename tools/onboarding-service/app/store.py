"""CRUD fino sobre a coleção `signups` no MongoDB compartilhado
(reaproveita mongo.atendagente.svc.cluster.local, Fase 5) — controle do
estado de cada cadastro em andamento, não dados de conversa.

Nota de dívida técnica (ver plano da Fase 4): o access_token do WABA do
cliente fica gravado aqui entre o passo de troca de code e o passo de
provisionamento (duas requisições HTTP separadas) — não é um cofre de
segredos dedicado, mas aceitável pra V1 do trial já que o Mongo vive
dentro do namespace protegido. Não normalizar esse padrão pra outros
segredos.
"""
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

# Janela de restauração da "lixeira" (Fase 9) — dias entre o cancelamento
# autorizado (cancelamento_autorizado_em) e a exclusão definitiva
# automática (ver tools/provision-tenant/lixeira_watch.py, CronJob). Esse
# valor é duplicado lá de propósito (mesmo padrão de LIMITES em
# usage_watch.py) — processos/deploys separados, preferível a um import
# cross-serviço frágil por uma constante. Se mudar, atualizar nos dois
# lugares.
LIXEIRA_DIAS = 10

MONGO_URI = os.environ["MONGO_URI"]
_client = MongoClient(MONGO_URI)
_db = _client.get_default_database()
_signups = _db["signups"]
_free_tokens = _db["free_tokens"]
_panel_auth = _db["panel_auth"]


def create_signup(
    waba_id: str,
    phone_number_id: str,
    access_token: str,
    display_phone_number: str | None = None,
    verified_name: str | None = None,
) -> str:
    signup_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    _signups.insert_one(
        {
            "_id": signup_id,
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "access_token": access_token,
            "display_phone_number": display_phone_number,
            "verified_name": verified_name,
            "tenant_id": None,
            "status": "code_exchanged",
            "plano": "trial",
            "erro": None,
            "criado_em": now,
            "atualizado_em": now,
        }
    )
    return signup_id


def get_signup(signup_id: str) -> dict | None:
    return _signups.find_one({"_id": signup_id})


def update_signup(signup_id: str, **fields) -> None:
    fields["atualizado_em"] = datetime.now(timezone.utc)
    _signups.update_one({"_id": signup_id}, {"$set": fields})


def tenant_id_taken(tenant_id: str) -> bool:
    """Um tenant_id conta como ocupado se já tem signup provisionando ou
    ao vivo com ele — um `failed` libera o slug de novo pra tentativa."""
    return (
        _signups.find_one({"tenant_id": tenant_id, "status": {"$ne": "failed"}})
        is not None
    )


def list_live_signups() -> list[dict]:
    return list(_signups.find({"status": "live"}).sort("criado_em", -1))


def get_signup_by_tenant_id(tenant_id: str) -> dict | None:
    return _signups.find_one({"tenant_id": tenant_id, "status": "live"})


def list_usage_alerts() -> list[dict]:
    """Tenants ao vivo com usage_status em warning/over — preenchido
    pelo CronJob usage_watch.py, não calculado aqui."""
    return list(_signups.find({"status": "live", "usage_status": {"$in": ["warning", "over"]}}))


def list_failed_signups() -> list[dict]:
    """Cadastros com pagamento confirmado que travaram no provisionamento
    (status='failed') — candidatos a retry manual via admin-mcp."""
    return list(_signups.find({"status": "failed"}).sort("criado_em", -1))


# ── Tokens de gratuidade (cadastro sem cobrança, uso único) ────────────
#
# Gerados pela Duda (via MCP, ver tools/admin-mcp/) mediante PIN de admin
# verificado no lado do servidor — nunca a IA decidindo quem tem
# permissão. Consumidos em submit_form via ?invite=<token> no link de
# cadastro, pulando o checkout da Asaas e provisionando na hora.

def create_free_token(nota: str = "", criado_por: str = "duda") -> str:
    token = secrets.token_urlsafe(16)
    _free_tokens.insert_one(
        {
            "_id": token,
            "nota": nota,
            "criado_por": criado_por,
            "criado_em": datetime.now(timezone.utc),
            "usado": False,
            "usado_em": None,
            "signup_id": None,
        }
    )
    return token


def get_free_token(token: str) -> dict | None:
    return _free_tokens.find_one({"_id": token})


def mark_free_token_used(token: str, signup_id: str) -> None:
    _free_tokens.update_one(
        {"_id": token},
        {"$set": {"usado": True, "usado_em": datetime.now(timezone.utc), "signup_id": signup_id}},
    )


# ── Fila de alterações de SOUL vindas do painel do cliente ─────────────
#
# O painel do tenant não tem kubectl/kubeconfig, só Mongo — ele só marca
# `soul_pending=True` em cima do `config` já salvo no signup (ver
# tools/tenant-panel/app.py). Quem publica de verdade (regenera SOUL.md
# e reinicia o Deployment) é o próprio onboarding-service, que já roda
# com kubeconfig namespace-scoped — via loop periódico, ver main.py.

def list_soul_pending_signups() -> list[dict]:
    return list(_signups.find({"status": "live", "soul_pending": True}))


def mark_soul_applied(signup_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {
            "$set": {"soul_pending": False, "soul_applied_at": datetime.now(timezone.utc)},
            "$unset": {"soul_pending_error": ""},
        },
    )


def mark_soul_failed(signup_id: str, erro: str) -> None:
    """Publicação falhou (ex: kubectl exec fora do ar) — fica com
    soul_pending=True pro loop tentar de novo no próximo ciclo, só grava
    o erro pra aparecer no painel do cliente (ver
    tools/tenant-panel/templates/configuracoes.html)."""
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {"soul_pending_error": erro, "soul_pending_error_at": datetime.now(timezone.utc)}},
    )


# ── Pedidos de cancelamento de assinatura ───────────────────────────────
#
# O botão "cancelar assinatura" do painel do cliente só registra o
# pedido aqui (`cancelamento_status="solicitado"`) — quem cancela de
# verdade na Asaas é a Duda, via ferramenta `cancelar` do admin-mcp,
# depois de olhar a fila com `cancelamentos`. Nunca automático.

def list_cancellation_requests() -> list[dict]:
    return list(_signups.find({"status": "live", "cancelamento_status": "solicitado"}))


def request_cancellation(tenant_id: str) -> None:
    _signups.update_one(
        {"tenant_id": tenant_id, "status": "live"},
        {"$set": {
            "cancelamento_status": "solicitado",
            "cancelamento_solicitado_em": datetime.now(timezone.utc),
        }},
    )


# ── "Esqueci minha senha" — a Duda resolve na hora, na conversa ────────
#
# Sem fila, sem PIN de admin: o cliente pede o reset direto pra Duda no
# WhatsApp, ela pede o CPF/CNPJ do cadastro e chama `resetar_senha`
# (admin-mcp). A validação de identidade é esse dado batendo com
# signups.cpf_cnpj — nunca a IA decidindo sozinha, é o servidor
# comparando dígito a dígito (normalizado, sem pontuação).

def _only_digits(valor: str) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


def authorize_password_reset(tenant_id: str, cpf_cnpj: str) -> dict | None:
    """Confirma cpf_cnpj contra o cadastro do tenant; se bater, apaga o
    usuário/senha atual do painel (reabre o link de setup de uso único)
    e retorna o doc de signup (com panel_setup_url). Se não bater —
    tenant inexistente ou CPF/CNPJ errado — retorna None sem dar pista
    de qual dos dois foi."""
    signup = _signups.find_one({"tenant_id": tenant_id, "status": "live"})
    if not signup:
        return None
    if not secrets.compare_digest(_only_digits(cpf_cnpj), _only_digits(signup.get("cpf_cnpj", ""))):
        return None
    _panel_auth.delete_one({"_id": tenant_id})
    return signup


def mark_cancelled(signup_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {
            "cancelamento_status": "cancelado",
            "cancelamento_autorizado_em": datetime.now(timezone.utc),
            "status": "cancelado",
        }},
    )


# ── Lixeira (Fase 9): janela de 10 dias entre cancelamento e exclusão ──
#
# `cancelar` (admin-mcp) já para o pod (scale a 0) e marca
# status="cancelado" acima. Dentro da janela, o cliente pode pedir
# restauração (`reativar`/`reativar-checkout`, novo checkout Asaas —
# assinatura cancelada não volta sozinha, ver asaas_client.
# cancel_subscription). Passados os 10 dias sem restauração, o CronJob
# `lixeira_watch.py` apaga a infra de verdade e chama `mark_deleted`.

def list_lixeira() -> list[dict]:
    """Tenants cancelados ainda dentro (ou não) da janela de restauração —
    pra ferramenta `lixeira` da Duda calcular dias restantes. Não filtra
    por prazo aqui: mostrar mesmo os já vencidos (aguardando o próximo
    ciclo do CronJob) ajuda a responder "já foi apagado?" com precisão."""
    return list(_signups.find({"status": "cancelado"}).sort("cancelamento_autorizado_em", 1))


def list_recently_deleted(dias: int = 30) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
    return list(_signups.find({"status": "excluido", "excluido_em": {"$gte": cutoff}}).sort("excluido_em", -1))


def list_pending_deletion() -> list[dict]:
    """Cancelados cuja janela de `LIXEIRA_DIAS` já venceu — consumido só
    pelo CronJob `lixeira_watch.py`, nunca pelas rotas admin."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LIXEIRA_DIAS)
    return list(_signups.find({"status": "cancelado", "cancelamento_autorizado_em": {"$lte": cutoff}}))


def mark_reactivation_pending(signup_id: str, checkout_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {"status": "reativacao_pendente", "reativacao_checkout_id": checkout_id}},
    )


def mark_reactivated(signup_id: str, asaas_subscription_id: str | None) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {
            "$set": {
                "status": "live",
                "reativado_em": datetime.now(timezone.utc),
                **({"asaas_subscription_id": asaas_subscription_id} if asaas_subscription_id else {}),
            },
            "$unset": {
                "cancelamento_status": "", "cancelamento_autorizado_em": "",
                "reativacao_checkout_id": "",
            },
        },
    )


def mark_deleted(signup_id: str, tenant_id: str) -> None:
    """Exclusão definitiva confirmada (infra K8s/DNS já removida por
    `provision_tenant.delete_tenant_infra`) — mantém o doc de `signups`
    (auditoria/cobrança), só muda o status, e apaga o login do painel
    (não faz mais sentido logar em nada)."""
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {"status": "excluido", "excluido_em": datetime.now(timezone.utc)}},
    )
    _panel_auth.delete_one({"_id": tenant_id})


# ── Troca de número de WhatsApp (Fase 9... 10) ──────────────────────────
#
# O painel já valida o número novo direto com a Meta (register + envio
# de teste, tudo com o access_token do próprio tenant, sem precisar de
# kubectl) — só o corte de verdade (Secret + restart do pod) precisa
# passar por aqui, mesmo padrão assíncrono do soul_pending/
# catalogo_pending, só que com um loop bem mais curto (ver
# WHATSAPP_NUMERO_APPLY_INTERVAL_SECONDS em main.py) porque o cliente
# fica esperando na tela, não é uma alteração de fundo.

def list_whatsapp_numero_pending_signups() -> list[dict]:
    return list(_signups.find({"status": "live", "whatsapp_numero_pending": {"$exists": True}}))


def request_whatsapp_numero_troca(tenant_id: str, phone_number_id: str) -> None:
    _signups.update_one(
        {"tenant_id": tenant_id, "status": "live"},
        {"$set": {
            "whatsapp_numero_pending": {"phone_number_id": phone_number_id, "solicitado_em": datetime.now(timezone.utc)},
        }, "$unset": {"whatsapp_numero_erro": ""}},
    )


def mark_whatsapp_numero_aplicado(signup_id: str, phone_number_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {
            "$set": {"whatsapp_numero_aplicado_em": datetime.now(timezone.utc)},
            "$unset": {"whatsapp_numero_pending": "", "whatsapp_numero_erro": ""},
        },
    )
    # phone_number_id do próprio doc fica desatualizado (o de verdade é
    # só o que está no Secret) — não é a fonte de roteamento de nada,
    # mas atualizar evita confundir quem olhar o Mongo depois.
    _signups.update_one({"_id": signup_id}, {"$set": {"phone_number_id": phone_number_id}})


def mark_whatsapp_numero_falhou(signup_id: str, erro: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {"whatsapp_numero_erro": erro}, "$unset": {"whatsapp_numero_pending": ""}},
    )


# ── Limite de fotos do catálogo (Fase 10) ───────────────────────────────
#
# `FOTO_LIMITES` (por plano) vive no tenant-panel — a extensão manual
# concedida aqui é somada em cima daquele valor (ver `_foto_limite` em
# tools/tenant-panel/app.py). Sempre uma decisão humana (Duda, PIN via
# admin-mcp) depois de combinar cobrança avulsa — nunca automático.

def set_foto_limite_extra(tenant_id: str, extra: int) -> None:
    _signups.update_one(
        {"tenant_id": tenant_id, "status": "live"},
        {"$set": {"foto_limite_extra": extra}},
    )


# ── Fila de alterações de Catálogo vindas do painel do cliente ─────────
#
# Mesmo padrão da fila de SOUL (ver acima): o painel do tenant não tem
# kubectl, só marca catalogo_pending=True em cima dos itens já salvos em
# `catalogo_itens` (ver tools/tenant-panel/app.py). Quem publica de
# verdade — monta o CSV derivado e escreve no pod — é o
# onboarding-service, via loop periódico (ver main.py).

def list_catalogo_pending_signups() -> list[dict]:
    return list(_signups.find({"status": "live", "catalogo_pending": True}))


def log_ai_assist_usage(signup_id: str, tenant_id: str | None, origem: str, field: str, sucesso: bool) -> None:
    """1 documento por clique em "Gerar sugestão" (sucesso ou falha) — ver
    specs/assistente-ia-configuracoes.md seção 6. Não limita nem cobra
    nada ainda, só registra pra decidir preço/limite quando essa feature
    virar um add-on pago."""
    _db["ai_assist_usage"].insert_one({
        "signup_id": signup_id,
        "tenant_id": tenant_id,
        "origem": origem,
        "field": field,
        "sucesso": sucesso,
        "criado_em": datetime.now(timezone.utc),
    })


def list_catalogo_ativos(tenant_id: str) -> list[dict]:
    return list(
        _db["catalogo_itens"]
        .find({"tenant_id": tenant_id, "ativo": True})
        .sort("categoria", 1)
    )


def mark_catalogo_applied(signup_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {
            "$set": {"catalogo_pending": False, "catalogo_applied_at": datetime.now(timezone.utc)},
            "$unset": {"catalogo_pending_error": ""},
        },
    )


def mark_catalogo_failed(signup_id: str, erro: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {"catalogo_pending_error": erro, "catalogo_pending_error_at": datetime.now(timezone.utc)}},
    )


def tenant_usage(tenant_id: str) -> dict:
    """Contagem de sessões/mensagens sincronizadas pelo mongo-sync do
    tenant (mesmas collections que o tenant-panel lê)."""
    sessions_coll = _db["sessions"]
    messages_coll = _db["messages"]
    session_count = sessions_coll.count_documents({"tenant_id": tenant_id})
    message_count = messages_coll.count_documents({"tenant_id": tenant_id})
    last_session = sessions_coll.find_one(
        {"tenant_id": tenant_id}, sort=[("last_activity_at", -1)]
    )
    return {
        "sessoes": session_count,
        "mensagens": message_count,
        "ultima_atividade": (last_session or {}).get("last_activity_at"),
    }
