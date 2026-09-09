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
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import onboarding_client, prospeccao, store

PROSPECCAO_CHAMADAS_POR_LOTE = 50

BACKOFFICE_SESSION_SECRET = os.environ["BACKOFFICE_SESSION_SECRET"]
ADMIN_PIN = os.environ.get("ADMIN_PIN", "")
SIGNUP_BASE_URL = os.environ.get("SIGNUP_BASE_URL", "https://atendpragente.com.br")

app = FastAPI(title="AtendPraGente — Backoffice Comercial")
app.add_middleware(SessionMiddleware, secret_key=BACKOFFICE_SESSION_SECRET, session_cookie="backoffice_session")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@app.exception_handler(HTTPException)
async def _sem_sessao_manda_pro_login(request: Request, exc: HTTPException):
    """Sem isso, quem abre uma página protegida sem sessão (GET normal
    de navegador, não fetch/API) recebe um 401 cru em JSON em vez de
    cair na tela de login — ruim pra uma ferramenta que só tem
    formulário HTML, nenhum consumidor de API de verdade."""
    if exc.status_code == 401 and request.method == "GET":
        return RedirectResponse("/login", status_code=303)
    return await http_exception_handler(request, exc)


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
    leads_por_status = {status: [] for status in store.LEAD_STATUS}
    for lead in leads:
        leads_por_status[lead["status"]].append(lead)
    funil = store.funil_por_vendedor(vendedor["_id"])
    forecast = store.forecast_mes(vendedor["_id"])
    link_convite = f"{SIGNUP_BASE_URL}/signup?vendedor={vendedor['slug']}"
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "vendedor": vendedor,
            "comissao": comissao,
            "bate_bonus_rampa": bate_bonus,
            "leads_por_status": leads_por_status,
            "lead_status": store.LEAD_STATUS,
            "funil": funil,
            "funil_max": max(funil.values()) if funil.values() else 0,
            "forecast": forecast,
            "planos_disponiveis": store.PLANOS_DISPONIVEIS,
            "segmentos_disponiveis": store.SEGMENTOS_DISPONIVEIS,
            "roteiro_agendamento": store.ROTEIRO_AGENDAMENTO,
            "roteiro_agendamento_objecao": store.ROTEIRO_AGENDAMENTO_OBJECAO,
            "roteiro_demonstracao_passos": store.ROTEIRO_DEMONSTRACAO_PASSOS,
            "roteiro_demonstracao_objecoes": store.ROTEIRO_DEMONSTRACAO_OBJECOES,
            "roteiro_demonstracao_objecao_preco": store.ROTEIRO_DEMONSTRACAO_OBJECAO_PRECO,
            "meta_tier": store.META_CLIENTES_POR_TIER,
            "link_convite": link_convite,
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
    segmento: str = Form(""),
):
    vendedor = require_session(request)
    store.create_lead(
        negocio.strip(), contato.strip(), cidade.strip(), origem.strip(), vendedor["_id"],
        plano_alvo or None, segmento or None,
    )
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/status")
def atualizar_status_lead(request: Request, lead_id: str, status: str = Form(...)):
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if status not in store.LEAD_STATUS:
        raise HTTPException(status_code=400, detail="Status inválido")
    store.update_lead_status(lead_id, status)
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/plano")
def atualizar_plano_lead(request: Request, lead_id: str, plano_alvo: str = Form("")):
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    plano_alvo = plano_alvo.strip()
    if plano_alvo and plano_alvo not in store.PLANOS_DISPONIVEIS:
        raise HTTPException(status_code=400, detail="Plano inválido")
    store.update_lead_plano(lead_id, plano_alvo or None)
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/segmento")
def atualizar_segmento_lead(request: Request, lead_id: str, segmento: str = Form("")):
    """Segmento decide qual roteiro (agendamento/demonstração) aparece
    no card — specs/comissao-vendedores.md, "Script de abordagem"."""
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    segmento = segmento.strip()
    if segmento and segmento not in store.SEGMENTOS_DISPONIVEIS:
        raise HTTPException(status_code=400, detail="Segmento inválido")
    store.update_lead_segmento(lead_id, segmento or None)
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/reuniao")
def marcar_reuniao(request: Request, lead_id: str, data: str = Form(...)):
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    store.marcar_reuniao(lead_id, data.strip())
    return RedirectResponse("/", status_code=303)


@app.post("/leads/{lead_id}/comentario")
def adicionar_comentario_lead(request: Request, lead_id: str, texto: str = Form(...)):
    vendedor = require_session(request)
    lead = store.get_lead(lead_id)
    if not lead or lead["vendedor_id"] != vendedor["_id"]:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    texto = texto.strip()
    if texto:
        store.adicionar_comentario(lead_id, texto)
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


# ---------------------------------------------------------- prospecção (base de consulta)

@app.get("/prospeccao", response_class=HTMLResponse)
def listar_prospeccoes(request: Request, pagina: int = 1):
    """Lista compartilhada entre todos os vendedores — quem converter
    primeiro fica com o lead (mesma lógica de "primeiro toque" que já
    vale pra atribuição de tenant, regra 03 do spec). Paginada a 25 por
    página; só mostra quem ainda não foi capturado (convertido_lead_id
    vazio) — um candidato nunca fica "preso" numa página velha, some da
    lista só quando alguém converte ele de verdade."""
    vendedor = require_session(request)
    pagina = max(pagina, 1)
    total = store.count_prospeccoes()
    total_paginas = max((total + store.PROSPECCAO_PAGE_SIZE - 1) // store.PROSPECCAO_PAGE_SIZE, 1)
    pagina = min(pagina, total_paginas)
    prospeccoes = store.list_prospeccoes(pagina=pagina)
    return templates.TemplateResponse(
        request,
        "prospeccao.html",
        {
            "vendedor": vendedor,
            "prospeccoes": prospeccoes,
            "chamadas_por_lote": PROSPECCAO_CHAMADAS_POR_LOTE,
            "pagina": pagina,
            "total_paginas": total_paginas,
            "total": total,
        },
    )


@app.post("/prospeccao/{place_id}/converter")
def converter_prospeccao(request: Request, place_id: str, cidade: str = Form(...)):
    vendedor = require_session(request)
    prospect = store.get_prospeccao(place_id)
    if not prospect or prospect.get("convertido_lead_id"):
        raise HTTPException(status_code=404, detail="Candidato não encontrado (já convertido por outro vendedor?)")
    lead_id = store.create_lead(
        negocio=prospect["negocio"],
        contato=prospect.get("telefone") or "",
        cidade=cidade,
        origem="prospecção automática",
        vendedor_id=vendedor["_id"],
        segmento=store.segmento_para_categoria(prospect.get("categoria", "")),
    )
    store.marcar_prospeccao_convertida(place_id, lead_id)
    return RedirectResponse("/prospeccao", status_code=303)


@app.post("/prospeccao/{place_id}/desqualificar")
def desqualificar_prospeccao(request: Request, place_id: str, motivo: str = Form("")):
    """Continua na lista (não é conversão nem exclusão) — só marca pra
    quem olhar depois não perder tempo tentando de novo."""
    require_session(request)
    prospect = store.get_prospeccao(place_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Candidato não encontrado")
    store.marcar_prospeccao_desqualificada(place_id, motivo.strip())
    return RedirectResponse("/prospeccao", status_code=303)


@app.post("/admin/prospeccao/buscar-mais")
def admin_buscar_mais_prospeccoes(request: Request, pin: str = Form(...)):
    """Gasta chamadas de Google Places de verdade — PIN-gated pelo
    mesmo motivo das outras ações sensíveis: custo real, decisão
    humana, nunca automática além do lote diário do CronJob."""
    require_admin_role(request)
    _check_pin(pin)
    prospeccao.buscar_lote(PROSPECCAO_CHAMADAS_POR_LOTE)
    return RedirectResponse("/prospeccao", status_code=303)


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
    cidade_designada: str = Form(""),
):
    """Todo cadastro novo nasce vendedor — não existe seletor de papel
    aqui de propósito. Admin é reservado, promovido manualmente só via
    banco (fora da UI) quando for mesmo o caso."""
    require_admin_role(request)
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Senha precisa ter pelo menos 8 caracteres")
    store.create_vendedor(nome.strip(), email.strip(), telefone.strip(), username.strip(), password, "vendedor", cidade_designada or None)
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

def _reconciliar_convites_vendedor(tenants: list[dict]) -> int:
    """Auto-atribui tenants que chegaram por um link pessoal de vendedor
    (?vendedor=<slug> em /signup) e ainda não foram atribuídos —
    dispensa o passo manual de "atribuir tenant" pra esses casos. Usa
    exatamente o mesmo caminho da atribuição manual (onboarding_client
    + store.atribuir_tenant), só que disparado pelo slug em vez de por
    um clique. Slug sem vendedor conhecido fica pra trás sem travar
    nada — mesma postura que o onboarding-service já tem com um invite
    inválido."""
    reconciliados = 0
    for tenant in tenants:
        slug = tenant.get("vendedor_slug_hint")
        if not slug or tenant.get("vendedor_id") or store.existe_atribuicao(tenant["tenant_id"]):
            continue
        vendedor = store.get_vendedor_by_slug(slug)
        if not vendedor:
            continue
        plano = tenant.get("plano", "")
        mrr = store.PRECO_POR_PLANO.get(plano)
        if mrr is None:
            continue
        onboarding_client.atribuir_vendedor(tenant["tenant_id"], vendedor["_id"], origem_venda="link-vendedor")
        store.atribuir_tenant(vendedor["_id"], tenant["tenant_id"], plano, mrr)
        tenant["vendedor_id"] = vendedor["_id"]
        reconciliados += 1
    return reconciliados


@app.get("/admin/tenants", response_class=HTMLResponse)
def admin_listar_tenants(request: Request):
    admin = require_admin_role(request)
    tenants = onboarding_client.listar_tenants()
    reconciliados = _reconciliar_convites_vendedor(tenants)
    return templates.TemplateResponse(
        request, "admin_tenants.html", {"tenants": tenants, "vendedor": admin, "reconciliados": reconciliados}
    )


@app.get("/admin/pipeline", response_class=HTMLResponse)
def admin_pipeline(request: Request):
    admin = require_admin_role(request)
    pipeline = store.pipeline_por_vendedor()
    return templates.TemplateResponse(
        request, "admin_pipeline.html", {"vendedor": admin, "pipeline": pipeline, "lead_status": store.LEAD_STATUS}
    )


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
