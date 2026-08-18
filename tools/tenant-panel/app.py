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
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pymongo import MongoClient

import storage

TENANT_ID = os.environ["TENANT_ID"]
MONGO_URI = os.environ["MONGO_URI"]
PANEL_SETUP_TOKEN = os.environ["PANEL_SETUP_TOKEN"]
PANEL_SESSION_SECRET = os.environ["PANEL_SESSION_SECRET"]
ACCESS_TOKEN = os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
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
    return templates.TemplateResponse(request, "index.html", {"tenant_id": TENANT_ID})


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


@app.get("/painel/configuracoes", response_class=HTMLResponse)
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
            "agenda_disponivel": bool(CALENDAR_MCP_TOKEN),
            "google_calendar_email": config.get("google_calendar_email", ""),
            "agendamento_duracao_minutos": config.get("agendamento_duracao_minutos", 30),
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

    storage.delete_photo(item.get("foto_url"))
    foto_url = storage.upload_photo(TENANT_ID, item["slug"], file_bytes, content_type)
    catalogo_col.update_one({"_id": item["_id"]}, {"$set": {"foto_url": foto_url, "atualizado_em": datetime.now(timezone.utc)}})
    _mark_catalogo_pending()
    return {"ok": True, "foto_url": foto_url}


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

    itens_por_slug = {i["slug"]: i for i in catalogo_col.find({"tenant_id": TENANT_ID})}
    vinculados, sem_correspondencia = [], []

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
        storage.delete_photo(item.get("foto_url"))
        foto_url = storage.upload_photo(TENANT_ID, base, zf.read(name), content_type)
        catalogo_col.update_one({"_id": item["_id"]}, {"$set": {"foto_url": foto_url, "atualizado_em": datetime.now(timezone.utc)}})
        vinculados.append({"nome": item["nome"], "slug": base})

    if vinculados:
        _mark_catalogo_pending()
    return {"ok": True, "vinculados": vinculados, "sem_correspondencia": sem_correspondencia}


@app.get("/vitrine", response_class=HTMLResponse)
def vitrine(request: Request):
    """Pública, sem require_session — link que o bot pode mandar pro
    cliente final ver o catálogo inteiro (ver SOUL.md, catalog_block)."""
    if not TENANT_ENABLED:
        return templates.TemplateResponse(request, "tapume.html", {"tenant_id": TENANT_ID})

    itens = list(catalogo_col.find({"tenant_id": TENANT_ID, "ativo": True}).sort("categoria", 1))
    categorias: dict[str, list[dict]] = {}
    for item in itens:
        categorias.setdefault(item.get("categoria") or "Outros", []).append(item)

    signup = signups_col.find_one({"tenant_id": TENANT_ID, "status": "live"})
    nome_negocio = ((signup or {}).get("config") or {}).get("nome_negocio", TENANT_ID)
    return templates.TemplateResponse(
        request, "vitrine.html",
        {"tenant_id": TENANT_ID, "nome_negocio": nome_negocio, "categorias": categorias},
    )


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
    config["agendamento_ativo"] = True

    update = {"config": config}
    op: dict = {"$set": update}
    if primeira_ativacao:
        update["soul_pending"] = True
        update["soul_pending_at"] = datetime.now(timezone.utc)
        op["$unset"] = {"soul_pending_error": ""}
    signups_col.update_one({"_id": signup["_id"]}, op)
    return {"ok": True}


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
