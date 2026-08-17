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
