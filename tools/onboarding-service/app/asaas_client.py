"""Chamadas server-side à API da Asaas pra cobrança recorrente do
AtendPraGente. Mesmo padrão de meta_client.py (stdlib puro, sem
dependência nova) e mesmo desenho do core-wallet (re-colocar-me):
Checkout hospedado pela Asaas — nunca lidamos com dado de cartão aqui.

ASAAS_API_KEY autentica as chamadas; ASAAS_WEBHOOK_TOKEN é um segredo
SEPARADO, configurado no painel da Asaas pro webhook, nunca reaproveitar
o mesmo valor da API key (mesma lição do core-wallet).
"""
import json
import os
import urllib.error
import urllib.request

ASAAS_BASE_URL = os.environ.get("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")


class AsaasApiError(RuntimeError):
    pass


def _post(path: str, payload: dict) -> dict:
    api_key = os.environ["ASAAS_API_KEY"]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{ASAAS_BASE_URL}{path}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "access_token": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise AsaasApiError(
            f"Asaas {path} falhou: HTTP {e.code}: {e.read().decode()}"
        ) from e


def create_subscription_checkout(
    *,
    external_reference: str,
    plano_nome: str,
    valor: float,
    trial_dias: int,
    nome_negocio: str,
    email: str,
    cpf_cnpj: str,
    telefone: str,
    endereco: str,
    endereco_numero: str,
    cep: str,
    bairro: str,
    success_url: str,
    cancel_url: str,
    expired_url: str,
) -> dict:
    """Cria um Checkout hospedado pela Asaas pra assinatura recorrente
    mensal, com o primeiro vencimento adiado pelo trial. Retorna
    {"id": ..., "link": ...} — o `link` é pra onde redirecionar o
    cliente pra ele confirmar o cartão."""
    import datetime

    next_due_date = (
        datetime.date.today() + datetime.timedelta(days=trial_dias)
    ).isoformat()

    payload = {
        "billingTypes": ["CREDIT_CARD"],
        "chargeTypes": ["RECURRENT"],
        "minutesToExpire": 60 * 24,
        "externalReference": external_reference,
        "callback": {
            "successUrl": success_url,
            "cancelUrl": cancel_url,
            "expiredUrl": expired_url,
        },
        "items": [
            {
                "name": f"AtendPraGente {plano_nome}"[:30],
                "description": f"Assinatura mensal do AtendPraGente pra {nome_negocio}",
                "quantity": 1,
                "value": valor,
            }
        ],
        "customerData": {
            "name": nome_negocio,
            "email": email,
            "cpfCnpj": cpf_cnpj,
            "phone": telefone,
            "address": endereco,
            "addressNumber": endereco_numero,
            "postalCode": cep,
            "province": bairro,
        },
        "subscription": {
            "cycle": "MONTHLY",
            "nextDueDate": next_due_date,
        },
    }
    data = _post("/checkouts", payload)
    checkout_id = data.get("id")
    link = data.get("link")
    if not checkout_id or not link:
        raise AsaasApiError(f"Resposta da Asaas sem id/link de checkout: {data}")
    return {"id": checkout_id, "link": link}
