"""Painel de conversas por tenant (Fase 5 UI, movida pra dentro do
provisionamento — cada tenant tem o seu, não um painel central).

Lê TENANT_ID do ambiente e consulta o MongoDB compartilhado
(sessions/messages, sincronizados pelo sidecar mongo-sync), filtrando
sempre por esse tenant_id. Protegido por HTTP Basic Auth com
credenciais geradas no provisionamento (PANEL_USER/PANEL_PASSWORD).
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
def list_conversations(request: Request, _: None = Depends(require_auth)):
    sessions = list(
        db["sessions"]
        .find({"tenant_id": TENANT_ID})
        .sort("last_activity_at", -1)
        .limit(200)
    )
    for s in sessions:
        s["last_activity_fmt"] = fmt_ts(s.get("last_activity_at"))
    return templates.TemplateResponse(
        request, "list.html", {"tenant_id": TENANT_ID, "sessions": sessions}
    )


@app.get("/painel/{session_id}", response_class=HTMLResponse)
def view_thread(session_id: str, request: Request, _: None = Depends(require_auth)):
    messages = list(
        db["messages"]
        .find({"tenant_id": TENANT_ID, "session_id": session_id})
        .sort("timestamp", 1)
    )
    if not messages:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    for m in messages:
        m["timestamp_fmt"] = fmt_ts(m.get("timestamp"))
    return templates.TemplateResponse(
        request,
        "thread.html",
        {
            "tenant_id": TENANT_ID,
            "session_id": session_id,
            "messages": messages,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "tenant_id": TENANT_ID}
