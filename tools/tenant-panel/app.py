"""Painel de conversas por tenant (Fase 5 UI, movida pra dentro do
provisionamento — cada tenant tem o seu, não um painel central).

Lê TENANT_ID do ambiente e consulta o MongoDB compartilhado
(sessions/messages, sincronizados pelo sidecar mongo-sync), filtrando
sempre por esse tenant_id.

Autenticação: o cliente cadastra o próprio usuário/senha na primeira
visita, via um link de configuração de uso único
(`/painel/setup?token=...`, o token vem do provisionamento — ver
PANEL_SETUP_TOKEN). Depois disso é login por formulário normal, sessão
em cookie assinado (SessionMiddleware). Nada de HTTP Basic — evita o
popup nativo do navegador e permite logout de verdade.

"Esqueci minha senha": não tem fluxo nessa aplicação — o cliente pede
pra Duda (WhatsApp) direto, ela confirma o CPF/CNPJ do cadastro e usa a
ferramenta `resetar_senha` (admin-mcp), sem PIN, validação é o próprio
dado bater com o signup. Isso apaga o doc de panel_auth e reabre o
mesmo link de /painel/setup?token=... de uso único gerado no
provisionamento (nunca expira, só ficava bloqueado enquanto já havia
usuário/senha configurados).

Layout de duas colunas (lista à esquerda, thread à direita, estilo
WhatsApp Web) — a lista/thread carregam via fetch (rotas /painel/api/*),
sem reload de página. O badge de "não lidas" é calculado só no
navegador (localStorage comparando message_count com o que já foi
visto) — não existe conceito de usuário/leitura no backend.
"""
import csv
import hashlib
import io
import json
import os
import re
import secrets as secrets_module
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pymongo import MongoClient

import ai_assist
import storage

TENANT_ID = os.environ["TENANT_ID"]
MONGO_URI = os.environ["MONGO_URI"]
PANEL_SETUP_TOKEN = os.environ["PANEL_SETUP_TOKEN"]
PANEL_SESSION_SECRET = os.environ["PANEL_SESSION_SECRET"]
ACCESS_TOKEN = os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")
WABA_ID = os.environ.get("WHATSAPP_CLOUD_WABA_ID", "")
TWO_STEP_PIN = os.environ.get("WHATSAPP_CLOUD_TWO_STEP_PIN", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
GRAPH_API_VERSION = "v21.0"  # troca de número (Fase 10) usa endpoints mais novos (register) que não existem no v20.0 do envio de mensagem
TENANT_ENABLED = os.environ.get("WHATSAPP_CLOUD_ENABLED", "true").strip().lower() != "false"
CALENDAR_MCP_TOKEN = os.environ.get("CALENDAR_MCP_TOKEN", "")
CALENDAR_MCP_BASE_URL = os.environ.get("CALENDAR_MCP_BASE_URL", "http://calendar-mcp.atendagente.svc.cluster.local:8000")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client.get_default_database()
auth_col = db["panel_auth"]
sessions_col = db["sessions"]
messages_col = db["messages"]
signups_col = db["signups"]
catalogo_col = db["catalogo_itens"]
agendamentos_col = db["agendamentos"]
pedidos_col = db["pedidos"]
contadores_col = db["contadores"]

app = FastAPI(title=f"Painel — {TENANT_ID}")
app.add_middleware(SessionMiddleware, secret_key=PANEL_SESSION_SECRET, session_cookie="painel_session")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets_module.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return digest.hex(), salt.hex()


def _get_auth_doc() -> dict | None:
    return auth_col.find_one({"_id": TENANT_ID})


def _verify_password(password: str, doc: dict) -> bool:
    salt = bytes.fromhex(doc["password_salt"])
    digest_hex, _ = _hash_password(password, salt)
    return secrets_module.compare_digest(digest_hex, doc["password_hash"])


def require_session(request: Request) -> str:
    """Pras rotas de API (JSON) — 401 se não estiver logado, 403 se o
    tenant estiver pausado (ver TENANT_ENABLED / tapume.html)."""
    if not TENANT_ENABLED:
        raise HTTPException(status_code=403, detail="Atendimento pausado")
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return username


@app.get("/painel/setup", response_class=HTMLResponse)
def setup_page(request: Request, token: str = "", erro: str | None = None):
    if _get_auth_doc() is not None:
        return RedirectResponse("/painel/login")
    if not secrets_module.compare_digest(token, PANEL_SETUP_TOKEN):
        raise HTTPException(status_code=403, detail="Link de configuração inválido ou já usado")
    return templates.TemplateResponse(
        request, "setup.html", {"tenant_id": TENANT_ID, "token": token, "erro": erro}
    )


@app.post("/painel/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    token: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    def erro_de_volta(msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"tenant_id": TENANT_ID, "token": token, "erro": msg},
            status_code=400,
        )

    if _get_auth_doc() is not None:
        return RedirectResponse("/painel/login", status_code=303)
    if not secrets_module.compare_digest(token, PANEL_SETUP_TOKEN):
        raise HTTPException(status_code=403, detail="Link de configuração inválido ou já usado")

    username = username.strip()
    if len(username) < 3:
        return erro_de_volta("Usuário precisa ter pelo menos 3 caracteres.")
    if len(password) < 8:
        return erro_de_volta("Senha precisa ter pelo menos 8 caracteres.")
    if password != password_confirm:
        return erro_de_volta("As senhas não são iguais.")

    digest_hex, salt_hex = _hash_password(password)
    auth_col.insert_one(
        {
            "_id": TENANT_ID,
            "username": username,
            "password_hash": digest_hex,
            "password_salt": salt_hex,
            "created_at": datetime.now(timezone.utc),
        }
    )
    request.session["username"] = username
    return RedirectResponse("/painel", status_code=303)


@app.get("/painel/login", response_class=HTMLResponse)
def login_page(request: Request, erro: str | None = None):
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})
    if request.session.get("username"):
        return RedirectResponse("/painel")
    if _get_auth_doc() is None:
        return templates.TemplateResponse(request, "login.html", {"tenant_id": TENANT_ID, "erro": None, "not_configured": True})
    return templates.TemplateResponse(request, "login.html", {"tenant_id": TENANT_ID, "erro": erro, "not_configured": False})


@app.post("/painel/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    doc = _get_auth_doc()
    if doc and secrets_module.compare_digest(username.strip(), doc["username"]) and _verify_password(password, doc):
        request.session["username"] = doc["username"]
        return RedirectResponse("/painel", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"tenant_id": TENANT_ID, "erro": "Usuário ou senha incorretos.", "not_configured": False},
        status_code=400,
    )


@app.get("/painel/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/painel/login")


def fmt_ts(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=SAO_PAULO_TZ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(ts)


def fmt_data_br(iso: str) -> str:
    """Item de tipo evento guarda data_evento como "YYYY-MM-DD" (formato
    nativo de <input type=date>) — isso só formata pra exibição."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


templates.env.filters["data_br"] = fmt_data_br


@app.get("/painel", response_class=HTMLResponse)
def panel_shell(request: Request):
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})
    if not request.session.get("username"):
        return RedirectResponse("/painel/login")
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    agenda_disponivel = bool(CALENDAR_MCP_TOKEN) and bool(((signup or {}).get("config") or {}).get("agendamento_ativo"))
    pedidos_disponivel = bool(((signup or {}).get("config") or {}).get("pagamento_pix_ativo"))
    return templates.TemplateResponse(
        request, "index.html",
        {"tenant_id": TENANT_ID, "agenda_disponivel": agenda_disponivel, "pedidos_disponivel": pedidos_disponivel},
    )


@app.get("/painel/api/usage")
def api_usage(request: Request) -> dict:
    """Uso do mês corrente contra o limite do plano — calculado 1x/dia
    por tools/provision-tenant/usage_watch.py, não em tempo real. Sem
    doc de signup (ex: painel da Duda, que não passa pelo onboarding),
    devolve sem plano — o front esconde a barra nesse caso."""
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        return {"plano": None}
    return {
        "plano": signup.get("plano"),
        "usage_current_month": signup.get("usage_current_month"),
        "usage_limite": signup.get("usage_limite"),
        "usage_pct": signup.get("usage_pct"),
        "usage_status": signup.get("usage_status"),
    }


@app.get("/painel/api/sessions")
def api_sessions(request: Request) -> list[dict]:
    require_session(request)
    sessions = list(
        sessions_col
        .find({"tenant_id": TENANT_ID})
        .sort("last_activity_at", -1)
        .limit(200)
    )
    return [
        {
            "session_id": s.get("session_id"),
            "display_name": s.get("display_name"),
            "chat_id": s.get("chat_id"),
            "last_activity_fmt": fmt_ts(s.get("last_activity_at")),
            "message_count": s.get("message_count") or 0,
            "handoff": bool(s.get("handoff")),
            "needs_operator": bool(s.get("needs_operator")),
        }
        for s in sessions
    ]


@app.post("/painel/api/sessions/{session_id}/resolve")
def api_resolve_session(session_id: str, request: Request) -> dict:
    """Limpa o alerta de 'precisa de atenção manual' e devolve o
    controle pro bot (desliga handoff também) — o bot sinalizou (frase-
    gatilho detectada pelo mongo-sync) que essa conversa deve ser
    assumida por um humano, o que já silenciou o bot automaticamente;
    resolver aqui é o operador dizendo "cuidei disso, pode responder
    de novo"."""
    require_session(request)
    session = sessions_col.find_one({"tenant_id": TENANT_ID, "session_id": session_id})
    if not session:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    sessions_col.update_one(
        {"tenant_id": TENANT_ID, "session_id": session_id},
        {"$set": {"needs_operator": False, "handoff": False, "handoff_by": None, "handoff_at": None}},
    )
    return {"ok": True}


@app.delete("/painel/api/sessions/{session_id}")
def api_delete_session(session_id: str, request: Request) -> dict:
    """Remove uma conversa só da visualização do painel (Mongo) — não
    apaga a memória real do bot, que vive no SQLite do pod do Hermes e
    não é acessível daqui. Se o contato escrever de novo, a conversa
    volta a aparecer pro operador na próxima sincronização."""
    require_session(request)
    session = sessions_col.find_one({"tenant_id": TENANT_ID, "session_id": session_id})
    if not session:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    messages_col.delete_many({"tenant_id": TENANT_ID, "session_id": session_id})
    sessions_col.delete_one({"tenant_id": TENANT_ID, "session_id": session_id})
    return {"ok": True}


SOUL_APPLY_MINUTES = 5  # mantido em sincronia com SOUL_APPLY_INTERVAL_SECONDS do onboarding-service — ver comentário lá


def _parse_pipe_lines(text: str, fields: list[str]) -> list[dict]:
    """Mesmo formato do formulário de onboarding (um item por linha,
    campos separados por `|`) — duplicado aqui de propósito porque o
    pod do painel só monta tools/tenant-panel/ (não os diretórios
    irmãos), então não dá pra importar de main.py."""
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        items.append({f: (parts[i] if i < len(parts) else "") for i, f in enumerate(fields)})
    return items


def _parse_label_lines(text: str) -> dict:
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        label, _, value = line.partition(":")
        result[label.strip()] = value.strip()
    return result


def _format_pipe_lines(items: list[dict], fields: list[str]) -> str:
    return "\n".join("|".join((item.get(f) or "") for f in fields) for item in (items or []))


def _format_label_lines(mapping: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in (mapping or {}).items())


def _catalogo_itens_view() -> list[dict]:
    itens = list(catalogo_col.find({"tenant_id": TENANT_ID}).sort("criado_em", -1))
    return [
        {
            "id": str(i["_id"]),
            "nome": i.get("nome", ""),
            "slug": i.get("slug", ""),
            "descricao": i.get("descricao", ""),
            "preco": i.get("preco", 0),
            "categoria": i.get("categoria", ""),
            "tipo": i.get("tipo") or "produto",
            "data_evento": i.get("data_evento", ""),
            "horario_atendimento": i.get("horario_atendimento", ""),
            "ativo": bool(i.get("ativo", True)),
            "foto_url": i.get("foto_url"),
        }
        for i in itens
    ]


@app.get("/configuracoes", response_class=HTMLResponse)
def configuracoes_page(request: Request):
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})
    if not request.session.get("username"):
        return RedirectResponse("/painel/login")

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    config = (signup or {}).get("config") or {}
    tom = config.get("tom", {})
    escalacao = config.get("escalacao", {})

    return templates.TemplateResponse(
        request,
        "configuracoes.html",
        {
            "tenant_id": TENANT_ID,
            "soul_apply_minutes": SOUL_APPLY_MINUTES,
            "soul_pending": bool((signup or {}).get("soul_pending")),
            "soul_pending_error": (signup or {}).get("soul_pending_error"),
            "soul_applied_at_fmt": fmt_ts((signup or {}).get("soul_applied_at").timestamp()) if (signup or {}).get("soul_applied_at") else None,
            "cancelamento_status": (signup or {}).get("cancelamento_status"),
            "whatsapp_waba_id": (config.get("whatsapp") or {}).get("waba_id"),
            "whatsapp_phone_number_id": (config.get("whatsapp") or {}).get("phone_number_id"),
            "whatsapp_numero": (signup or {}).get("display_phone_number"),
            "whatsapp_numero_pending": bool((signup or {}).get("whatsapp_numero_pending")),
            "whatsapp_numero_erro": (signup or {}).get("whatsapp_numero_erro"),
            "tom_descricao": tom.get("descricao", ""),
            "tom_emoji": tom.get("emoji", ""),
            "pagamento_pix_ativo": bool(config.get("pagamento_pix_ativo")),
            "pagamento_pix_chave": config.get("pagamento_pix_chave", ""),
            "escalacao_nome": escalacao.get("nome", ""),
            "escalacao_telefone": escalacao.get("telefone", ""),
            "escalacao_pronome": escalacao.get("pronome", "ele"),
            "servicos": _format_pipe_lines(config.get("servicos"), ["nome", "descricao", "publico_alvo"]),
            "como_trabalhamos": _format_label_lines(config.get("como_trabalhamos")),
            "catalogo_itens": _catalogo_itens_view(),
            "catalogo_pending": bool((signup or {}).get("catalogo_pending")),
            "catalogo_pending_error": (signup or {}).get("catalogo_pending_error"),
            "catalogo_applied_at_fmt": fmt_ts((signup or {}).get("catalogo_applied_at").timestamp()) if (signup or {}).get("catalogo_applied_at") else None,
            "catalogo_apply_minutes": SOUL_APPLY_MINUTES,
            "fotos_configuradas": storage.configured(),
            "foto_limite": _foto_limite(signup),
            "foto_count": _fotos_count(),
            "agenda_disponivel": bool(CALENDAR_MCP_TOKEN),
            "google_calendar_email": config.get("google_calendar_email", ""),
            "agendamento_duracao_minutos": config.get("agendamento_duracao_minutos", 30),
            "agendamento_confirmacao_antecedencia_horas": config.get("agendamento_confirmacao_antecedencia_horas", 24),
            "agendamento_ativo": bool(config.get("agendamento_ativo")),
            "agenda_service_account_email": _calendar_mcp_service_account_email() if CALENDAR_MCP_TOKEN else None,
        },
    )


@app.post("/painel/api/configuracoes")
def api_salvar_configuracoes(request: Request, payload: dict) -> dict:
    require_session(request)

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup or not signup.get("config"):
        raise HTTPException(status_code=404, detail="Configuração do tenant não encontrada")

    tom_descricao = (payload.get("tom_descricao") or "").strip()
    escalacao_nome = (payload.get("escalacao_nome") or "").strip()
    escalacao_telefone = (payload.get("escalacao_telefone") or "").strip()
    if not tom_descricao:
        raise HTTPException(status_code=400, detail="Descrição do tom não pode ficar vazia")
    if not escalacao_nome or not escalacao_telefone:
        raise HTTPException(status_code=400, detail="Nome e telefone de escalação são obrigatórios")

    servicos = _parse_pipe_lines(payload.get("servicos") or "", ["nome", "descricao", "publico_alvo"])
    if not servicos:
        raise HTTPException(status_code=400, detail="Cadastre pelo menos um serviço")

    pagamento_pix_ativo = bool(payload.get("pagamento_pix_ativo"))
    pagamento_pix_chave = (payload.get("pagamento_pix_chave") or "").strip()
    if pagamento_pix_ativo and not pagamento_pix_chave:
        raise HTTPException(status_code=400, detail="Informe a chave PIX pra ativar pagamento via PIX")

    config = dict(signup["config"])
    config["tom"] = {"descricao": tom_descricao, "emoji": (payload.get("tom_emoji") or "").strip()}
    config["escalacao"] = {
        **config.get("escalacao", {}),
        "nome": escalacao_nome,
        "telefone": escalacao_telefone,
        "pronome": (payload.get("escalacao_pronome") or "ele").strip(),
    }
    config["servicos"] = servicos
    config["como_trabalhamos"] = _parse_label_lines(payload.get("como_trabalhamos") or "")
    config["pagamento_pix_ativo"] = pagamento_pix_ativo
    config["pagamento_pix_chave"] = pagamento_pix_chave if pagamento_pix_ativo else ""

    signups_col.update_one(
        {"_id": signup["_id"]},
        {
            "$set": {"config": config, "soul_pending": True, "soul_pending_at": datetime.now(timezone.utc)},
            "$unset": {"soul_pending_error": ""},
        },
    )
    return {"ok": True, "soul_apply_minutes": SOUL_APPLY_MINUTES}


@app.post("/painel/api/ai-assist")
def api_ai_assist(request: Request, payload: dict) -> dict:
    """Botão "Ajudar" nos campos difíceis de Configurações (ver
    specs/assistente-ia-configuracoes.md). Diferente do wizard, aqui o
    contexto vem do config já salvo no Mongo — o cliente já é um tenant
    live, não um cadastro em andamento."""
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    config = (signup or {}).get("config") or {}

    field = (payload.get("field") or "").strip()
    hint = (payload.get("hint") or "").strip()
    context = {
        "nome_negocio": config.get("nome_negocio", ""),
        "descricao_negocio": config.get("descricao_negocio", ""),
        "tom_descricao": (config.get("tom") or {}).get("descricao", ""),
        "servicos": _format_pipe_lines(config.get("servicos"), ["nome", "descricao", "publico_alvo"]),
        "como_trabalhamos": _format_label_lines(config.get("como_trabalhamos")),
    }

    try:
        suggestion = ai_assist.suggest_field(field, context, hint)
    except ai_assist.AiAssistError as e:
        db["ai_assist_usage"].insert_one({
            "signup_id": (signup or {}).get("_id"),
            "tenant_id": TENANT_ID,
            "origem": "painel",
            "field": field,
            "sucesso": False,
            "criado_em": datetime.now(timezone.utc),
        })
        raise HTTPException(status_code=502, detail=str(e))

    db["ai_assist_usage"].insert_one({
        "signup_id": (signup or {}).get("_id"),
        "tenant_id": TENANT_ID,
        "origem": "painel",
        "field": field,
        "sucesso": True,
        "criado_em": datetime.now(timezone.utc),
    })
    return {"suggestion": suggestion}


@app.get("/painel/api/soul-status")
def api_soul_status(request: Request) -> dict:
    """Pro front-end de Configurações ficar de olho na fila sem precisar
    recarregar a página — chamado em polling enquanto soul_pending=True
    (ver configuracoes.html). O erro (se houver) vem da última tentativa
    do loop de publicação no onboarding-service (ver soul_pending_error
    em tools/onboarding-service/app/store.py)."""
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        raise HTTPException(status_code=404, detail="Configuração do tenant não encontrada")
    applied_at = signup.get("soul_applied_at")
    return {
        "soul_pending": bool(signup.get("soul_pending")),
        "soul_pending_error": signup.get("soul_pending_error"),
        "soul_applied_at_fmt": fmt_ts(applied_at.timestamp()) if applied_at else None,
    }


def slugify(nome: str) -> str:
    """Minúsculas, sem acento, espaço/pontuação -> `_`. Base do slug de
    produto — congelado na criação (ver _unique_slug e specs/
    catalogo-produtos.md); é a chave de casamento com foto no import
    em massa, por isso a regra precisa ser estável."""
    nome = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii")
    nome = re.sub(r"[^a-z0-9]+", "_", nome.lower().strip()).strip("_")
    return nome or "produto"


def _unique_slug(base_slug: str) -> str:
    slug = base_slug
    i = 2
    while catalogo_col.find_one({"tenant_id": TENANT_ID, "slug": slug}):
        slug = f"{base_slug}-{i}"
        i += 1
    return slug


def _mark_catalogo_pending() -> None:
    """Mesmo padrão do soul_pending (ver api_salvar_configuracoes) — só
    marca a fila, quem publica de verdade é o onboarding-service. Na
    primeira ativação do catálogo (config.catalogo_ativo False->True),
    também marca soul_pending pra republicar o SOUL.md com o parágrafo
    fixo apontando pra vitrine (ver generate_soul.py)."""
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        return
    update = {"catalogo_pending": True, "catalogo_pending_at": datetime.now(timezone.utc)}
    config = signup.get("config") or {}
    if not config.get("catalogo_ativo"):
        config = dict(config)
        config["catalogo_ativo"] = True
        update["config"] = config
        update["soul_pending"] = True
        update["soul_pending_at"] = datetime.now(timezone.utc)
    signups_col.update_one(
        {"_id": signup["_id"]},
        {"$set": update, "$unset": {"catalogo_pending_error": ""}},
    )


TIPOS_CATALOGO = {"produto", "servico", "evento"}

# Limite de fotos de catálogo por plano (Fase 10, 2026-08-19) — estratégia
# comercial pro custo do storage/vitrine, não uma trava técnica de
# verdade (o custo real de storage por foto é irrisório, ~R$0,0001/mês
# mesmo no limite de 3MB — isso é diferencial de plano, não repasse de
# custo). Duplicado do conceito de LIMITES em usage_watch.py — mesma
# decisão de não fazer import cross-serviço por um dict pequeno. Plano
# ausente daqui (ex: "sem_limite" nem existe em PLANOS) = sem trava.
FOTO_LIMITES = {
    "entrada": 50,
    "comecando": 150,
    "crescendo": 500,
    "gratuito": 50,  # cadastro por convite, mesmo limite do Entrada
}


def _foto_limite(signup: dict) -> int | None:
    """None = sem limite (plano não travado, ex: Sem Limite). Soma um
    extra manual (`foto_limite_extra`) que a Duda pode conceder via
    admin-mcp depois de combinar cobrança avulsa com o cliente — nunca
    decidido sozinho pelo sistema."""
    base = FOTO_LIMITES.get((signup or {}).get("plano"))
    if base is None:
        return None
    extra = int((signup or {}).get("foto_limite_extra") or 0)
    return base + extra


def _fotos_count() -> int:
    return catalogo_col.count_documents({"tenant_id": TENANT_ID, "foto_url": {"$ne": None}})


def _catalogo_tipo_fields(tipo: str, payload: dict) -> dict:
    """Cada tipo só guarda o campo extra que faz sentido pra ele —
    evento tem data_evento, serviço tem horario_atendimento, produto não
    tem nenhum dos dois. Recalcula os dois de uma vez (nunca deixa lixo
    de um tipo antigo depois de trocar o tipo do item)."""
    return {
        "data_evento": (payload.get("data_evento") or "").strip() if tipo == "evento" else "",
        "horario_atendimento": (payload.get("horario_atendimento") or "").strip() if tipo == "servico" else "",
    }


def _find_produto(item_id: str) -> dict:
    try:
        oid = ObjectId(item_id)
    except InvalidId:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    item = catalogo_col.find_one({"_id": oid, "tenant_id": TENANT_ID})
    if not item:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return item


@app.get("/painel/api/catalogo-status")
def api_catalogo_status(request: Request) -> dict:
    """Mesmo padrão de polling do soul-status (ver configuracoes.html)."""
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        raise HTTPException(status_code=404, detail="Configuração do tenant não encontrada")
    applied_at = signup.get("catalogo_applied_at")
    return {
        "catalogo_pending": bool(signup.get("catalogo_pending")),
        "catalogo_pending_error": signup.get("catalogo_pending_error"),
        "catalogo_applied_at_fmt": fmt_ts(applied_at.timestamp()) if applied_at else None,
    }


@app.post("/painel/api/catalogo")
def api_criar_produto(request: Request, payload: dict) -> dict:
    require_session(request)
    nome = (payload.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório")
    try:
        preco = float(payload.get("preco") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Preço inválido")
    tipo = (payload.get("tipo") or "produto").strip().lower()
    if tipo not in TIPOS_CATALOGO:
        raise HTTPException(status_code=400, detail="Tipo inválido — use produto, servico ou evento")

    slug = _unique_slug(slugify(nome))
    now = datetime.now(timezone.utc)
    item = {
        "tenant_id": TENANT_ID,
        "nome": nome,
        "slug": slug,
        "descricao": (payload.get("descricao") or "").strip(),
        "preco": preco,
        "categoria": (payload.get("categoria") or "").strip(),
        "tipo": tipo,
        **_catalogo_tipo_fields(tipo, payload),
        "ativo": bool(payload.get("ativo", True)),
        "foto_url": None,
        "criado_em": now,
        "atualizado_em": now,
    }
    result = catalogo_col.insert_one(item)
    _mark_catalogo_pending()
    return {"ok": True, "item_id": str(result.inserted_id), "slug": slug}


@app.put("/painel/api/catalogo/{item_id}")
def api_editar_produto(item_id: str, request: Request, payload: dict) -> dict:
    require_session(request)
    item = _find_produto(item_id)

    update = {"atualizado_em": datetime.now(timezone.utc)}
    if "nome" in payload:
        nome = (payload.get("nome") or "").strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Nome não pode ficar vazio")
        update["nome"] = nome  # slug fica congelado, não recalcula
    if "descricao" in payload:
        update["descricao"] = (payload.get("descricao") or "").strip()
    if "preco" in payload:
        try:
            update["preco"] = float(payload.get("preco") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Preço inválido")
    if "categoria" in payload:
        update["categoria"] = (payload.get("categoria") or "").strip()
    if "tipo" in payload:
        tipo = (payload.get("tipo") or "produto").strip().lower()
        if tipo not in TIPOS_CATALOGO:
            raise HTTPException(status_code=400, detail="Tipo inválido — use produto, servico ou evento")
        update["tipo"] = tipo
        update.update(_catalogo_tipo_fields(tipo, payload))
    if "ativo" in payload:
        update["ativo"] = bool(payload.get("ativo"))

    catalogo_col.update_one({"_id": item["_id"]}, {"$set": update})
    _mark_catalogo_pending()
    return {"ok": True}


@app.delete("/painel/api/catalogo/{item_id}")
def api_excluir_produto(item_id: str, request: Request) -> dict:
    require_session(request)
    item = _find_produto(item_id)
    storage.delete_photo(item.get("foto_url"))
    catalogo_col.delete_one({"_id": item["_id"]})
    _mark_catalogo_pending()
    return {"ok": True}


@app.post("/painel/api/catalogo/{item_id}/foto")
async def api_upload_foto_produto(item_id: str, request: Request, file: UploadFile = File(...)) -> dict:
    require_session(request)
    item = _find_produto(item_id)

    content_type = file.content_type or ""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    if not item.get("foto_url"):
        signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
        limite = _foto_limite(signup)
        if limite is not None and _fotos_count() >= limite:
            raise HTTPException(
                status_code=402,
                detail=f"Seu plano inclui até {limite} fotos no catálogo — fala com a gente pra aumentar.",
            )

    storage.delete_photo(item.get("foto_url"))
    foto_url = storage.upload_photo(TENANT_ID, item["slug"], file_bytes, content_type)
    catalogo_col.update_one({"_id": item["_id"]}, {"$set": {"foto_url": foto_url, "atualizado_em": datetime.now(timezone.utc)}})
    _mark_catalogo_pending()
    return {"ok": True, "foto_url": foto_url}


@app.get("/painel/api/catalogo/template.csv")
def api_catalogo_template(request: Request) -> Response:
    """Modelo de planilha pro import em massa (ver api_importar_catalogo
    logo abaixo) — mesmas colunas, com uma linha de exemplo de cada tipo
    pra deixar claro quais campos valem pra qual (data_evento só conta
    pra evento, horario_atendimento só pra serviço)."""
    require_session(request)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["nome", "descricao", "preco", "categoria", "tipo", "data_evento", "horario_atendimento"])
    writer.writerow(["Produto exemplo", "Descrição do produto", "49,90", "Categoria", "produto", "", ""])
    writer.writerow(["Serviço exemplo", "Descrição do serviço", "120,00", "Categoria", "servico", "", "seg a sex, 9h às 18h"])
    writer.writerow(["Evento exemplo", "Descrição do evento", "80,00", "Categoria", "evento", "2026-12-24", ""])
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=modelo-catalogo.csv"},
    )


@app.post("/painel/api/catalogo/import")
async def api_importar_catalogo(request: Request, file: UploadFile = File(...)) -> dict:
    """Import em massa via CSV (colunas nome,descricao,preco,categoria,
    tipo,data_evento,horario_atendimento — as três últimas são
    opcionais) — devolve a lista nome->slug pro cliente nomear as fotos
    antes do upload em massa (ver /painel/api/catalogo/fotos-bulk)."""
    require_session(request)
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Arquivo precisa estar em UTF-8")

    now = datetime.now(timezone.utc)
    criados = []
    for row in csv.DictReader(io.StringIO(text)):
        nome = (row.get("nome") or "").strip()
        if not nome:
            continue
        try:
            preco = float((row.get("preco") or "0").replace(",", "."))
        except ValueError:
            preco = 0.0
        tipo = (row.get("tipo") or "produto").strip().lower()
        if tipo not in TIPOS_CATALOGO:
            tipo = "produto"
        slug = _unique_slug(slugify(nome))
        catalogo_col.insert_one({
            "tenant_id": TENANT_ID,
            "nome": nome,
            "slug": slug,
            "descricao": (row.get("descricao") or "").strip(),
            "preco": preco,
            "categoria": (row.get("categoria") or "").strip(),
            "tipo": tipo,
            **_catalogo_tipo_fields(tipo, row),
            "ativo": True,
            "foto_url": None,
            "criado_em": now,
            "atualizado_em": now,
        })
        criados.append({"nome": nome, "slug": slug})

    if not criados:
        raise HTTPException(status_code=400, detail="Nenhum produto válido encontrado (esperado: coluna 'nome')")

    _mark_catalogo_pending()
    return {"ok": True, "criados": criados}


MAX_BULK_ZIP_BYTES = 25 * 1024 * 1024
BULK_PHOTO_CONTENT_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@app.post("/painel/api/catalogo/fotos-bulk")
async def api_fotos_bulk(request: Request, file: UploadFile = File(...)) -> dict:
    """Zip de fotos nomeadas com o slug do produto (ex:
    cadeirao_de_praia_vermelha_10kg.jpg) — casa pelo nome do arquivo sem
    extensão. Itens sem correspondência ficam disponíveis pro upload
    unitário (ver api_upload_foto_produto)."""
    require_session(request)
    raw = await file.read()
    if len(raw) > MAX_BULK_ZIP_BYTES:
        raise HTTPException(status_code=400, detail="Zip maior que 25MB — divida em lotes menores")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Arquivo precisa ser um .zip")

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    limite = _foto_limite(signup)
    vagas = None if limite is None else max(0, limite - _fotos_count())

    itens_por_slug = {i["slug"]: i for i in catalogo_col.find({"tenant_id": TENANT_ID})}
    vinculados, sem_correspondencia, bloqueados_por_limite = [], [], []

    for name in zf.namelist():
        if name.endswith("/"):
            continue
        base, ext = os.path.splitext(os.path.basename(name))
        content_type = BULK_PHOTO_CONTENT_TYPES.get(ext.lower().lstrip("."))
        if not content_type:
            continue
        item = itens_por_slug.get(base)
        if not item:
            sem_correspondencia.append(name)
            continue
        eh_foto_nova = not item.get("foto_url")
        if eh_foto_nova and vagas is not None and vagas <= 0:
            bloqueados_por_limite.append(name)
            continue
        storage.delete_photo(item.get("foto_url"))
        foto_url = storage.upload_photo(TENANT_ID, base, zf.read(name), content_type)
        catalogo_col.update_one({"_id": item["_id"]}, {"$set": {"foto_url": foto_url, "atualizado_em": datetime.now(timezone.utc)}})
        vinculados.append({"nome": item["nome"], "slug": base})
        if eh_foto_nova and vagas is not None:
            vagas -= 1

    if vinculados:
        _mark_catalogo_pending()
    resultado = {"ok": True, "vinculados": vinculados, "sem_correspondencia": sem_correspondencia}
    if bloqueados_por_limite:
        resultado["bloqueados_por_limite"] = bloqueados_por_limite
        resultado["aviso_limite"] = f"Seu plano inclui até {limite} fotos no catálogo — {len(bloqueados_por_limite)} não foram enviadas. Fala com a gente pra aumentar."
    return resultado


TIPO_LABEL = {"produto": "Produtos", "evento": "Eventos", "servico": "Serviços"}
TIPO_ORDEM = ["produto", "evento", "servico"]

# Tipos que entram no fluxo de pedido+Pix (Fase 12) — serviço fica de
# fora, continua exclusivo do caminho de agendamento (Fase 11).
TIPOS_COMPRAVEIS = {"produto", "evento"}


@app.get("/vitrine", response_class=HTMLResponse)
def vitrine(request: Request):
    """Pública, sem require_session — link que o bot pode mandar pro
    cliente final ver o catálogo inteiro (ver SOUL.md, catalog_block).

    Segmentada por tipo (produto/evento/serviço, Fase 12) antes de
    categoria — cada tipo sua própria seção, mesma ordem de
    TIPO_ORDEM."""
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})

    itens = list(catalogo_col.find({"tenant_id": TENANT_ID, "ativo": True}).sort("categoria", 1))
    secoes: dict[str, dict[str, list[dict]]] = {}
    for item in itens:
        item["id"] = str(item["_id"])  # vitrine.html usa item.id (o botão "Pedir" precisa disso, Fase 12)
        tipo = item.get("tipo") or "produto"
        categoria = item.get("categoria") or "Outros"
        secoes.setdefault(tipo, {}).setdefault(categoria, []).append(item)
    secoes_ordenadas = [
        {"tipo": tipo, "label": TIPO_LABEL.get(tipo, tipo), "categorias": secoes[tipo]}
        for tipo in TIPO_ORDEM if tipo in secoes
    ]

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    nome_negocio = ((signup or {}).get("config") or {}).get("nome_negocio", TENANT_ID)
    return templates.TemplateResponse(
        request, "vitrine.html",
        {"tenant_id": TENANT_ID, "nome_negocio": nome_negocio, "secoes": secoes_ordenadas,
         "tipos_compraveis": list(TIPOS_COMPRAVEIS)},
    )


def _proximo_numero_pedido() -> int:
    """Contador sequencial por tenant (coleção `contadores`, `$inc`
    atômico) — número de pedido curto (#1, #2...) pra mencionar numa
    conversa, em vez do ObjectId de 24 caracteres do Mongo."""
    doc = contadores_col.find_one_and_update(
        {"_id": f"{TENANT_ID}:pedido"},
        {"$inc": {"valor": 1}},
        upsert=True,
        return_document=True,
    )
    return doc["valor"]


_numero_whatsapp_cache: str | None = None


def _numero_whatsapp_tenant() -> str | None:
    """Número de exibição do WhatsApp do tenant, pro link `wa.me/...` da
    vitrine — a Graph API só devolve o `phone_number_id` interno no
    Secret, o número formatado (com DDI/DDD) precisa de uma chamada à
    parte. Cacheado em memória (não muda com frequência; pod reinicia
    em troca de número — Fase 10 — o que já limpa o cache)."""
    global _numero_whatsapp_cache
    if _numero_whatsapp_cache is not None:
        return _numero_whatsapp_cache
    if not (ACCESS_TOKEN and PHONE_NUMBER_ID):
        return None
    try:
        body = _graph_get(PHONE_NUMBER_ID, {"fields": "display_phone_number"})
    except HTTPException:
        return None
    numero = (body.get("display_phone_number") or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "").lstrip("+")
    if numero:
        _numero_whatsapp_cache = numero
    return numero or None


_sufixo_tenant_cache: str | None = None


def _sufixo_tenant() -> str:
    """Últimos 8 caracteres do ObjectId do signup do tenant — vira parte
    do "número do pedido" exibido (ex: #47-a3f2b1c8), só pra
    identificação/exibição, nunca usado na busca (a busca continua só
    pelo número sequencial + tenant_id do processo, já garantido pelo
    isolamento por pod). Cacheado em memória (não muda)."""
    global _sufixo_tenant_cache
    if _sufixo_tenant_cache is not None:
        return _sufixo_tenant_cache
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    sufixo = str((signup or {}).get("_id") or "")[-8:] or "00000000"
    _sufixo_tenant_cache = sufixo
    return sufixo


def _numero_exibicao(numero_pedido: int) -> str:
    return f"{numero_pedido}-{_sufixo_tenant()}"


@app.post("/vitrine/api/pedido")
def api_vitrine_criar_pedido(payload: dict) -> dict:
    """Pública, sem require_session — clique em "Pedir" na vitrine.
    Cria o pedido com o preço travado no momento (nunca atualiza se o
    catálogo mudar depois) e devolve o link `wa.me` com o texto
    pré-preenchido que o webhook (whatsapp_cloud_patched.py) vai casar
    por regex pra fechar o pedido — ver Fase 12 na spec."""
    if not TENANT_ENABLED:
        raise HTTPException(status_code=404, detail="Não disponível")
    item_id = str(payload.get("item_id") or "")
    try:
        oid = ObjectId(item_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Item inválido")
    item = catalogo_col.find_one({"_id": oid, "tenant_id": TENANT_ID, "ativo": True})
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    tipo = item.get("tipo") or "produto"
    if tipo not in TIPOS_COMPRAVEIS:
        raise HTTPException(status_code=400, detail="Esse item não está disponível pra pedido direto")

    numero_whatsapp = _numero_whatsapp_tenant()
    if not numero_whatsapp:
        raise HTTPException(status_code=503, detail="WhatsApp não configurado neste momento")

    numero_pedido = _proximo_numero_pedido()
    valor = float(item.get("preco") or 0)
    pedidos_col.insert_one({
        "tenant_id": TENANT_ID,
        "numero": numero_pedido,
        "item_id": str(item["_id"]),
        "item_nome": item.get("nome", ""),
        "valor": valor,
        "status": "aberto",
        "chat_id": None,
        "criado_em": datetime.now(timezone.utc),
    })

    valor_fmt = f"R$ {valor:.2f}".replace(".", ",")
    texto = f"Quero fechar o pedido #{_numero_exibicao(numero_pedido)} ({item.get('nome', '')} - {valor_fmt})"
    wa_link = f"https://wa.me/{numero_whatsapp}?text={urllib.parse.quote(texto)}"
    return {"numero": numero_pedido, "numero_exibicao": _numero_exibicao(numero_pedido), "wa_link": wa_link}


# ── /pedidos (Fase 12) ───────────────────────────────────────────────

PEDIDOS_PAGINA_TAMANHO = 20


@app.get("/pedidos", response_class=HTMLResponse)
def pedidos_page(request: Request):
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})
    if not request.session.get("username"):
        return RedirectResponse("/painel/login")
    return templates.TemplateResponse(request, "pedidos.html", {"tenant_id": TENANT_ID})


@app.get("/painel/api/pedidos")
def api_pedidos_listar(request: Request, offset: int = 0) -> dict:
    require_session(request)
    itens = list(
        pedidos_col.find({"tenant_id": TENANT_ID})
        .sort("criado_em", -1)
        .skip(max(offset, 0))
        .limit(PEDIDOS_PAGINA_TAMANHO)
    )

    def _iso_utc(dt):
        return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None

    return {
        "pedidos": [
            {
                "id": str(p["_id"]),
                "numero": p.get("numero"),
                "numero_exibicao": _numero_exibicao(p.get("numero")),
                "item_nome": p.get("item_nome"),
                "valor": p.get("valor"),
                "status": p.get("status"),
                "chat_id": p.get("chat_id"),
                "criado_em": _iso_utc(p.get("criado_em")),
                "tem_comprovante": bool(p.get("comprovante_key")),
            }
            for p in itens
        ],
        "tem_mais": len(itens) == PEDIDOS_PAGINA_TAMANHO,
    }


@app.get("/painel/api/pedidos/{pedido_id}/comprovante")
def api_pedido_comprovante(request: Request, pedido_id: str) -> Response:
    """Serve o comprovante direto do bucket privado (sem ACL
    public-read, Fase 12) — só autenticado, nunca por link CDN direto,
    porque é dado financeiro/pessoal do cliente."""
    require_session(request)
    try:
        oid = ObjectId(pedido_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Id inválido")
    pedido = pedidos_col.find_one({"_id": oid, "tenant_id": TENANT_ID})
    if not pedido or not pedido.get("comprovante_key"):
        raise HTTPException(status_code=404, detail="Comprovante não encontrado")
    try:
        obj = storage._get_client().get_object(Bucket=storage.OBJECT_STORAGE_BUCKET, Key=pedido["comprovante_key"])
    except Exception:
        raise HTTPException(status_code=502, detail="Não consegui buscar o comprovante agora")
    return Response(content=obj["Body"].read(), media_type=obj.get("ContentType", "application/octet-stream"))


def _avisar_rastreamento_manual(pedido: dict) -> None:
    """Fase 12 — ao marcar pago, avisa o cliente que o acompanhamento a
    partir dali é manual e ensina a frase-gatilho pra pedir status
    (ver pedido_status em mongo_sync/sync_conversations.py). Best-
    effort: sem chat_id (pedido nunca foi fechado pela conversa) não
    tem pra quem mandar, e a própria _graph_call_best_effort já
    engole erro de rede/API — nunca bloqueia o "marcar como pago"."""
    chat_id = pedido.get("chat_id")
    if not chat_id or not (ACCESS_TOKEN and PHONE_NUMBER_ID):
        return
    numero_exibicao = _numero_exibicao(pedido.get("numero"))
    texto = (
        f"Pagamento do pedido #{numero_exibicao} confirmado! ✅\n\n"
        "O acompanhamento a partir daqui é direto com a gente — se quiser saber como está, "
        f"é só mandar \"qual o status do meu pedido #{numero_exibicao}?\" que a gente verifica."
    )
    _graph_call_best_effort(
        f"{PHONE_NUMBER_ID}/messages",
        {"messaging_product": "whatsapp", "to": chat_id, "type": "text", "text": {"body": texto}},
    )


@app.post("/painel/api/pedidos/{pedido_id}/pago")
def api_pedido_marcar_pago(request: Request, pedido_id: str) -> dict:
    require_session(request)
    try:
        oid = ObjectId(pedido_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Id inválido")
    pedido = pedidos_col.find_one({"_id": oid, "tenant_id": TENANT_ID})
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    pedidos_col.update_one({"_id": oid}, {"$set": {"status": "pago", "pago_em": datetime.now(timezone.utc)}})
    _avisar_rastreamento_manual(pedido)
    return {"ok": True}


@app.post("/painel/api/pedidos/{pedido_id}/cancelar")
def api_pedido_cancelar(request: Request, pedido_id: str) -> dict:
    require_session(request)
    try:
        oid = ObjectId(pedido_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Id inválido")
    resultado = pedidos_col.update_one(
        {"_id": oid, "tenant_id": TENANT_ID},
        {"$set": {"status": "cancelado", "cancelado_em": datetime.now(timezone.utc)}},
    )
    if resultado.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return {"ok": True}


def _calendar_mcp_post(path: str, payload: dict) -> dict:
    """Fala com o calendar-mcp usando o token deste tenant (mesmo Bearer
    que o Hermes usa pra ferramenta de agendamento, ver
    tools/calendar-mcp/server.py) — o painel reaproveita esse token só
    pra testar conexão, nunca cria evento por aqui."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{CALENDAR_MCP_BASE_URL}{path}",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {CALENDAR_MCP_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"ok": False, "motivo": "erro_desconhecido"}
    except urllib.error.URLError:
        return {"ok": False, "motivo": "calendar_mcp_indisponivel"}




def _calendar_mcp_service_account_email() -> str | None:
    """Melhor esforço — se o calendar-mcp ainda não estiver no ar (ex:
    infra da Google não provisionada ainda), a tela de Configurações
    simplesmente não mostra a instrução de compartilhamento."""
    try:
        with urllib.request.urlopen(f"{CALENDAR_MCP_BASE_URL}/service-account-email", timeout=5) as resp:
            return json.loads(resp.read()).get("email") or None
    except Exception:
        return None


AGENDA_ERRO_MENSAGENS = {
    "sem_acesso_a_agenda": "Não consegui acessar essa agenda — confirme que ela foi compartilhada com a conta de serviço.",
    "calendar_mcp_indisponivel": "Serviço de agenda indisponível no momento — tenta de novo em instantes.",
    "nao_configurado": "Agendamento ainda não está disponível neste servidor.",
    "email_vazio": "Informe o e-mail da Google Agenda.",
    "agenda_nao_configurada": "Configure sua Google Agenda em Configurações antes de ver os eventos aqui.",
}


@app.post("/painel/api/agenda")
def api_salvar_agenda(request: Request, payload: dict) -> dict:
    require_session(request)
    if not CALENDAR_MCP_TOKEN:
        raise HTTPException(status_code=500, detail="Agendamento ainda não está disponível neste servidor")

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup or not signup.get("config"):
        raise HTTPException(status_code=404, detail="Configuração do tenant não encontrada")

    google_calendar_email = (payload.get("google_calendar_email") or "").strip()
    if not google_calendar_email:
        raise HTTPException(status_code=400, detail="Informe o e-mail da Google Agenda")
    try:
        duracao = int(payload.get("agendamento_duracao_minutos") or 30)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Duração inválida")
    if duracao < 5 or duracao > 480:
        raise HTTPException(status_code=400, detail="Duração precisa ficar entre 5 e 480 minutos")
    try:
        antecedencia_horas = int(payload.get("agendamento_confirmacao_antecedencia_horas") or 24)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Antecedência inválida")
    if antecedencia_horas < 1 or antecedencia_horas > 168:
        raise HTTPException(status_code=400, detail="Antecedência precisa ficar entre 1 e 168 horas")

    resultado = _calendar_mcp_post("/testar-conexao", {"google_calendar_email": google_calendar_email})
    if not resultado.get("ok"):
        motivo = resultado.get("motivo", "erro_desconhecido")
        raise HTTPException(
            status_code=400,
            detail=AGENDA_ERRO_MENSAGENS.get(motivo, f"Não consegui confirmar o acesso à agenda ({motivo})"),
        )

    config = dict(signup["config"])
    primeira_ativacao = not config.get("agendamento_ativo")
    config["google_calendar_email"] = google_calendar_email
    config["agendamento_duracao_minutos"] = duracao
    config["agendamento_confirmacao_antecedencia_horas"] = antecedencia_horas
    config["agendamento_ativo"] = True

    update = {"config": config}
    op: dict = {"$set": update}
    if primeira_ativacao:
        update["soul_pending"] = True
        update["soul_pending_at"] = datetime.now(timezone.utc)
        op["$unset"] = {"soul_pending_error": ""}
    signups_col.update_one({"_id": signup["_id"]}, op)
    return {"ok": True}


@app.get("/agenda", response_class=HTMLResponse)
def agenda_page(request: Request):
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})
    if not request.session.get("username"):
        return RedirectResponse("/painel/login")
    return templates.TemplateResponse(request, "agenda.html", {"tenant_id": TENANT_ID})


@app.get("/painel/api/agenda/eventos")
def api_agenda_eventos(request: Request, semana: int = 0) -> dict:
    """`semana`: deslocamento em semanas a partir da atual (0 = semana
    corrente, -1 = anterior, 1 = próxima) — a visualização do painel é
    por semana (segunda a domingo), ver agenda.html.

    Lê direto do espelho local (`agendamentos`, Fase 11) em vez de bater
    na Graph API do Google a cada carregamento — a Google Agenda continua
    sendo a fonte de disponibilidade real na hora de marcar
    (`criar_agendamento`/`check_and_book`, calendar-mcp), esse espelho é
    só pra leitura rápida e pra guardar chat_id/status."""
    require_session(request)

    hoje = datetime.now(SAO_PAULO_TZ)
    inicio_semana_atual = (hoje - timedelta(days=hoje.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = inicio_semana_atual + timedelta(weeks=semana)
    fim = inicio + timedelta(days=7)

    def _iso_utc(dt):
        # PyMongo devolve datetime naive em UTC (converteu na escrita, ver
        # check_and_book) — sem isso o isoformat() sai sem timezone e o
        # navegador interpreta como horário local dele, não UTC.
        return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None

    eventos = [
        {
            "id": str(ev["_id"]),
            "titulo": ev.get("titulo"),
            "inicio": _iso_utc(ev.get("inicio")),
            "fim": _iso_utc(ev.get("fim")),
            "status": ev.get("status", "agendado"),
            "tem_contato": bool(ev.get("chat_id")),
            "link_evento": ev.get("link_evento"),
            "link_meet": ev.get("link_meet"),
        }
        for ev in agendamentos_col.find({
            "tenant_id": TENANT_ID,
            "inicio": {"$gte": inicio, "$lt": fim},
        }).sort("inicio", 1)
    ]
    return {
        "eventos": eventos,
        "semana_inicio": inicio.date().isoformat(),
        "semana_fim": (fim - timedelta(days=1)).date().isoformat(),
    }


@app.post("/painel/api/cancelar-assinatura")
def api_cancelar_assinatura(request: Request) -> dict:
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        raise HTTPException(status_code=404, detail="Assinatura não encontrada")
    if signup.get("cancelamento_status") == "solicitado":
        return {"ok": True, "cancelamento_status": "solicitado"}

    signups_col.update_one(
        {"_id": signup["_id"]},
        {"$set": {
            "cancelamento_status": "solicitado",
            "cancelamento_solicitado_em": datetime.now(timezone.utc),
        }},
    )
    return {"ok": True, "cancelamento_status": "solicitado"}


# ── Troca de número de WhatsApp (Fase 10) ───────────────────────────────
#
# Listar/testar é tudo direto com a Graph API usando o access_token do
# próprio tenant (já vem no Secret) — não precisa de kubectl. Só o corte
# de verdade (Secret + restart) passa pelo onboarding-service, via um
# flag no Mongo (ver store.request_whatsapp_numero_troca), mesmo padrão
# assíncrono do soul_pending/catalogo_pending.

def _graph_get(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "access_token": ACCESS_TOKEN})
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp API falhou: HTTP {exc.code}: {detail}")


def _graph_post(path: str, payload: dict) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp API falhou: HTTP {exc.code}: {detail}")


# ── Confirmação de agendamento por WhatsApp (Fase 11, Peça 2/3) ─────────
#
# Envio é sob demanda: pelo botão "Confirmar" em /agenda (aqui embaixo)
# ou pelo cronjob `agenda_lembrete_cron.py` (roda fora deste processo,
# direto no Mongo + Graph API, mesma lógica duplicada — ver comentário
# em LIMITES de usage_watch.py sobre por que duplicar em vez de importar
# entre serviços separados). Best-effort: qualquer erro aqui (template
# ainda não aprovado pela Meta, HTTP falhou) só retorna False, nunca
# derruba o resto do painel.

AGENDA_TEMPLATE_NAME = "agenda_confirmacao"
_template_waba_ids_ok: set[str] = set()  # cache em memória — evita recriar o template a cada envio


def _graph_call_best_effort(path: str, payload: dict, method: str = "POST") -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method=method,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": {"message": f"HTTP {exc.code}"}}
    except urllib.error.URLError as exc:
        return {"error": {"message": str(exc)}}


def _ensure_agenda_template() -> None:
    if WABA_ID in _template_waba_ids_ok or not WABA_ID or not ACCESS_TOKEN:
        return
    resultado = _graph_call_best_effort(
        f"{WABA_ID}/message_templates",
        {
            "name": AGENDA_TEMPLATE_NAME,
            "language": "pt_BR",
            "category": "UTILITY",
            "components": [
                {
                    "type": "BODY",
                    "text": "Oi! Confirmando seu compromisso: {{1}}.\nSe puder, toca em um dos botões abaixo.",
                    "example": {"body_text": [["quinta-feira (21/08) às 15:00"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Confirmar"},
                        {"type": "QUICK_REPLY", "text": "Remarcar"},
                    ],
                },
            ],
        },
    )
    erro = json.dumps(resultado.get("error") or {}).lower()
    if resultado.get("id") or "already" in erro:
        _template_waba_ids_ok.add(WABA_ID)


_DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def _formatar_quando(start_utc_naive) -> str:
    start = start_utc_naive.replace(tzinfo=timezone.utc).astimezone(SAO_PAULO_TZ)
    return f"{_DIAS_SEMANA[start.weekday()]} ({start.strftime('%d/%m')}) às {start.strftime('%H:%M')}"


def _enviar_confirmacao_agenda(agendamento: dict) -> bool:
    chat_id = agendamento.get("chat_id")
    if not (ACCESS_TOKEN and PHONE_NUMBER_ID and WABA_ID and chat_id):
        return False
    _ensure_agenda_template()
    agendamento_id = str(agendamento["_id"])
    resultado = _graph_call_best_effort(
        f"{PHONE_NUMBER_ID}/messages",
        {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "template",
            "template": {
                "name": AGENDA_TEMPLATE_NAME,
                "language": {"code": "pt_BR"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": _formatar_quando(agendamento["inicio"])}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "0",
                     "parameters": [{"type": "payload", "payload": f"agenda_confirmar:{agendamento_id}"}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "1",
                     "parameters": [{"type": "payload", "payload": f"agenda_remarcar:{agendamento_id}"}]},
                ],
            },
        },
    )
    return bool(resultado.get("messages") or [])


@app.post("/painel/api/agenda/confirmar")
def api_agenda_confirmar(request: Request, payload: dict) -> dict:
    require_session(request)
    agendamento_id = str(payload.get("agendamento_id") or "")
    try:
        oid = ObjectId(agendamento_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Id de agendamento inválido")
    agendamento = agendamentos_col.find_one({"_id": oid, "tenant_id": TENANT_ID, "status": "agendado"})
    if not agendamento:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado (ou já tem confirmação enviada)")
    if not agendamento.get("chat_id"):
        raise HTTPException(status_code=400, detail="Esse agendamento não tem um contato de WhatsApp vinculado")
    if not _enviar_confirmacao_agenda(agendamento):
        raise HTTPException(status_code=502, detail="Não consegui mandar a confirmação agora — o template pode ainda estar em análise na Meta, tenta de novo mais tarde")
    agendamentos_col.update_one({"_id": oid}, {"$set": {"status": "aguardando_confirmacao"}})
    return {"ok": True}


@app.get("/painel/api/whatsapp/numeros")
def api_whatsapp_numeros(request: Request) -> dict:
    """Números cadastrados na WABA do tenant, menos o que já está ativo —
    o cliente precisa ter adicionado o número real na WABA pelo lado da
    Meta antes disso (fora do nosso sistema)."""
    require_session(request)
    if not ACCESS_TOKEN or not WABA_ID:
        raise HTTPException(status_code=500, detail="WABA não configurada para este tenant")
    body = _graph_get(f"{WABA_ID}/phone_numbers", {"fields": "display_phone_number,verified_name,status,id"})
    numeros = [n for n in (body.get("data") or []) if n.get("id") != PHONE_NUMBER_ID]
    return {"numeros": numeros}


@app.post("/painel/api/whatsapp/testar-numero")
def api_whatsapp_testar_numero(request: Request, payload: dict) -> dict:
    """Registra o número candidato na Cloud API e manda uma mensagem de
    teste pro telefone de escalação — nada é trocado ainda, só valida.
    Fica ok pra chamar de novo em cima do mesmo número (register é
    idempotente do lado da Meta)."""
    require_session(request)
    phone_number_id = (payload.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="phone_number_id é obrigatório")
    if not TWO_STEP_PIN:
        raise HTTPException(status_code=500, detail="PIN de dois fatores não configurado para este tenant")

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    telefone_escalacao = ((signup or {}).get("config") or {}).get("escalacao", {}).get("telefone")
    if not telefone_escalacao:
        raise HTTPException(status_code=400, detail="Telefone de escalação não cadastrado — não dá pra testar sem um destinatário")

    _graph_post(f"{phone_number_id}/register", {"messaging_product": "whatsapp", "pin": TWO_STEP_PIN})

    status = None
    for _ in range(6):
        status = _graph_get(phone_number_id, {"fields": "status"}).get("status")
        if status == "CONNECTED":
            break
        time.sleep(2)
    if status != "CONNECTED":
        return {"ok": False, "erro": f"Número registrado, mas status ainda é '{status}' (esperado CONNECTED) — tenta de novo em instantes."}

    to = normalize_br_phone(telefone_escalacao)
    try:
        _graph_post(f"{phone_number_id}/messages", {
            "messaging_product": "whatsapp", "to": to, "type": "text",
            "text": {"body": "Teste de conexão do número novo do AtendPraGente — se você recebeu essa mensagem, o número está pronto pra ser confirmado no painel."},
        })
    except HTTPException as exc:
        return {"ok": False, "erro": f"Número registrado, mas o envio de teste falhou: {exc.detail}"}

    return {"ok": True}


def normalize_br_phone(raw: str) -> str:
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if digits.startswith("55") and len(digits) >= 12:
        return digits
    return f"55{digits}"


@app.post("/painel/api/whatsapp/confirmar-troca")
def api_whatsapp_confirmar_troca(request: Request, payload: dict) -> dict:
    """Só grava o pedido — quem troca de verdade (Secret + restart) é o
    loop `_whatsapp_numero_apply_loop` no onboarding-service, em até
    WHATSAPP_NUMERO_APPLY_INTERVAL_SECONDS. Não valida de novo aqui: se o
    cliente pulou o `testar-numero`, a troca ainda assim é tentada — o
    corte só falha (e fica registrado o erro) se o número realmente não
    funcionar, não é uma trava a mais pro fluxo normal (testar →
    confirmar) travar duas vezes."""
    require_session(request)
    phone_number_id = (payload.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(status_code=400, detail="phone_number_id é obrigatório")
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    signups_col.update_one(
        {"_id": signup["_id"]},
        {"$set": {"whatsapp_numero_pending": {"phone_number_id": phone_number_id, "solicitado_em": datetime.now(timezone.utc)}},
         "$unset": {"whatsapp_numero_erro": ""}},
    )
    return {"ok": True}


@app.get("/painel/api/whatsapp/status-troca")
def api_whatsapp_status_troca(request: Request) -> dict:
    """Pro painel fazer polling enquanto a troca está pendente (mesmo
    padrão do soul-status)."""
    require_session(request)
    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    if not signup:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    aplicado_em = signup.get("whatsapp_numero_aplicado_em")
    return {
        "pending": bool(signup.get("whatsapp_numero_pending")),
        "erro": signup.get("whatsapp_numero_erro"),
        "aplicado_em_fmt": fmt_ts(aplicado_em.timestamp()) if aplicado_em else None,
        "phone_number_id_ativo": PHONE_NUMBER_ID,
    }


DISPLAY_ROLES = ["user", "assistant", "operator"]


@app.get("/painel/api/messages/{session_id}")
def api_messages(session_id: str, request: Request) -> list[dict]:
    require_session(request)
    messages = list(
        messages_col
        .find({"tenant_id": TENANT_ID, "session_id": session_id, "role": {"$in": DISPLAY_ROLES}})
        .sort("timestamp", 1)
    )
    if not messages:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return [
        {
            "role": m.get("role"),
            "content": m.get("content"),
            "timestamp_fmt": fmt_ts(m.get("timestamp")),
        }
        for m in messages
    ]


@app.post("/painel/api/sessions/{session_id}/handoff")
def api_set_handoff(session_id: str, request: Request, payload: dict) -> dict:
    username = require_session(request)
    session = sessions_col.find_one({"tenant_id": TENANT_ID, "session_id": session_id})
    if not session:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")

    active = bool(payload.get("active"))
    update = {"handoff": active}
    update["handoff_by"] = username if active else None
    update["handoff_at"] = datetime.now(timezone.utc) if active else None
    sessions_col.update_one({"tenant_id": TENANT_ID, "session_id": session_id}, {"$set": update})
    return {"handoff": active}


def _send_whatsapp_message(chat_id: str, content: str) -> None:
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        raise HTTPException(status_code=500, detail="Envio manual não configurado para este tenant")

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    body = json.dumps(
        {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "text",
            "text": {"body": content},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp API falhou: HTTP {exc.code}: {detail}")


MAX_IMAGE_BYTES = 5 * 1024 * 1024  # limite da própria API do WhatsApp pra imagens


def _build_multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode()
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _upload_whatsapp_media(file_bytes: bytes, content_type: str, filename: str) -> str:
    """Sobe a imagem pro armazenamento de mídia da própria Meta — a
    gente nunca grava os bytes em disco/Mongo aqui. O media_id devolvido
    só existe do lado da Meta; se precisar reenviar/recuperar a imagem
    depois, é lá que ela vive, não neste painel."""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        raise HTTPException(status_code=500, detail="Envio manual não configurado para este tenant")

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/media"
    body, content_type_header = _build_multipart(
        {"messaging_product": "whatsapp", "type": content_type}, "file", filename, file_bytes, content_type
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": content_type_header},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Upload de mídia falhou: HTTP {exc.code}: {detail}")

    media_id = data.get("id")
    if not media_id:
        raise HTTPException(status_code=502, detail=f"Resposta da Meta sem media id: {data}")
    return media_id


def _send_whatsapp_image(chat_id: str, media_id: str, caption: str = "") -> None:
    image_payload = {"id": media_id}
    if caption:
        image_payload["caption"] = caption
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    body = json.dumps(
        {"messaging_product": "whatsapp", "to": chat_id, "type": "image", "image": image_payload}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp API falhou: HTTP {exc.code}: {detail}")


@app.post("/painel/api/messages/{session_id}/send-image")
async def api_send_image(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
) -> dict:
    username = require_session(request)
    session = sessions_col.find_one({"tenant_id": TENANT_ID, "session_id": session_id})
    if not session:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")

    chat_id = session.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=500, detail="Conversa sem chat_id — não é possível enviar")

    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Só é possível enviar arquivos de imagem")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    if len(file_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Imagem maior que 5MB — limite da API do WhatsApp")

    caption = caption.strip()
    media_id = _upload_whatsapp_media(file_bytes, content_type, file.filename or "imagem.jpg")
    _send_whatsapp_image(chat_id, media_id, caption)

    now = time.time()
    messages_col.insert_one(
        {
            "tenant_id": TENANT_ID,
            "session_id": session_id,
            "role": "operator",
            "content": f"📷 {caption}" if caption else "📷 Imagem enviada",
            "timestamp": now,
            "sent_by": username,
        }
    )
    sessions_col.update_one(
        {"tenant_id": TENANT_ID, "session_id": session_id},
        {
            "$set": {
                "last_activity_at": now,
                "handoff": True,
                "handoff_by": username,
                "handoff_at": datetime.now(timezone.utc),
            },
            "$inc": {"message_count": 1},
        },
    )
    return {"ok": True}


@app.post("/painel/api/messages/{session_id}/send")
def api_send_message(session_id: str, request: Request, payload: dict) -> dict:
    username = require_session(request)
    session = sessions_col.find_one({"tenant_id": TENANT_ID, "session_id": session_id})
    if not session:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")

    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Mensagem vazia")

    chat_id = session.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=500, detail="Conversa sem chat_id — não é possível enviar")

    _send_whatsapp_message(chat_id, content)

    now = time.time()
    messages_col.insert_one(
        {
            "tenant_id": TENANT_ID,
            "session_id": session_id,
            "role": "operator",
            "content": content,
            "timestamp": now,
            "sent_by": username,
        }
    )
    sessions_col.update_one(
        {"tenant_id": TENANT_ID, "session_id": session_id},
        {
            "$set": {
                "last_activity_at": now,
                "handoff": True,
                "handoff_by": username,
                "handoff_at": datetime.now(timezone.utc),
            },
            "$inc": {"message_count": 1},
        },
    )
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok", "tenant_id": TENANT_ID}
