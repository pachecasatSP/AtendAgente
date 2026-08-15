"""Servidor MCP de ferramentas administrativas — só a Duda (hermes-duda)
conecta nele, via `hermes mcp add` com `--auth header` (Bearer
MCP_AUTH_TOKEN). Expõe: criar token de gratuidade, listar tenants, medir
recursos de um tenant, pausar/reativar um tenant.

Segurança em duas camadas independentes, nenhuma delas decidida pela IA:
1. MCP_AUTH_TOKEN — Bearer estático checado por middleware antes de
   qualquer chamada chegar nas tools (protege o endpoint em si).
2. ADMIN_PIN — cada tool exige esse PIN como argumento; comparado no
   servidor (secrets.compare_digest), nunca a Duda "decidindo" que quem
   está falando é confiável. Errar o PIN nunca revela nada, só nega.

Esse servidor não fala com kubectl/Mongo diretamente — delega tudo pro
onboarding-service (`/api/admin/*`), que já tem RBAC/kubeconfig restrito
ao namespace atendagente. ONBOARDING_ADMIN_KEY autentica essa chamada
servidor-a-servidor (também nunca visto pela IA).
"""
import os
import secrets

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

MCP_AUTH_TOKEN = os.environ["MCP_AUTH_TOKEN"]
ADMIN_PIN = os.environ["ADMIN_PIN"]
ONBOARDING_BASE_URL = os.environ.get("ONBOARDING_BASE_URL", "https://onboarding.atendpragente.com.br")
ONBOARDING_ADMIN_KEY = os.environ["ONBOARDING_ADMIN_KEY"]
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "admin-mcp.atendpragente.com.br")

mcp = MCPServer("admin")


def _check_pin(pin: str) -> str | None:
    """Retorna uma mensagem de erro se o PIN estiver errado, senão None."""
    if not secrets.compare_digest((pin or "").strip(), ADMIN_PIN):
        return "PIN incorreto. Essa ação não foi executada."
    return None


def _admin_request(method: str, path: str, **kwargs) -> dict:
    resp = httpx.request(
        method,
        f"{ONBOARDING_BASE_URL}{path}",
        headers={"x-admin-api-key": ONBOARDING_ADMIN_KEY},
        timeout=20.0,
        **kwargs,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def convite(pin: str, nota: str = "") -> dict:
    """Gera um link de cadastro gratuito (sem cobrança) pra um novo
    cliente da AtendPraGente. O token é de uso único. `nota` é livre,
    só pra lembrar depois pra quem foi gerado (ex: nome do cliente)."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("POST", "/api/admin/free-tokens", json={"nota": nota})


@mcp.tool()
def listar(pin: str) -> dict:
    """Lista todos os tenants (clientes) ativos da AtendPraGente, com
    plano e se o cadastro foi gratuito."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return {"tenants": _admin_request("GET", "/api/admin/tenants")}


@mcp.tool()
def uso(pin: str, tenant_id: str) -> dict:
    """Mostra uso de um tenant específico: sessões, mensagens e última
    atividade."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("GET", f"/api/admin/tenants/{tenant_id}/usage")


@mcp.tool()
def ativar(pin: str, tenant_id: str, ativo: bool) -> dict:
    """Pausa (ativo=false) ou reativa (ativo=true) o WhatsApp de um
    tenant. Reversível — não apaga nada, só desliga/liga o atendimento.
    Enquanto pausado, o painel do cliente mostra um aviso de manutenção."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request(
        "POST", f"/api/admin/tenants/{tenant_id}/enabled", json={"enabled": ativo}
    )


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("authorization") != f"Bearer {MCP_AUTH_TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = mcp.streamable_http_app(
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[PUBLIC_HOST, "localhost", "127.0.0.1"],
        allowed_origins=[f"https://{PUBLIC_HOST}"],
    ),
)
app.add_middleware(BearerAuthMiddleware)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
