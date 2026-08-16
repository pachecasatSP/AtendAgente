"""Servidor MCP multi-tenant de agendamento — cada Hermes de tenant
conecta nele via `mcp_servers.agenda` no config.yaml (escrito direto no
provisionamento por `enable_calendar_mcp` em
tools/provision-tenant/provision_tenant.py, não via `hermes mcp add`
interativo — esse comando espera respostas por prompt, frágil pra
automação sem TTY). Expõe uma única ferramenta, `criar_agendamento`,
que confere disponibilidade e cria o evento na Google Agenda do
tenant. Ver specs/agendamento-google-calendar.md pro desenho completo.

Autenticação/roteamento: cada tenant tem seu próprio
CALENDAR_MCP_TOKEN (gerado no provisionamento, mesmo padrão do
PANEL_SETUP_TOKEN), enviado como Bearer no header Authorization. O
middleware busca no Mongo (mesma coleção `signups` do
onboarding-service) qual tenant esse token pertence, e guarda o doc
inteiro num contextvar — os tools do MCP não recebem o Request
diretamente, só os argumentos declarados.

Só uma conta de serviço da Google Cloud (compartilhada entre todos os
tenants) fala com a Calendar API — cada tenant só precisa compartilhar
a própria agenda com o e-mail dela (ver /service-account-email, rota
pública que o painel usa pra mostrar essa instrução). Sem chave por
tenant: se o tenant parar de compartilhar, as chamadas passam a falhar
com erro claro (`sem_acesso_a_agenda`), nada quebra silenciosamente.
"""
import contextvars
import json
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pymongo import MongoClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MONGO_URI = os.environ["MONGO_URI"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_mongo = MongoClient(MONGO_URI)
_db = _mongo.get_default_database()
_signups = _db["signups"]

_current_tenant: contextvars.ContextVar[dict] = contextvars.ContextVar("current_tenant")

_service_account_info: dict | None = None
_calendar_service = None


def _load_service_account_info() -> dict | None:
    global _service_account_info
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None
    if _service_account_info is None:
        _service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    return _service_account_info


def _service_account_email() -> str:
    info = _load_service_account_info()
    return (info or {}).get("client_email", "")


def _get_calendar_service():
    """Cliente da Calendar API, montado uma vez só por processo — a
    conta de serviço é a mesma pra todos os tenants, só a agenda de
    destino (`calendarId`) muda por chamada."""
    global _calendar_service
    info = _load_service_account_info()
    if not info:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON não configurado neste servidor")
    if _calendar_service is None:
        creds = service_account.Credentials.from_service_account_info(info, scopes=CALENDAR_SCOPES)
        _calendar_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _calendar_service


def _parse_start(data_hora_inicio_iso: str) -> datetime:
    dt = datetime.fromisoformat(data_hora_inicio_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAO_PAULO_TZ)
    return dt


def check_and_book(calendar_email: str, start: datetime, duration_minutes: int, titulo: str) -> dict:
    service = _get_calendar_service()
    end = start + timedelta(minutes=duration_minutes)

    try:
        freebusy = service.freebusy().query(body={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": "America/Sao_Paulo",
            "items": [{"id": calendar_email}],
        }).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return {"ok": False, "motivo": "sem_acesso_a_agenda"}
        return {"ok": False, "motivo": "erro_google", "detalhe": str(e)}

    calendario = freebusy.get("calendars", {}).get(calendar_email, {})
    if calendario.get("errors"):
        return {"ok": False, "motivo": "sem_acesso_a_agenda"}
    if calendario.get("busy"):
        return {"ok": False, "motivo": "horario_ocupado"}

    event_body = {
        "summary": titulo,
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/Sao_Paulo"},
        "conferenceData": {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    try:
        created = service.events().insert(
            calendarId=calendar_email, body=event_body, conferenceDataVersion=1
        ).execute()
    except HttpError as e:
        return {"ok": False, "motivo": "erro_google", "detalhe": str(e)}

    return {
        "ok": True,
        "link_evento": created.get("htmlLink"),
        "link_meet": created.get("hangoutLink"),
    }


mcp = MCPServer("agenda")


@mcp.tool()
def criar_agendamento(data_hora_inicio_iso: str, titulo: str) -> dict:
    """Confere disponibilidade e cria um evento na Google Agenda do
    negócio, com Google Meet habilitado. `data_hora_inicio_iso`: data e
    hora no formato ISO 8601 (ex: "2026-08-20T15:00:00"), horário de
    Brasília se não vier com fuso. `titulo`: assunto do agendamento
    (ex: "Consulta com Fulano").  Duração vem da configuração do
    negócio no painel (padrão 30 minutos).

    Devolve {"ok": true, "link_evento": ..., "link_meet": ...} se
    conseguiu marcar, ou {"ok": false, "motivo": "..."} se não —
    motivos possíveis: "horario_ocupado" (peça outro horário, nunca
    insista no mesmo nem invente disponibilidade), "agenda_nao_configurada"
    (esse negócio ainda não configurou a agenda), "sem_acesso_a_agenda"
    (configurado errado, avise que vai verificar)."""
    tenant = _current_tenant.get()
    config = tenant.get("config") or {}
    calendar_email = config.get("google_calendar_email")
    if not calendar_email:
        return {"ok": False, "motivo": "agenda_nao_configurada"}
    duracao = int(config.get("agendamento_duracao_minutos") or 30)

    try:
        start = _parse_start(data_hora_inicio_iso)
    except ValueError:
        return {"ok": False, "motivo": "data_invalida"}

    return check_and_book(calendar_email, start, duracao, titulo)


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Resolve o tenant pelo Bearer token e guarda no contextvar.
    `/service-account-email` fica de fora (rota pública, não expõe
    dado de tenant nenhum — só serve pro painel mostrar a instrução de
    compartilhamento antes mesmo do tenant ter um token configurado)."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/service-account-email":
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        tenant = _signups.find_one({"status": "live", "config.calendar_mcp_token": token})
        if not tenant:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        request.state.tenant = tenant
        reset_token = _current_tenant.set(tenant)
        try:
            return await call_next(request)
        finally:
            _current_tenant.reset(reset_token)


async def service_account_email_route(request: Request) -> JSONResponse:
    return JSONResponse({"email": _service_account_email()})


async def testar_conexao_route(request: Request) -> JSONResponse:
    """Verificação de conectividade pro botão "Testar conexão" do
    painel (ver tools/tenant-panel/app.py) — não cria evento nenhum, só
    confere se a conta de serviço consegue ler o freebusy do e-mail
    informado, o que só funciona se o compartilhamento foi feito
    certo."""
    tenant = request.state.tenant
    payload = await request.json()
    calendar_email = (payload.get("google_calendar_email") or "").strip()
    if not calendar_email:
        return JSONResponse({"ok": False, "motivo": "email_vazio"}, status_code=400)

    try:
        service = _get_calendar_service()
        now = datetime.now(SAO_PAULO_TZ)
        service.freebusy().query(body={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(minutes=1)).isoformat(),
            "timeZone": "America/Sao_Paulo",
            "items": [{"id": calendar_email}],
        }).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return JSONResponse({"ok": False, "motivo": "sem_acesso_a_agenda"})
        return JSONResponse({"ok": False, "motivo": "erro_google", "detalhe": str(e)})
    except RuntimeError as e:
        return JSONResponse({"ok": False, "motivo": "nao_configurado", "detalhe": str(e)}, status_code=500)

    return JSONResponse({"ok": True})


app = mcp.streamable_http_app(
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        allowed_hosts=["calendar-mcp.atendagente.svc.cluster.local", "calendar-mcp", "localhost", "127.0.0.1"],
        allowed_origins=None,
    ),
)
app.add_middleware(TenantAuthMiddleware)
app.add_route("/service-account-email", service_account_email_route, methods=["GET"])
app.add_route("/testar-conexao", testar_conexao_route, methods=["POST"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
