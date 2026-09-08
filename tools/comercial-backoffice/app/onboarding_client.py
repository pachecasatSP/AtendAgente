"""Cliente HTTP fino pro `/api/admin/*` do onboarding-service — o
backoffice roda num servidor Hetzner diferente (2.28.15.6, namespace
`hermes`) do onboarding-service (62.238.103.17, namespace
`atendagente`), sem rede privada entre os dois (confirmado por SSH,
ver plano de deploy). Qualquer coisa sobre tenant passa por aqui, nunca
por acesso direto ao Mongo do atendagente — mesmo padrão que o
admin-mcp já usa pra falar com o onboarding-service a partir da Duda.
"""
import os

import httpx

ONBOARDING_BASE_URL = os.environ.get("ONBOARDING_BASE_URL", "https://onboarding.atendpragente.com.br")
ONBOARDING_ADMIN_KEY = os.environ["ONBOARDING_ADMIN_KEY"]

_headers = {"x-admin-api-key": ONBOARDING_ADMIN_KEY}


def _get(path: str, **kwargs) -> httpx.Response:
    return httpx.get(f"{ONBOARDING_BASE_URL}{path}", headers=_headers, timeout=15, **kwargs)


def _post(path: str, json: dict | None = None) -> httpx.Response:
    return httpx.post(f"{ONBOARDING_BASE_URL}{path}", headers=_headers, json=json or {}, timeout=15)


def listar_tenants() -> list[dict]:
    resp = _get("/api/admin/tenants")
    resp.raise_for_status()
    return resp.json()


def listar_alertas_uso() -> list[dict]:
    resp = _get("/api/admin/usage-alerts")
    resp.raise_for_status()
    return resp.json()


def ativar_tenant(tenant_id: str, enabled: bool) -> dict:
    resp = _post(f"/api/admin/tenants/{tenant_id}/enabled", {"enabled": enabled})
    resp.raise_for_status()
    return resp.json()


def atribuir_vendedor(tenant_id: str, vendedor_id: str, origem_venda: str) -> dict:
    """Chama a nova rota admin (ver plano — precisa existir no
    onboarding-service) que grava vendedor_id/origem_venda no doc de
    `signups`. onboarding-service continua dono do schema — o
    backoffice nunca escreve nessa coleção diretamente."""
    resp = _post(
        f"/api/admin/tenants/{tenant_id}/atribuir-vendedor",
        {"vendedor_id": vendedor_id, "origem_venda": origem_venda},
    )
    resp.raise_for_status()
    return resp.json()
