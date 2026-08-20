"""Servidor MCP de ferramentas administrativas — só a Duda (hermes-duda)
conecta nele, via `hermes mcp add` com `--auth header` (Bearer
MCP_AUTH_TOKEN). Expõe: criar token de gratuidade, listar tenants, medir
recursos de um tenant, pausar/reativar um tenant, alertar sobre tenants
perto do limite do plano, listar cadastros travados no provisionamento e
refazer o provisionamento de um deles, autorizar cancelamentos, resetar
a senha do painel de um tenant, (Fase 9) consultar a lixeira de
cancelados e restaurar um tenant dentro da janela de 10 dias, e (Fase
10) conceder fotos extras de catálogo além do limite do plano.

Segurança em duas camadas independentes, nenhuma delas decidida pela IA:
1. MCP_AUTH_TOKEN — Bearer estático checado por middleware antes de
   qualquer chamada chegar nas tools (protege o endpoint em si).
2. Por tool: a maioria exige ADMIN_PIN como argumento, comparado no
   servidor (secrets.compare_digest) — nunca a Duda "decidindo" que
   quem está falando é confiável, errar o PIN nunca revela nada, só
   nega. Exceção é `resetar_senha`: ela fala direto com o cliente (não
   com o admin), então a prova de identidade é um dado do próprio
   cadastro do tenant (CPF/CNPJ), também comparado no servidor — mesma
   lógica de "nunca a IA decide", só muda o que está sendo comparado.

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
def alertas(pin: str) -> dict:
    """Lista tenants perto do limite do plano (80%+) ou já estourando
    (100%+) — contagem de contatos únicos no mês, atualizada 1x/dia.
    Não pausa nem cobra nada sozinho, só avisa pra decisão manual."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return {"alertas": _admin_request("GET", "/api/admin/usage-alerts")}


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


@mcp.tool()
def pendentes(pin: str) -> dict:
    """Lista cadastros com pagamento confirmado que travaram no
    provisionamento (ex: erro técnico na hora de criar o tenant) — é o
    que o cliente vê na tela de aguardando quando dá erro. Cada item tem
    o signup_id pra usar em `provisionar`."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return {"pendentes": _admin_request("GET", "/api/admin/signups/failed")}


@mcp.tool()
def provisionar(pin: str, signup_id: str) -> dict:
    """Refaz o provisionamento de um cadastro travado em 'failed' (veja
    `pendentes`), sem exigir um novo pagamento — usa o mesmo pagamento já
    confirmado. Só usar depois de entender/corrigir a causa do erro
    técnico original, senão vai falhar de novo do mesmo jeito."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("POST", f"/api/admin/signups/{signup_id}/retry-provisioning")


@mcp.tool()
def cancelamentos(pin: str) -> dict:
    """Lista pedidos de cancelamento de assinatura feitos por clientes no
    próprio painel (botão 'Cancelar assinatura' em Configurações). Cada
    item tem o signup_id pra usar em `cancelar` — nada é cancelado
    sozinho, é sempre você autorizando um a um."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return {"cancelamentos": _admin_request("GET", "/api/admin/cancelamentos")}


@mcp.tool()
def cancelar(pin: str, signup_id: str) -> dict:
    """Autoriza um pedido de cancelamento (veja `cancelamentos`): cancela
    a cobrança recorrente na Asaas (não estorna o que já foi cobrado) e
    para o pod do tenant. Não apaga conversas nem dados — o tenant entra
    numa "lixeira" de 10 dias (ver `lixeira`), onde ainda dá pra
    restaurar via `restaurar`. Passados os 10 dias sem restauração, um
    processo automático apaga tudo de vez (DNS, infra, dados) — sem
    volta."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("POST", f"/api/admin/signups/{signup_id}/cancelar")


@mcp.tool()
def lixeira(pin: str) -> dict:
    """Lista tenants cancelados: quanto tempo falta (em dias) até a
    exclusão definitiva automática, e os que já foram excluídos nos
    últimos 30 dias. Use pra responder "ainda dá pra recuperar?" — não
    executa nem decide nada."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("GET", "/api/admin/lixeira")


@mcp.tool()
def restaurar(pin: str, signup_id: str) -> dict:
    """Gera um link de pagamento novo pra restaurar um tenant cancelado
    que ainda está dentro da janela de 10 dias (veja `lixeira`). A
    assinatura antiga foi cancelada de verdade na Asaas — não tem como só
    "religar", precisa de uma cobrança nova. Manda esse link pro cliente;
    o atendimento só volta depois que ele confirmar o pagamento (nunca
    antes). Depois dos 10 dias, essa ferramenta para de funcionar — o
    tenant já foi ou está prestes a ser excluído de vez."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request("POST", f"/api/admin/signups/{signup_id}/reativar-checkout")


@mcp.tool()
def fotos_extra(pin: str, tenant_id: str, quantidade: int) -> dict:
    """Concede fotos extras de catálogo além do limite do plano do
    tenant (limites: Entrada 25, Começando 75, Crescendo 250, Sem
    Limite sem trava). Use só depois de combinar com o cliente a
    cobrança avulsa (R$1,00/foto/mês) — isso aqui só libera o limite
    técnico, não cria cobrança nenhuma sozinho, é você quem já
    combinou e vai registrar a cobrança à parte. `quantidade` é o
    total concedido (não soma ao que já tinha) — pra tirar o extra
    depois, chame de novo com 0."""
    erro = _check_pin(pin)
    if erro:
        return {"erro": erro}
    return _admin_request(
        "POST", f"/api/admin/tenants/{tenant_id}/foto-limite-extra", json={"extra": quantidade}
    )


@mcp.tool()
def resetar_senha(tenant_id: str, cpf_cnpj: str) -> dict:
    """'Esqueci minha senha' — use direto na conversa quando o cliente
    pedir, sem precisar de PIN de admin. Antes de chamar, peça pro
    cliente confirmar o CPF/CNPJ do cadastro dele; passe exatamente o
    que ele informou em `cpf_cnpj`. O servidor confere contra o
    cadastro — se não bater, a chamada falha sem revelar mais nada (não
    tente adivinhar ou repetir com variações). Se bater, apaga o
    usuário/senha atual do painel e reabre o link de configuração de
    uso único (o mesmo gerado no provisionamento) — a resposta traz
    panel_setup_url, que é esse link pra mandar pro cliente. Não apaga
    conversas nem outros dados."""
    return _admin_request(
        "POST", f"/api/admin/tenants/{tenant_id}/password-reset", json={"cpf_cnpj": cpf_cnpj}
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
