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

Layout de duas colunas (lista à esquerda, thread à direita, estilo
WhatsApp Web) — a lista/thread carregam via fetch (rotas /painel/api/*),
sem reload de página. O badge de "não lidas" é calculado só no
navegador (localStorage comparando message_count com o que já foi
visto) — não existe conceito de usuário/leitura no backend.
"""
import hashlib
import json
import os
import secrets as secrets_module
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pymongo import MongoClient

TENANT_ID = os.environ["TENANT_ID"]
MONGO_URI = os.environ["MONGO_URI"]
PANEL_SETUP_TOKEN = os.environ["PANEL_SETUP_TOKEN"]
PANEL_SESSION_SECRET = os.environ["PANEL_SESSION_SECRET"]
ACCESS_TOKEN = os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
TENANT_ENABLED = os.environ.get("WHATSAPP_CLOUD_ENABLED", "true").strip().lower() != "false"

mongo_client = MongoClient(MONGO_URI)
db = mongo_client.get_default_database()
auth_col = db["panel_auth"]
sessions_col = db["sessions"]
messages_col = db["messages"]
signups_col = db["signups"]

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
            "tom_descricao": tom.get("descricao", ""),
            "tom_emoji": tom.get("emoji", ""),
            "escalacao_nome": escalacao.get("nome", ""),
            "escalacao_telefone": escalacao.get("telefone", ""),
            "escalacao_pronome": escalacao.get("pronome", "ele"),
            "servicos": _format_pipe_lines(config.get("servicos"), ["nome", "descricao", "publico_alvo"]),
            "como_trabalhamos": _format_label_lines(config.get("como_trabalhamos")),
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
