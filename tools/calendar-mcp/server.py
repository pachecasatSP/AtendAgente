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
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config as BotoConfig
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

# Mesmo bucket compartilhado das fotos do catálogo (ver
# tools/tenant-panel/storage.py e infra_object_storage_cdn na memória
# do projeto) — prefixo próprio pra não misturar com as fotos. Envio do
# .ics é best-effort: se o bucket não estiver configurado, o
# agendamento continua funcionando normal, só sem o anexo.
OBJECT_STORAGE_ENDPOINT = os.environ.get("OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "")
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "")
OBJECT_STORAGE_CDN_BASE_URL = os.environ.get("OBJECT_STORAGE_CDN_BASE_URL", "").rstrip("/")
ICS_PREFIX = os.environ.get("OBJECT_STORAGE_ICS_PREFIX", "atendpragente-agenda-ics").strip("/")

# Encurtador de link do .ics (Fase 10, 2026-08-19) — Worker + KV própria
# na Cloudflare (tools/calendar-mcp/shortlink-worker.js), pra não mandar pro
# cliente uma URL de 90+ caracteres com UUID de 32 dígitos. Best-effort
# igual o resto do fluxo de .ics: se não estiver configurado ou a
# chamada falhar, cai pra URL longa direto do object storage — nunca
# bloqueia o agendamento por causa disso.
CLOUDFLARE_KV_TOKEN = os.environ.get("CLOUDFLARE_KV_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_KV_NAMESPACE_ID = os.environ.get("CLOUDFLARE_KV_NAMESPACE_ID", "")
SHORTLINK_BASE_URL = os.environ.get("SHORTLINK_BASE_URL", "https://link.atendpragente.com.br").rstrip("/")

_mongo = MongoClient(MONGO_URI)
_db = _mongo.get_default_database()
_signups = _db["signups"]

_current_tenant: contextvars.ContextVar[dict] = contextvars.ContextVar("current_tenant")

_service_account_info: dict | None = None
_calendar_service = None
_s3_client = None


def _object_storage_configured() -> bool:
    return bool(
        OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_BUCKET
        and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY
        and OBJECT_STORAGE_CDN_BASE_URL
    )


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3", endpoint_url=OBJECT_STORAGE_ENDPOINT,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def build_ics(uid: str, summary: str, start: datetime, end: datetime) -> bytes:
    def fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AtendPraGente//Agendamento//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@atendpragente.com.br",
        f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{_ics_escape(summary)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def upload_ics(tenant_id: str, ics_bytes: bytes) -> str | None:
    """Melhor esforço — não derruba o agendamento se o bucket estiver
    fora do ar ou não configurado."""
    if not _object_storage_configured():
        return None
    key = f"{ICS_PREFIX}/{tenant_id}/{uuid.uuid4().hex}.ics"
    try:
        _get_s3_client().put_object(
            Bucket=OBJECT_STORAGE_BUCKET, Key=key, Body=ics_bytes,
            ContentType="text/calendar; charset=utf-8", ACL="public-read",
        )
    except Exception:
        return None
    return f"{OBJECT_STORAGE_CDN_BASE_URL}/{key}"


def _shortlink_configured() -> bool:
    return bool(CLOUDFLARE_KV_TOKEN and CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_KV_NAMESPACE_ID)


def shorten_url(long_url: str) -> str:
    """Grava `long_url` na KV do Worker de shortlinks (id curto de 8
    caracteres) e devolve o link em link.atendpragente.com.br — best
    effort, devolve `long_url` sem alterar se não estiver configurado ou
    a chamada falhar (nunca trava o agendamento por causa disso)."""
    if not _shortlink_configured():
        return long_url
    short_id = secrets.token_urlsafe(6)
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{CLOUDFLARE_KV_NAMESPACE_ID}/values/{short_id}"
    )
    req = urllib.request.Request(
        url, data=long_url.encode("utf-8"), method="PUT",
        headers={"Authorization": f"Bearer {CLOUDFLARE_KV_TOKEN}", "Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except Exception:
        return long_url
    if not body.get("success"):
        return long_url
    return f"{SHORTLINK_BASE_URL}/{short_id}"


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


def check_and_book(calendar_email: str, start: datetime, duration_minutes: int, titulo: str, tenant_id: str) -> dict:
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
        # Contas de serviço não conseguem gerar Google Meet automático
        # sem "domain-wide delegation" (só existe em Google Workspace
        # administrado — não serve pra Gmail pessoal/pequena empresa,
        # que é o caso comum aqui). Confirmado reproduzindo o erro
        # (400 "Invalid conference type value") — é limitação
        # documentada da Google, não intermitência. Em vez de falhar o
        # agendamento inteiro por causa do Meet, cria sem conferência:
        # o cliente ainda recebe o horário marcado e o link do evento.
        if e.resp.status == 400 and "conference" in str(e).lower():
            event_body_sem_meet = {k: v for k, v in event_body.items() if k != "conferenceData"}
            try:
                created = service.events().insert(calendarId=calendar_email, body=event_body_sem_meet).execute()
            except HttpError as e2:
                return {"ok": False, "motivo": "erro_google", "detalhe": str(e2)}
        else:
            return {"ok": False, "motivo": "erro_google", "detalhe": str(e)}

    ics_bytes = build_ics(created.get("id", uuid.uuid4().hex), titulo, start, end)
    link_ics = upload_ics(tenant_id, ics_bytes)
    if link_ics:
        link_ics = shorten_url(link_ics)

    return {
        "ok": True,
        "link_evento": created.get("htmlLink"),
        "link_meet": created.get("hangoutLink"),  # None se essa conta não suporta Meet via API
        "link_ics": link_ics,  # None se o bucket não estiver configurado — nunca bloqueia o agendamento
    }


mcp = MCPServer("agenda")


@mcp.tool()
def criar_agendamento(data_hora_inicio_iso: str, titulo: str) -> dict:
    """Confere disponibilidade e cria um evento na Google Agenda do
    negócio, com Google Meet quando a conta suportar (nem toda agenda
    consegue gerar Meet automático, ver comentário em check_and_book).
    `data_hora_inicio_iso`: data e hora no formato ISO 8601 (ex:
    "2026-08-20T15:00:00"), horário de Brasília se não vier com fuso.
    `titulo`: assunto do agendamento (ex: "Consulta com Fulano").
    Duração vem da configuração do negócio no painel (padrão 30 minutos).

    Devolve {"ok": true, "link_evento": ..., "link_meet": ..., "link_ics": ...}
    se conseguiu marcar (`link_meet`/`link_ics` podem vir null — normal,
    sem problema; `link_ics` é um arquivo de convite universal, funciona
    em qualquer app de calendário, não só Google — sempre mande esse
    junto se vier preenchido), ou {"ok": false, "motivo": "..."} se não —
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

    return check_and_book(calendar_email, start, duracao, titulo, tenant["tenant_id"])


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


async def listar_eventos_route(request: Request) -> JSONResponse:
    """Próximos eventos da agenda do tenant — leitura, nunca cria/altera
    nada (ver criar_agendamento pra isso). Usada pela página /agenda do
    painel (ver tools/tenant-panel/app.py)."""
    tenant = request.state.tenant
    config = tenant.get("config") or {}
    calendar_email = config.get("google_calendar_email")
    if not calendar_email:
        return JSONResponse({"ok": False, "motivo": "agenda_nao_configurada"})

    try:
        service = _get_calendar_service()
        now = datetime.now(SAO_PAULO_TZ)
        result = service.events().list(
            calendarId=calendar_email,
            timeMin=now.isoformat(),
            maxResults=25,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return JSONResponse({"ok": False, "motivo": "sem_acesso_a_agenda"})
        return JSONResponse({"ok": False, "motivo": "erro_google", "detalhe": str(e)})
    except RuntimeError as e:
        return JSONResponse({"ok": False, "motivo": "nao_configurado", "detalhe": str(e)}, status_code=500)

    eventos = [
        {
            "titulo": ev.get("summary") or "(sem título)",
            "inicio": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"),
            "link_evento": ev.get("htmlLink"),
            "link_meet": ev.get("hangoutLink"),
        }
        for ev in result.get("items", [])
    ]
    return JSONResponse({"ok": True, "eventos": eventos})


_ALLOWED_HOSTS_BASE = ["calendar-mcp.atendagente.svc.cluster.local", "calendar-mcp", "localhost", "127.0.0.1"]

app = mcp.streamable_http_app(
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        # O Host header vem com a porta (não é 80/443) — a checagem de
        # DNS-rebinding compara a string inteira, então precisa das duas
        # variantes (com e sem `:8000`), senão dá 421 Misdirected Request.
        allowed_hosts=_ALLOWED_HOSTS_BASE + [f"{h}:8000" for h in _ALLOWED_HOSTS_BASE],
        allowed_origins=[],
    ),
)
app.add_middleware(TenantAuthMiddleware)
app.add_route("/service-account-email", service_account_email_route, methods=["GET"])
app.add_route("/testar-conexao", testar_conexao_route, methods=["POST"])
app.add_route("/listar-eventos", listar_eventos_route, methods=["GET"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
