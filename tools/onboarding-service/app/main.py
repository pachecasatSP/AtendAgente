"""Serviço de onboarding self-service (Fase 4): Embedded Signup +
formulário de SOUL + provisionamento automático de tenant.

Roda como pod no namespace atendagente-onboarding, com um kubeconfig
próprio (namespace-scoped, restrito a `atendagente`) via KUBECONFIG, e
código montado via hostPath a partir de /root/atendagente-tools/ (sem
imagem custom/registry — ver README).
"""
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# /app é o hostPath de tools/ inteiro — provision-tenant/ e
# soul-generator/ são diretórios irmãos deste (tools/onboarding-service/app/main.py).
_TOOLS_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_TOOLS_DIR / "provision-tenant"))
sys.path.insert(0, str(_TOOLS_DIR / "soul-generator"))
from provision_tenant import ProvisionError, provision, set_tenant_enabled, subscribe_app_to_waba, tenant_exists  # noqa: E402
from generate_soul import render as render_soul  # noqa: E402

from app import asaas_client, meta_client, store  # noqa: E402

APP_ID = os.environ["WHATSAPP_CLOUD_APP_ID"]
CONFIG_ID = os.environ["META_CONFIG_ID"]
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "atendpragente.com.br")
TENANT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")

# Só planos com valor fixo entram no checkout automático — "Sem limite"
# é sob consulta e não aparece no wizard (ver PLANOS no form.html).
PLANOS = {
    "entrada": {"nome": "Entrada", "valor": 84.90, "valor_de": 105.90, "limite": 500},
    "comecando": {"nome": "Começando", "valor": 157.90, "valor_de": 197.00, "limite": 1000},
    "crescendo": {"nome": "Crescendo", "valor": 717.90, "valor_de": 897.00, "limite": 5000},
}
TRIAL_DIAS = 7
CONFIRM_EVENTS = {"CHECKOUT_PAID", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}
FAIL_EVENTS = {
    "PAYMENT_OVERDUE", "PAYMENT_DELETED",
    "CHECKOUT_EXPIRED", "CHECKOUT_CANCELED",
}

app = FastAPI(title="AtendAgente — Onboarding")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def provisioning_env() -> dict:
    """Credenciais compartilhadas da Ac Soluções (não por-cliente) —
    vêm do Secret do próprio onboarding-service."""
    return {
        "WHATSAPP_APP_SECRET": os.environ["WHATSAPP_CLOUD_APP_SECRET"],
        "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
        "CLOUDFLARE_API_TOKEN": os.environ["CLOUDFLARE_API_TOKEN"],
        "CLOUDFLARE_ZONE_ID": os.environ["CLOUDFLARE_ZONE_ID"],
        "SERVER_IP": os.environ["SERVER_IP"],
    }


def parse_pipe_lines(text: str, fields: list[str]) -> list[dict]:
    """Cada linha vira um dict, campos separados por `|`. Formato
    simples pra evitar JS de formulário dinâmico (grupos repetíveis)."""
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        items.append({f: (parts[i] if i < len(parts) else "") for i, f in enumerate(fields)})
    return items


def parse_label_lines(text: str) -> dict:
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        label, _, value = line.partition(":")
        result[label.strip()] = value.strip()
    return result


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse(
        request, "signup.html", {"app_id": APP_ID, "config_id": CONFIG_ID}
    )


@app.post("/api/signup/callback")
def signup_callback(payload: dict):
    code = payload.get("code")
    waba_id = payload.get("waba_id")
    phone_number_id = payload.get("phone_number_id")
    invite = (payload.get("invite") or "").strip()
    if not (code and waba_id and phone_number_id):
        raise HTTPException(400, "code, waba_id e phone_number_id são obrigatórios")

    invite_token = None
    if invite:
        token_doc = store.get_free_token(invite)
        if token_doc and not token_doc["usado"]:
            invite_token = invite
        # token inválido/já usado: ignora silenciosamente, cliente segue
        # o fluxo pago normal em vez de travar o cadastro por isso.

    try:
        access_token = meta_client.exchange_code_for_token(code)
    except meta_client.MetaApiError as e:
        raise HTTPException(502, str(e))

    signup_id = store.create_signup(waba_id, phone_number_id, access_token)
    if invite_token:
        store.update_signup(signup_id, invite_token=invite_token)
    return {"signup_id": signup_id, "next": f"/signup/{signup_id}/form"}


@app.get("/signup/{signup_id}/form", response_class=HTMLResponse)
def form_page(signup_id: str, request: Request, erro: str | None = None):
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")
    return templates.TemplateResponse(
        request, "form.html", {"signup_id": signup_id, "erro": erro, "gratuito": bool(signup.get("invite_token"))}
    )


@app.get("/api/signup/check-slug")
def check_slug(tenant_id: str):
    tenant_id = tenant_id.strip().lower()
    if not TENANT_ID_RE.match(tenant_id):
        return {"valido": False, "disponivel": False}
    disponivel = not (store.tenant_id_taken(tenant_id) or tenant_exists(tenant_id))
    return {"valido": True, "disponivel": disponivel}


def _run_provisioning(signup_id: str, signup: dict) -> dict:
    """Provisiona de verdade — chamado só depois da Asaas confirmar o
    pagamento (webhook), nunca direto do submit do formulário. Levanta
    ProvisionError se falhar; quem chama decide o que fazer com isso."""
    config = signup["config"]
    tenant_id = config["tenant_id"]
    dominio = config["dominio"]

    store.update_signup(signup_id, status="provisioning")

    tmp_path = Path(f"/tmp/signup-{signup_id}.yaml")
    tmp_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    env = provisioning_env()
    env["WHATSAPP_ACCESS_TOKEN"] = signup["access_token"]

    try:
        credentials = provision(tmp_path, env, dry_run=False)
        subscribe_app_to_waba(
            signup["waba_id"],
            signup["access_token"],
            credentials["verify_token"],
            f"https://{dominio}/whatsapp/webhook",
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    panel_setup_url = f"https://{dominio}/painel/setup?token={credentials['panel_setup_token']}"
    store.update_signup(signup_id, status="live", panel_setup_url=panel_setup_url)
    return credentials


@app.post("/signup/{signup_id}/form", response_class=HTMLResponse)
def submit_form(
    signup_id: str,
    request: Request,
    tenant_id: str = Form(...),
    nome_negocio: str = Form(...),
    nome_atendente: str = Form(""),
    artigo_negocio: str = Form("a"),
    descricao_negocio: str = Form(...),
    descricao_publico: str = Form(""),
    tom_descricao: str = Form(""),
    tom_emoji: str = Form(""),
    servicos: str = Form(...),
    como_trabalhamos: str = Form(""),
    lacunas_conhecidas: str = Form(""),
    escalacao_nome: str = Form(...),
    escalacao_telefone: str = Form(...),
    escalacao_pronome: str = Form("ele"),
    exemplos: str = Form(""),
    plano: str = Form(...),
    email: str = Form(...),
    cpf_cnpj: str = Form(...),
    telefone_cobranca: str = Form(...),
    endereco: str = Form(...),
    endereco_numero: str = Form(...),
    cep: str = Form(...),
    bairro: str = Form(...),
    eula_aceito: str = Form(""),
    comunicacoes_aceito: str = Form(""),
):
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")

    def erro_de_volta(msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "form.html",
            {"signup_id": signup_id, "erro": msg, "gratuito": bool(signup.get("invite_token"))},
            status_code=400,
        )

    tenant_id = tenant_id.strip().lower()
    if not TENANT_ID_RE.match(tenant_id):
        return erro_de_volta(
            "tenant_id inválido — use só letras minúsculas, números e hífen, "
            "começando com letra (3-32 caracteres)."
        )
    if store.tenant_id_taken(tenant_id) or tenant_exists(tenant_id):
        return erro_de_volta(
            f"O endereço \"{tenant_id}\" já está em uso — escolha outro."
        )
    if plano not in PLANOS:
        return erro_de_volta("Plano inválido — escolhe um dos planos disponíveis.")
    if eula_aceito not in ("on", "true", "1"):
        return erro_de_volta("Precisa aceitar os Termos de Uso (EULA) pra continuar.")

    dominio = f"{tenant_id}.{BASE_DOMAIN}"
    config = {
        "tenant_id": tenant_id,
        "dominio": dominio,
        "whatsapp": {
            "phone_number_id": signup["phone_number_id"],
            "waba_id": signup["waba_id"],
        },
        "nome_negocio": nome_negocio,
        "nome_atendente": nome_atendente,
        "artigo_negocio": artigo_negocio,
        "descricao_negocio": descricao_negocio,
        "descricao_publico": descricao_publico,
        "tom": {"descricao": tom_descricao, "emoji": tom_emoji},
        "servicos": parse_pipe_lines(servicos, ["nome", "descricao", "publico_alvo"]),
        "como_trabalhamos": parse_label_lines(como_trabalhamos),
        "lacunas_conhecidas": [
            line.strip() for line in lacunas_conhecidas.splitlines() if line.strip()
        ],
        "escalacao": {
            "nome": escalacao_nome,
            "telefone": escalacao_telefone,
            "pronome": escalacao_pronome,
        },
        "exemplos": parse_pipe_lines(exemplos, ["pergunta", "resposta"]),
    }

    try:
        render_soul(config)
    except Exception as e:
        return erro_de_volta(f"Erro no formulário de SOUL: {e}")

    eula_aceito_em = datetime.now(timezone.utc)
    comunicacoes_ok = comunicacoes_aceito in ("on", "true", "1")

    invite_token = signup.get("invite_token")
    if invite_token:
        # Cadastro gratuito (token gerado pela Duda) — pula a Asaas
        # inteira e provisiona na hora, sem esperar webhook de pagamento.
        store.update_signup(
            signup_id,
            status="payment_pending",  # _run_provisioning espera achar o signup com config já setado
            tenant_id=tenant_id,
            config=config,
            plano=plano,
            email=email,
            cpf_cnpj=cpf_cnpj,
            telefone_cobranca=telefone_cobranca,
            endereco=endereco,
            endereco_numero=endereco_numero,
            cep=cep,
            bairro=bairro,
            eula_aceito_em=eula_aceito_em,
            comunicacoes_aceito=comunicacoes_ok,
        )
        store.mark_free_token_used(invite_token, signup_id)
        signup = store.get_signup(signup_id)
        try:
            _run_provisioning(signup_id, signup)
        except ProvisionError as e:
            store.update_signup(signup_id, status="failed", erro=str(e))
        return RedirectResponse(f"/signup/{signup_id}/aguardando", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    try:
        checkout = asaas_client.create_subscription_checkout(
            external_reference=signup_id,
            plano_nome=PLANOS[plano]["nome"],
            valor=PLANOS[plano]["valor"],
            trial_dias=TRIAL_DIAS,
            nome_negocio=nome_negocio,
            email=email,
            cpf_cnpj=cpf_cnpj,
            telefone=telefone_cobranca,
            endereco=endereco,
            endereco_numero=endereco_numero,
            cep=cep,
            bairro=bairro,
            success_url=f"{base_url}/signup/{signup_id}/aguardando",
            cancel_url=f"{base_url}/signup/{signup_id}/form",
            expired_url=f"{base_url}/signup/{signup_id}/form",
        )
    except asaas_client.AsaasApiError as e:
        return erro_de_volta(f"Não consegui gerar a cobrança: {e}")

    store.update_signup(
        signup_id,
        status="payment_pending",
        tenant_id=tenant_id,
        config=config,
        plano=plano,
        email=email,
        cpf_cnpj=cpf_cnpj,
        telefone_cobranca=telefone_cobranca,
        endereco=endereco,
        endereco_numero=endereco_numero,
        cep=cep,
        bairro=bairro,
        checkout_id=checkout["id"],
        eula_aceito_em=eula_aceito_em,
        comunicacoes_aceito=comunicacoes_ok,
    )

    return RedirectResponse(checkout["link"], status_code=303)


@app.get("/signup/{signup_id}/aguardando", response_class=HTMLResponse)
def aguardando_pagamento(signup_id: str, request: Request):
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")
    if signup.get("status") == "live":
        return RedirectResponse(f"/signup/{signup_id}/done", status_code=303)
    return templates.TemplateResponse(
        request,
        "aguardando.html",
        {
            "signup_id": signup_id,
            "status": signup.get("status", "payment_pending"),
            "erro": signup.get("erro"),
        },
    )


@app.post("/api/asaas/webhook")
async def asaas_webhook(request: Request):
    token = request.headers.get("asaas-access-token")
    if token != os.environ["ASAAS_WEBHOOK_TOKEN"]:
        raise HTTPException(401, "Token de webhook inválido")

    payload = await request.json()
    event = payload.get("event")
    external_reference = (
        payload.get("payment", {}).get("externalReference")
        or payload.get("checkout", {}).get("externalReference")
    )
    if not external_reference:
        return {"ok": True, "ignorado": "sem externalReference"}

    signup = store.get_signup(external_reference)
    if not signup or signup.get("status") != "payment_pending":
        # já processado (webhook duplicado) ou não é sobre um signup nosso
        return {"ok": True, "ignorado": "signup não encontrado ou já processado"}

    if event in CONFIRM_EVENTS:
        try:
            # _run_provisioning faz subprocess.run bloqueante (kubectl
            # rollout status, até 120s) — numa def async isso travaria o
            # event loop inteiro, derrubando até /health. Roda numa
            # threadpool pra não bloquear outras requisições enquanto
            # provisiona.
            await run_in_threadpool(_run_provisioning, external_reference, signup)
        except ProvisionError as e:
            store.update_signup(external_reference, status="failed", erro=str(e))
    elif event in FAIL_EVENTS:
        store.update_signup(external_reference, status="failed", erro=f"Pagamento: {event}")

    return {"ok": True}


@app.get("/signup/{signup_id}/done", response_class=HTMLResponse)
def done_page(signup_id: str, request: Request):
    """Revisita de status pra um cadastro já concluído. O link de
    configuração do painel é de uso único no próprio painel (não é
    segredo persistido aqui como senha era antes), então dá pra
    mostrar de novo sem problema."""
    signup = store.get_signup(signup_id)
    if not signup or signup.get("status") != "live":
        raise HTTPException(404, "Cadastro não encontrado ou ainda não concluído")
    dominio = f"{signup['tenant_id']}.{BASE_DOMAIN}"
    return templates.TemplateResponse(
        request,
        "done.html",
        {
            "tenant_id": signup["tenant_id"],
            "dominio": dominio,
            "panel_setup_url": signup.get("panel_setup_url"),
        },
    )


def require_admin(request: Request) -> None:
    """Autenticação de serviço-pra-serviço entre o admin-mcp (ferramentas
    da Duda) e essas rotas — não confunde com o PIN de admin que o
    admin-mcp pede pro humano: essa chave nunca é vista/decidida pela IA,
    fica só no ambiente do admin-mcp."""
    key = request.headers.get("x-admin-api-key", "")
    if not secrets.compare_digest(key, os.environ.get("ADMIN_API_KEY", "")):
        raise HTTPException(401, "Chave de admin inválida")


@app.post("/api/admin/free-tokens")
def admin_create_free_token(payload: dict, request: Request):
    require_admin(request)
    nota = (payload.get("nota") or "").strip()
    token = store.create_free_token(nota=nota)
    base_url = str(request.base_url).rstrip("/")
    return {"token": token, "link": f"{base_url}/signup?invite={token}"}


@app.get("/api/admin/tenants")
def admin_list_tenants(request: Request):
    require_admin(request)
    signups = store.list_live_signups()
    return [
        {
            "tenant_id": s["tenant_id"],
            "dominio": f"{s['tenant_id']}.{BASE_DOMAIN}",
            "plano": s.get("plano"),
            "criado_em": s.get("criado_em"),
            "gratuito": bool(s.get("invite_token")),
        }
        for s in signups
    ]


@app.get("/api/admin/tenants/{tenant_id}/usage")
def admin_tenant_usage(tenant_id: str, request: Request):
    require_admin(request)
    signup = store.get_signup_by_tenant_id(tenant_id)
    if not signup:
        raise HTTPException(404, "Tenant não encontrado ou não está ao vivo")
    return {"tenant_id": tenant_id, "plano": signup.get("plano"), **store.tenant_usage(tenant_id)}


@app.post("/api/admin/tenants/{tenant_id}/enabled")
def admin_set_tenant_enabled(tenant_id: str, payload: dict, request: Request):
    """Liga/desliga o WhatsApp do tenant (`WHATSAPP_CLOUD_ENABLED`) sem
    apagar nada — reversível, pro "pausar/reativar" da Duda."""
    require_admin(request)
    signup = store.get_signup_by_tenant_id(tenant_id)
    if not signup:
        raise HTTPException(404, "Tenant não encontrado ou não está ao vivo")
    enabled = bool(payload.get("enabled"))
    set_tenant_enabled(tenant_id, enabled)
    return {"tenant_id": tenant_id, "enabled": enabled}


@app.get("/api/admin/usage-alerts")
def admin_usage_alerts(request: Request):
    """Tenants em warning (>=80% do limite) ou over (estourou) — ver
    tools/provision-tenant/usage_watch.py (CronJob diário) que preenche
    esses campos. Não pausa nada sozinho, só sinaliza pra decisão manual."""
    require_admin(request)
    signups = store.list_usage_alerts()
    return [
        {
            "tenant_id": s["tenant_id"],
            "plano": s.get("plano"),
            "usage_current_month": s.get("usage_current_month"),
            "usage_limite": s.get("usage_limite"),
            "usage_status": s.get("usage_status"),
        }
        for s in signups
    ]


@app.get("/api/admin/signups/failed")
def admin_list_failed_signups(request: Request):
    """Pagamentos confirmados que travaram no provisionamento (status
    'failed') — o que aparece pro cliente na tela de aguardando quando
    dá erro. Candidatos a retry via admin_retry_provisioning."""
    require_admin(request)
    signups = store.list_failed_signups()
    return [
        {
            "signup_id": s["_id"],
            "tenant_id": s.get("tenant_id"),
            "plano": s.get("plano"),
            "criado_em": s.get("criado_em"),
            "erro": s.get("erro"),
        }
        for s in signups
    ]


@app.post("/api/admin/signups/{signup_id}/retry-provisioning")
def admin_retry_provisioning(signup_id: str, request: Request):
    """Reprocessa o provisionamento de um cadastro travado em 'failed'
    sem exigir um novo pagamento — mesma rotina (_run_provisioning) que
    o webhook do Asaas chama, só que disparada manualmente pela Duda
    depois que o problema técnico foi corrigido."""
    require_admin(request)
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")
    if signup.get("status") != "failed":
        raise HTTPException(409, f"Cadastro não está em 'failed' (status atual: {signup.get('status')})")
    try:
        _run_provisioning(signup_id, signup)
    except ProvisionError as e:
        store.update_signup(signup_id, status="failed", erro=str(e))
        raise HTTPException(500, f"Provisionamento falhou de novo: {e}")
    return {"ok": True, "signup_id": signup_id, "tenant_id": signup["tenant_id"]}


@app.get("/health")
def health():
    return {"status": "ok"}
