"""Chamadas server-side à Graph API da Meta pro fluxo de Embedded Signup
(Fase 4). O App Secret nunca é enviado ao browser — só usado aqui.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

GRAPH_API_VERSION = "v21.0"


class MetaApiError(RuntimeError):
    pass


def exchange_code_for_token(code: str) -> str:
    """Troca o `code` de autorização (curto prazo, uso único) do
    Embedded Signup por um access token de longa duração do cliente."""
    app_id = os.environ["WHATSAPP_CLOUD_APP_ID"]
    app_secret = os.environ["WHATSAPP_CLOUD_APP_SECRET"]
    params = urllib.parse.urlencode(
        {"client_id": app_id, "client_secret": app_secret, "code": code}
    )
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/oauth/access_token?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao trocar code por token: HTTP {e.code}: {e.read().decode()}"
        ) from e

    token = body.get("access_token")
    if not token:
        raise MetaApiError(f"Resposta da Meta sem access_token: {body}")
    return token


def revoke_access(access_token: str) -> None:
    """Desconecta o app do Portfólio Empresarial do cliente — revoga tudo
    que o Embedded Signup concedeu (DELETE /me/permissions, mesmo
    mecanismo do "remover app" que apareceria nas configurações do
    Facebook do cliente). Usado pelo botão "Desistir do cadastro"
    (ver /signup/{id}/desistir em main.py), só disponível antes do
    formulário ser enviado — depois disso a WABA já pode ter webhook
    registrado (subscribe_app_to_waba) e desistir vira cancelamento de
    assinatura normal, não desconexão."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/permissions"
    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao revogar acesso do app: HTTP {e.code}: {e.read().decode()}"
        ) from e


def register_phone_number(phone_number_id: str, access_token: str, pin: str) -> None:
    """Registra o número na Cloud API (`POST /{id}/register`) — passo que
    o Embedded Signup NÃO faz sozinho apesar do `code_verification_status`
    já vir "VERIFIED": sem isso o número fica em `status: PENDING` (não
    "CONNECTED") e não manda/recebe mensagem de gente de verdade, só
    aparenta funcionar pros até 5 destinatários de teste. Descoberto
    2026-08-19 comparando dois tenants live: `novo-negocio` estava
    CONNECTED (registrado manualmente numa depuração anterior, nunca
    trazido pro código) e `linda-ana-calcados` ficou PENDING — todo
    onboarding por Embedded Signup passava por esse buraco. `pin` é o PIN
    de verificação em duas etapas (6 dígitos) que a Cloud API exige pra
    registrar; qualquer PIN novo serve na primeira vez, mas precisa ficar
    salvo (Secret do tenant) pra caso o número precise ser re-registrado
    depois (troca de app, etc.)."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/register"
    payload = json.dumps({"messaging_product": "whatsapp", "pin": pin}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao registrar o número {phone_number_id} na Cloud API: "
            f"HTTP {e.code}: {e.read().decode()}"
        ) from e
    if not body.get("success"):
        raise MetaApiError(f"Meta não confirmou o registro do número {phone_number_id}: {body}")


def list_waba_phone_numbers(waba_id: str, access_token: str) -> list[dict]:
    """Lista os números cadastrados numa WABA (Fase 10 — troca de número
    de um tenant já configurado). Mesma chamada usada em 2026-08-19 pra
    diagnosticar o status do `linda-ana-calcados` na Graph API."""
    params = urllib.parse.urlencode(
        {"fields": "display_phone_number,verified_name,status,id", "access_token": access_token}
    )
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{waba_id}/phone_numbers?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao listar números da WABA {waba_id}: HTTP {e.code}: {e.read().decode()}"
        ) from e
    return body.get("data") or []


def get_phone_number_status(phone_number_id: str, access_token: str) -> str | None:
    """Só o campo `status` (PENDING/CONNECTED/...) — usado pra confirmar
    que um número recém-registrado (Fase 10) já propagou antes de mandar
    a mensagem de teste."""
    params = urllib.parse.urlencode({"fields": "status", "access_token": access_token})
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao consultar status do número {phone_number_id}: HTTP {e.code}: {e.read().decode()}"
        ) from e
    return body.get("status")


def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    """Manda uma mensagem de texto simples a partir de um número
    específico (Fase 10 — teste de um número candidato antes de trocar o
    Secret do tenant pra ele de vez). Mensagem business-initiated fora da
    janela de 24h pode ser rejeitada pela Meta dependendo do histórico de
    conversa com `to` — quem chama trata a falha como "teste não passou",
    não como erro de sistema."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = json.dumps(
        {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao mandar mensagem de teste via {phone_number_id}: HTTP {e.code}: {e.read().decode()}"
        ) from e


def get_phone_number_details(phone_number_id: str, access_token: str) -> dict:
    """Busca o número visível e o nome verificado do WABA recém-conectado —
    só pra mostrar "conectamos o número X" no Passo 1 do wizard de SOUL.
    Best-effort: quem chama decide o que fazer se isso falhar (não deve
    travar o cadastro por causa de um dado cosmético)."""
    params = urllib.parse.urlencode(
        {"fields": "display_phone_number,verified_name", "access_token": access_token}
    )
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MetaApiError(
            f"Falha ao buscar dados do número: HTTP {e.code}: {e.read().decode()}"
        ) from e
    return {
        "display_phone_number": body.get("display_phone_number"),
        "verified_name": body.get("verified_name"),
    }
