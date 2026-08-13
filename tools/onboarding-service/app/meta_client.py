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
