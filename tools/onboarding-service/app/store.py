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
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
_client = MongoClient(MONGO_URI)
_db = _client.get_default_database()
_signups = _db["signups"]
_free_tokens = _db["free_tokens"]


def create_signup(waba_id: str, phone_number_id: str, access_token: str) -> str:
    signup_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    _signups.insert_one(
        {
            "_id": signup_id,
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "access_token": access_token,
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


def mark_cancelled(signup_id: str) -> None:
    _signups.update_one(
        {"_id": signup_id},
        {"$set": {
            "cancelamento_status": "cancelado",
            "cancelamento_autorizado_em": datetime.now(timezone.utc),
            "status": "cancelado",
        }},
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
