"""Backoffice comercial — CRM de leads + apuração de comissão pra
vendedores/parceiros externos do AtendPraGente (specs/comissao-vendedores.md).

Roda no mesmo servidor da Duda (2.28.15.6, namespace `hermes`), com
Mongo PRÓPRIO (não o do atendagente) — ver "Topologia de deploy" no
plano. Qualquer coisa sobre tenant (atribuir vendedor, listar, uso)
passa pela API HTTP do onboarding-service via `app/onboarding_client.py`,
nunca por acesso direto ao Mongo do outro cluster.

Estilo de código deliberadamente igual ao onboarding-service/tenant-panel:
FastAPI single-file, sem Pydantic/ODM, documentos Mongo como dicts crus.
"""
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import onboarding_client, store

BACKOFFICE_SESSION_SECRET = os.environ["BACKOFFICE_SESSION_SECRET"]
ADMIN_PIN = os.environ.get("ADMIN_PIN", "")

app = FastAPI(title="AtendPraGente — Backoffice Comercial")
app.add_middleware(SessionMiddleware, secret_key=BACKOFFICE_SESSION_SECRET, session_cookie="backoffice_session")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def require_session(request: Request) -> dict:
    vendedor_id = request.session.get("vendedor_id")
    if not vendedor_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    vendedor = store.get_vendedor(vendedor_id)
    if not vendedor or vendedor.get("status") != "ativo":
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sessão inválida")
    return vendedor


def require_admin_role(request: Request) -> dict:
    vendedor = require_session(request)
    if vendedor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Só admin acessa isso")
    return vendedor


def _check_pin(pin: str) -> None:
    """Mesmo padrão do admin-mcp — PIN pra ações sensíveis (pausar
    tenant, etc.), nunca decidido pela IA, só o humano digita."""
    if not ADMIN_PIN or not secrets.compare_digest(pin, ADMIN_PIN):
        raise HTTPException(status_code=403, detail="PIN inválido")


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------- login

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, erro: str | None = None):
    if request.session.get("vendedor_id"):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html", {"erro": erro})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    vendedor = store.get_vendedor_by_username(username.strip())
    if vendedor and vendedor.get("status") == "ativo" and store.verify_password(password, vendedor):
        request.session["vendedor_id"] = vendedor["_id"]
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"erro": "Usuário ou senha incorretos."}, status_code=400
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


# ----------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    vendedor = require_session(request)
    comissao = store.comissao_do_mes(vendedor["_id"])
    bate_bonus = store.bate_gatilho_bonus_rampa(comissao["qtd_clientes_ativos"])
    leads = store.list_leads_by_vendedor(vendedor["_id"])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "vendedor": vendedor,
            "comissao": comissao,
            "bate_bonus_rampa": bate_bonus,
            "leads": leads,
            "meta_tier": store.META_CLIENTES_POR_TIER,
        },
    )


# --------------------------------------------------------------------- leads

@app.post("/leads")
def criar_lead(
    request: Request,
    negocio: str = Form(...),
    contato: str = Form(...),
    cidade: str = Form(...),
    origem: str = Form(""),
    plano_alvo: str = Form(""),
):
    vendedor = require_session(request)
    store.create_lead(negocio.strip(), contato.strip(), cidade.strip(), origem.strip(), vendedor["_id"], plano_alvo or None)
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/status")
def atualizar_status_lead(request: Request, lead_id: str, status: str = Form(...)):
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if status not in ("novo", "em_conversa", "proposta", "fechado", "perdido"):
        raise HTTPException(status_code=400, detail="Status inválido")
    store.update_lead_status(lead_id, status)
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/atribuir-tenant")
def atribuir_tenant(request: Request, lead_id: str, tenant_id: str = Form(...)):
    """Lead virou cliente de verdade: busca o signup no
    onboarding-service, marca vendedor_id/origem_venda lá, e trava a
    comissão aqui na faixa atual do vendedor (specs/comissao-vendedores.md)."""
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")

    tenants = onboarding_client.listar_tenants()
    tenant = next((t for t in tenants if t["tenant_id"] == tenant_id.strip()), None)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado no onboarding-service")

    onboarding_client.atribuir_vendedor(tenant_id.strip(), vendedor["_id"], origem_venda=f"lead:{lead_id}")

    plano = tenant.get("plano", "")
    mrr = store.PRECO_POR_PLANO.get(plano)
    if mrr is None:
        raise HTTPException(status_code=400, detail=f"Plano '{plano}' sem preço de tabela cadastrado (ex.: plano Sem limite) — atribuir manualmente no Mongo por enquanto")
    store.atribuir_tenant(vendedor["_id"], tenant_id.strip(), plano, mrr)
    store.update_lead_status(lead_id, "fechado")
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------- admin: vendedores

@app.get("/admin/vendedores", response_class=HTMLResponse)
def admin_listar_vendedores(request: Request):
    admin = require_admin_role(request)
    vendedores = store.list_vendedores(status=None)
    dados = []
    for v in vendedores:
        comissao = store.comissao_do_mes(v["_id"])
        dados.append({**v, "comissao": comissao})
    return templates.TemplateResponse(request, "admin_vendedores.html", {"vendedores": dados, "vendedor": admin})


@app.post("/admin/vendedores")
def admin_criar_vendedor(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    telefone: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("vendedor"),
    cidade_designada: str = Form(""),
):
    require_admin_role(request)
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Senha precisa ter pelo menos 8 caracteres")
    store.create_vendedor(nome.strip(), email.strip(), telefone.strip(), username.strip(), password, role, cidade_designada or None)
    return RedirectResponse("/admin/vendedores", status_code=303)


@app.post("/admin/vendedores/{vendedor_id}/status")
def admin_atualizar_status_vendedor(request: Request, vendedor_id: str, status: str = Form(...), pin: str = Form(...)):
    """Desativar vendedor é ação sensível (fim de linha, seção "regra
    de saída" do spec) — exige PIN, mesmo padrão do admin-mcp."""
    require_admin_role(request)
    _check_pin(pin)
    if status not in ("ativo", "inativo"):
        raise HTTPException(status_code=400, detail="Status inválido")
    store.update_vendedor(vendedor_id, status=status)
    return RedirectResponse("/admin/vendedores", status_code=303)


# ------------------------------------------------------- admin: ferramentas (proxy onboarding-service)

@app.get("/admin/tenants", response_class=HTMLResponse)
def admin_listar_tenants(request: Request):
    admin = require_admin_role(request)
    tenants = onboarding_client.listar_tenants()
    return templates.TemplateResponse(request, "admin_tenants.html", {"tenants": tenants, "vendedor": admin})


@app.get("/admin/alertas", response_class=HTMLResponse)
def admin_alertas(request: Request):
    admin = require_admin_role(request)
    alertas = onboarding_client.listar_alertas_uso()
    return templates.TemplateResponse(request, "admin_alertas.html", {"alertas": alertas, "vendedor": admin})


@app.post("/admin/tenants/{tenant_id}/ativar")
def admin_ativar_tenant(request: Request, tenant_id: str, enabled: bool = Form(...), pin: str = Form(...)):
    require_admin_role(request)
    _check_pin(pin)
    onboarding_client.ativar_tenant(tenant_id, enabled)
    return RedirectResponse("/admin/tenants", status_code=303)
