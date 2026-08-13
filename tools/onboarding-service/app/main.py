"""Serviço de onboarding self-service (Fase 4): Embedded Signup +
formulário de SOUL + provisionamento automático de tenant.

Roda como pod no namespace atendagente-onboarding, com um kubeconfig
próprio (namespace-scoped, restrito a `atendagente`) via KUBECONFIG, e
código montado via hostPath a partir de /root/atendagente-tools/ (sem
imagem custom/registry — ver README).
"""
import os
import re
import sys
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# /app é o hostPath de tools/ inteiro — provision-tenant/ e
# soul-generator/ são diretórios irmãos deste (tools/onboarding-service/app/main.py).
_TOOLS_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_TOOLS_DIR / "provision-tenant"))
sys.path.insert(0, str(_TOOLS_DIR / "soul-generator"))
from provision_tenant import ProvisionError, provision, subscribe_app_to_waba  # noqa: E402
from generate_soul import render as render_soul  # noqa: E402

from app import meta_client, store  # noqa: E402

APP_ID = os.environ["WHATSAPP_CLOUD_APP_ID"]
CONFIG_ID = os.environ["META_CONFIG_ID"]
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "atendpragente.com.br")
TENANT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")

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
    if not (code and waba_id and phone_number_id):
        raise HTTPException(400, "code, waba_id e phone_number_id são obrigatórios")

    try:
        access_token = meta_client.exchange_code_for_token(code)
    except meta_client.MetaApiError as e:
        raise HTTPException(502, str(e))

    signup_id = store.create_signup(waba_id, phone_number_id, access_token)
    return {"signup_id": signup_id, "next": f"/signup/{signup_id}/form"}


@app.get("/signup/{signup_id}/form", response_class=HTMLResponse)
def form_page(signup_id: str, request: Request, erro: str | None = None):
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")
    return templates.TemplateResponse(
        request, "form.html", {"signup_id": signup_id, "erro": erro}
    )


@app.post("/signup/{signup_id}/form", response_class=HTMLResponse)
def submit_form(
    signup_id: str,
    request: Request,
    tenant_id: str = Form(...),
    nome_negocio: str = Form(...),
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
):
    signup = store.get_signup(signup_id)
    if not signup:
        raise HTTPException(404, "Cadastro não encontrado")

    def erro_de_volta(msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "form.html",
            {"signup_id": signup_id, "erro": msg},
            status_code=400,
        )

    tenant_id = tenant_id.strip().lower()
    if not TENANT_ID_RE.match(tenant_id):
        return erro_de_volta(
            "tenant_id inválido — use só letras minúsculas, números e hífen, "
            "começando com letra (3-32 caracteres)."
        )

    dominio = f"{tenant_id}.{BASE_DOMAIN}"
    config = {
        "tenant_id": tenant_id,
        "dominio": dominio,
        "whatsapp": {
            "phone_number_id": signup["phone_number_id"],
            "waba_id": signup["waba_id"],
        },
        "nome_negocio": nome_negocio,
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

    store.update_signup(signup_id, status="provisioning", tenant_id=tenant_id)

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
    except ProvisionError as e:
        store.update_signup(signup_id, status="failed", erro=str(e))
        raise HTTPException(500, f"Provisionamento falhou: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    store.update_signup(signup_id, status="live", panel_user=credentials["panel_user"])

    # Renderiza direto (sem redirect) porque a senha do painel só existe
    # aqui, em memória — não é persistida no Mongo (ver store.py).
    return templates.TemplateResponse(
        request,
        "done.html",
        {
            "tenant_id": tenant_id,
            "dominio": dominio,
            "panel_user": credentials["panel_user"],
            "panel_password": credentials["panel_password"],
        },
    )


@app.get("/signup/{signup_id}/done", response_class=HTMLResponse)
def done_page(signup_id: str, request: Request):
    """Revisita de status pra um cadastro já concluído — não mostra a
    senha do painel de novo (só apareceu na resposta original)."""
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
            "panel_user": signup.get("panel_user"),
            "panel_password": None,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
