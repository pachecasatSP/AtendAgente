"""Paginas do onboarding (/signup, /signup/{id}/form, /aguardando, /done) e os arquivos que elas carregam (/estilo, /fonte).
Cobre o redesenho de 2026-11-04 (antes: ~75-95 KB de fonte em base64 em cada pagina, CSS e JS inline, emojis na interface, CTA amarelo com brilho,
aviso de convite fixo no topo, `pattern` do endereco invalido em navegador moderno, `?erro=` da URL escrito dentro do formulario)."""
import inspect
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2]
ONB = TOOLS / "onboarding-service"
sys.path[:0] = [str(ONB), str(TOOLS / "provision-tenant")]

ENV = {"WHATSAPP_CLOUD_APP_ID": "123456", "WHATSAPP_CLOUD_APP_SECRET": "s", "META_CONFIG_ID": "987654", "OPENROUTER_API_KEY": "o",
       "GROQ_API_KEY": "g", "CLOUDFLARE_API_TOKEN": "c", "CLOUDFLARE_ZONE_ID": "z", "SERVER_IP": "1.2.3.4",
       "ASAAS_API_KEY": "a", "ASAAS_WEBHOOK_TOKEN": "w", "MONGO_URI": "mongodb://127.0.0.1:1/t"}
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿⚠]")


def ler(nome):
    return (ONB / "static" / nome).read_text(encoding="utf-8")


@pytest.fixture
def m(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("app.main", None)
    sys.modules.pop("app", None)
    import app.main as mod
    return mod


@pytest.fixture
def cli(m):
    from starlette.testclient import TestClient
    return TestClient(m.app, follow_redirects=False)       # sem `with`: nao dispara os loops de startup


def signup_doc(**extra):
    return {"_id": "abc123", "status": "payment_pending", "display_phone_number": "+55 11 98765-4321", "verified_name": "Loja <Teste>", **extra}


def sem_chat_de_exemplo(html):
    """O balao de exemplo do mini-chat e conteudo de venda (mostra como o assistente fala), nao interface."""
    return re.sub(r'<div class="mini-chat".*?</div>\s*</div>', "", html, flags=re.S)


# ---------- arquivos servidos ----------
def test_fontes_so_a_lista_fixa_e_cache_longo(cli):
    for nome in ("baloo2-800.woff2", "nunito-var.woff2"):
        r = cli.get(f"/fonte/{nome}")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2" and "immutable" in r.headers["cache-control"] and len(r.content) > 5000
    for ruim in ("passwd", "..%2f..%2fapp%2fmain.py", "%2e%2e%2fapp.py", "baloo2-800.woff", "signup.html"):
        assert cli.get(f"/fonte/{ruim}").status_code == 404, ruim


def test_estilos_so_a_lista_fixa_e_cache_longo(cli):
    for nome, tipo in (("plataforma.css", "text/css"), ("onboarding.css", "text/css"), ("signup.js", "text/javascript"),
                       ("form.js", "text/javascript"), ("aguardando.js", "text/javascript")):
        r = cli.get(f"/estilo/{nome}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(tipo) and "immutable" in r.headers["cache-control"], nome
    for ruim in ("main.py", "..%2fapp%2fmain.py", "%2e%2e%2fapp%2fmain.py", "signup.html", "painel.css", "login.html"):
        assert cli.get(f"/estilo/{ruim}").status_code == 404, ruim


def test_plataforma_css_e_a_mesma_do_painel_e_acha_a_fonte_por_caminho_relativo(cli):
    r = cli.get("/estilo/plataforma.css")
    assert r.content == (TOOLS / "tenant-panel" / "static" / "plataforma.css").read_bytes()               # um arquivo so, sem copia
    assert "url(../fonte/baloo2-800.woff2)" in r.text and "url(../fonte/nunito-var.woff2)" in r.text      # /estilo/x.css -> /fonte/x.woff2


def test_endereco_dos_estilos_leva_o_hash_do_conteudo(m):
    for nome in ("plataforma.css", "onboarding.css", "signup.js", "form.js", "aguardando.js"):
        assert re.fullmatch(rf"/estilo/{re.escape(nome)}\?v=[0-9a-f]{{10}}", m.url_estilo(nome)), nome
    assert m.url_estilo("signup.js") != m.url_estilo("form.js")


# ---------- pagina inicial ----------
def test_signup_sem_fonte_nem_css_nem_js_embutidos(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_free_token", lambda t: None)
    html = cli.get("/signup").text
    assert "base64" not in html and "<style" not in html and len(html) < 10_000
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    for css in ("plataforma.css", "onboarding.css"):
        assert re.search(rf'href="/estilo/{re.escape(css)}\?v=[0-9a-f]{{10}}"', html), css
    assert 'href="/fonte/baloo2-800.woff2"' in html and "/estilo/signup.js?v=" in html
    assert 'noindex' not in html          # a pagina inicial e publica e continua indexavel


def test_signup_entrega_os_ids_da_meta_por_data_attribute_e_o_sdk_vem_depois_do_script(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_free_token", lambda t: None)
    html = cli.get("/signup").text
    assert 'data-app-id="123456"' in html and 'data-config-id="987654"' in html
    assert html.index("/estilo/signup.js") < html.index("connect.facebook.net/en_US/sdk.js")       # fbAsyncInit precisa existir antes do SDK carregar
    js = ler("signup.js")
    assert "window.fbAsyncInit" in js and "dados.appId" in js and "dados.configId" in js and "config_id: dados.configId" in js


def test_signup_botao_nao_quebra_se_o_sdk_da_meta_ainda_nao_carregou():
    js = ler("signup.js")
    assert "if (!window.FB)" in js and "btn.disabled = true" in js and "btn.disabled = false" in js


def test_signup_avisos_de_convite_e_desistencia_sao_inline_com_status_e_sem_emoji(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_free_token", lambda t: {"usado": True})
    html = cli.get("/signup", params={"invite": "tok", "desistiu": "1"}).text
    assert "Esse link de convite já foi usado" in html and "cancelamos sua conexão com a Meta" in html
    assert html.count('role="status"') >= 2 and "position: fixed" not in ler("onboarding.css").split(".aviso-topo")[1].split("}")[0]
    assert not EMOJIS.search(sem_chat_de_exemplo(html))


def test_signup_sem_convite_usado_nao_mostra_aviso(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_free_token", lambda t: None)
    html = cli.get("/signup", params={"invite": "inexistente"}).text
    assert "convite já foi usado" not in html and "cancelamos sua conexão" not in html


def test_signup_painel_do_convite_de_teste_continua_la(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_free_token", lambda t: None)
    html = cli.get("/signup").text
    for i in ("egg-panel", "egg-pin", "egg-nota", "egg-gerar", "egg-link", "egg-close"):
        assert f'id="{i}"' in html, i
    js = ler("signup.js")
    assert "'duda'" in js and "/api/easter-egg/free-token" in js and "/api/signup/callback" in js


# ---------- formulario ----------
def test_form_nao_embute_nada_e_nao_deixa_indexar(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    assert "base64" not in html and "<style" not in html and len(html) < 25_000
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert '<meta name="robots" content="noindex">' in html and '<meta name="referrer" content="no-referrer">' in html
    assert 'data-signup-id="abc123"' in html and "/estilo/form.js?v=" in html


def test_form_tem_todos_os_campos_que_o_servidor_recebe(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    esperados = [n for n, p in inspect.signature(m.submit_form).parameters.items() if n not in ("signup_id", "request", "background_tasks")]
    nomes_html = set(re.findall(r'name="([a-z_]+)"', html))
    assert set(esperados) <= nomes_html, set(esperados) - nomes_html


def test_form_planos_e_convite_gratuito(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    assert re.findall(r'name="plano" value="([a-z]+)"', html) == ["entrada", "comecando", "crescendo"]
    assert re.search(r'value="comecando" required checked', html) and "mais escolhido" in html
    assert 'data-gratuito="false"' in html and 'data-i="6" hidden' not in html and "data-skip-if-gratuito" not in html
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(invite_token="tok"))
    html = cli.get("/signup/abc123/form").text
    assert 'data-gratuito="true"' in html and 'data-i="6" hidden' in html and "data-skip-if-gratuito" in html


def test_form_numero_conectado_vem_escapado(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    assert "Conectamos o número +55 11 98765-4321 (Loja &lt;Teste&gt;)." in html and "<Teste>" not in html


def test_form_erro_na_url_nao_vira_texto_da_pagina(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form", params={"erro": "Ligue 0800-FALSO"}).text
    assert "0800-FALSO" not in html and 'role="alert"><svg' not in html


def test_form_erro_do_servidor_aparece_como_alerta_escapado(m):
    html = m.templates.env.get_template("form.html").render(signup_id="abc123", erro="Falhou <b>x</b>", gratuito=False, display_phone_number=None, verified_name=None)
    assert 'class="erro" role="alert"' in html and "Falhou &lt;b&gt;x&lt;/b&gt;" in html


def test_form_campos_de_16px_com_autocomplete_e_pattern_valido(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    css = ler("onboarding.css")
    assert re.search(r"input\[type=text\][^{]*\{[^}]*font-size:\s*1rem", css)
    for trecho in ('autocomplete="email"', 'autocomplete="postal-code"', 'autocomplete="tel"', 'autocomplete="organization"', 'inputmode="numeric"'):
        assert trecho in html, trecho
    assert 'pattern="[a-z][a-z0-9\\-]{1,30}[a-z0-9]"' in html           # `-` solto numa classe e erro de sintaxe no modo `v` dos navegadores novos


def test_form_js_sem_alert_nem_confirm_nem_glifos_de_status_e_com_o_mesmo_contrato():
    js = ler("form.js")
    assert not re.search(r"(?<![\w.])(alert|confirm)\(", js) and not EMOJIS.search(js) and "✓" not in js and "✗" not in js
    for trecho in ("/api/signup/check-slug?tenant_id=", "https://viacep.com.br/ws/", "/ai-assist", "/desistir", "data-skip-if-gratuito", "visibleStepNumbers"):
        assert trecho in js, trecho
    assert "primeiraVez" in js        # nao rouba o foco na carga da pagina


def test_form_sugestao_de_exemplos_aparece_ao_chegar_no_passo_do_campo_e_foco_cai_no_plano_marcado():
    js = ler("form.js")
    # antes: `if (current === total) maybeSuggestExamples()` -> so no ultimo passo, depois de a pessoa ja ter visto o campo vazio no passo 5
    assert "current === total) maybeSuggestExamples" not in js and "contains($('exemplos-field'))" in js
    # antes: `querySelector('input, textarea')` focava o 1o plano (Entrada) mesmo com o Comecando marcado
    assert "input[type=radio]:checked') ||" in js


def test_form_linhas_de_dois_campos_tem_espaco_em_cima():
    css = ler("onboarding.css")
    assert re.search(r"\.row-2 \{[^}]*margin-top:\s*1\.15rem", css) and ".row-2:first-child { margin-top: 0; }" in css


def test_done_icone_de_confirmacao_nao_fica_ao_lado_da_marca():
    assert re.search(r"\.no-ar \.icone-ok \{[^}]*display:\s*block", ler("onboarding.css"))


def test_form_html_sem_emoji_e_ajuda_de_escrita_sem_estrela(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc())
    html = cli.get("/signup/abc123/form").text
    assert not EMOJIS.search(html) and html.count("Ajudar a escrever</button>") == 5 and "✨" not in html


# ---------- aguardando ----------
@pytest.mark.parametrize("status,titulo", [("payment_pending", "Confirmando seu pagamento"), ("provisioning", "Provisionando seu atendimento")])
def test_aguardando_se_atualiza_sozinha_e_avisa_leitor_de_tela(cli, m, monkeypatch, status, titulo):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status=status))
    html = cli.get("/signup/abc123/aguardando").text
    assert titulo in html and 'role="status"' in html and "/estilo/aguardando.js?v=" in html and "base64" not in html
    assert 'class="fase current"' in html or 'class="fase current "' in html
    assert "setTimeout" in ler("aguardando.js") and "4000" in ler("aguardando.js")


def test_aguardando_falha_mostra_erro_escapado_e_link_de_suporte_e_nao_recarrega(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status="failed", erro="kubectl <x> falhou"))
    html = cli.get("/signup/abc123/aguardando").text
    assert "Deu ruim no provisionamento" in html and "kubectl &lt;x&gt; falhou" in html and "<x>" not in html
    assert "wa.me/5511920081743" in html and "aguardando.js" not in html and 'role="alert"' in html


def test_aguardando_cadastro_no_ar_vai_para_a_pagina_final(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status="live"))
    r = cli.get("/signup/abc123/aguardando")
    assert r.status_code == 303 and r.headers["location"] == "/signup/abc123/done"


# ---------- no ar ----------
def test_done_com_link_de_cadastro_do_painel(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status="live", tenant_id="loja-x", panel_setup_url="https://loja-x.atendpragente.com.br/painel/setup?token=abc"))
    html = cli.get("/signup/abc123/done").text
    assert "Seu atendimento está no ar!" in html and "https://loja-x.atendpragente.com.br" in html
    assert re.search(r'<a class="btn btn-grande" href="https://loja-x\.atendpragente\.com\.br/painel/setup\?token=abc">Cadastrar meu acesso</a>', html)
    assert not EMOJIS.search(html) and "base64" not in html and '<meta name="referrer" content="no-referrer">' in html


def test_done_sem_link_manda_entrar_no_painel(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status="live", tenant_id="loja-x"))
    html = cli.get("/signup/abc123/done").text
    assert "Cadastrar meu acesso" not in html and 'href="https://loja-x.atendpragente.com.br/painel"' in html


def test_done_cadastro_inexistente_ou_nao_concluido_continua_404(cli, m, monkeypatch):
    monkeypatch.setattr(m.store, "get_signup", lambda sid: None)
    assert cli.get("/signup/abc123/done").status_code == 404
    monkeypatch.setattr(m.store, "get_signup", lambda sid: signup_doc(status="provisioning"))
    assert cli.get("/signup/abc123/done").status_code == 404


def test_nenhum_css_do_onboarding_embute_fonte_nem_usa_contorno_de_texto():
    css = ler("onboarding.css")
    assert "base64" not in css and "text-stroke" not in css and "255,201,60" not in css
