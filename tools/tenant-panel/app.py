"""Painel de conversas por tenant (Fase 5 UI, movida pra dentro do
provisionamento — cada tenant tem o seu, não um painel central).

Lê TENANT_ID do ambiente e consulta o MongoDB compartilhado
(sessions/messages, sincronizados pelo sidecar mongo-sync), filtrando
sempre por esse tenant_id. Protegido por HTTP Basic Auth com
credenciais geradas no provisionamento (PANEL_USER/PANEL_PASSWORD).

Layout de duas colunas (lista à esquerda, thread à direita, estilo
WhatsApp Web) — a lista/thread carregam via fetch (rotas /painel/api/*),
sem reload de página. O badge de "não lidas" é calculado só no
navegador (localStorage comparando message_count com o que já foi
visto) — não existe conceito de usuário/leitura no backend.
"""
import os
import secrets as secrets_module
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient

TENANT_ID = os.environ["TENANT_ID"]
MONGO_URI = os.environ["MONGO_URI"]
PANEL_USER = os.environ["PANEL_USER"]
PANEL_PASSWORD = os.environ["PANEL_PASSWORD"]

mongo_client = MongoClient(MONGO_URI)
db = mongo_client.get_default_database()

app = FastAPI(title=f"Painel — {TENANT_ID}")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    user_ok = secrets_module.compare_digest(credentials.username, PANEL_USER)
    pass_ok = secrets_module.compare_digest(credentials.password, PANEL_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Credenciais inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )


def fmt_ts(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(ts)


@app.get("/painel", response_class=HTMLResponse)
def panel_shell(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse(request, "index.html", {"tenant_id": TENANT_ID})


@app.get("/painel/api/sessions")
def api_sessions(_: None = Depends(require_auth)) -> list[dict]:
    sessions = list(
        db["sessions"]
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
        }
        for s in sessions
    ]


@app.get("/painel/api/messages/{session_id}")
def api_messages(session_id: str, _: None = Depends(require_auth)) -> list[dict]:
    messages = list(
        db["messages"]
        .find({"tenant_id": TENANT_ID, "session_id": session_id})
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


@app.get("/health")
def health():
    return {"status": "ok", "tenant_id": TENANT_ID}
