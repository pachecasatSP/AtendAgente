"""Cliente HTTP fino pro `/api/admin/*` do onboarding-service. Backoffice
e onboarding rodam no mesmo cluster (namespace `atendagente-admin`), mas o
backoffice tem Mongo próprio e é o onboarding quem é dono dos `signups`:
qualquer coisa sobre tenant passa por aqui, nunca por acesso direto ao
Mongo dos tenants — mesmo padrão que o admin-mcp usa a partir da Duda.

`ONBOARDING_BASE_URL` aponta por padrão pro host público; dentro do
cluster dá pra apontar pro Service interno (`http://onboarding-service.
atendagente-admin.svc.cluster.local:8000`) setando a variável no manifesto.
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


def listar_lixeira() -> dict:
    """{"cancelados": [{tenant_id, ...}], "excluidos_recentemente": [{tenant_id, ...}]}
    — fonte do churn: ver app/churn.py."""
    resp = _get("/api/admin/lixeira")
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
