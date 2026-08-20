#!/usr/bin/env python3
"""Roda periodicamente (ver setup_agenda_lembrete_cron.py) no namespace
atendagente — manda a confirmação de agendamento por WhatsApp (Fase 11)
pra compromissos que estão perto do horário e ainda não tiveram a
mensagem de template enviada.

"Perto do horário" é configurável por tenant
(config.agendamento_confirmacao_antecedencia_horas em `signups`, padrão
24h, editável em /configuracoes — ver tenant-panel/app.py). Só age em
`agendamentos` com status "agendado" (ainda não enviado — o botão manual
"Confirmar" em /agenda usa o mesmo critério e muda pro mesmo status
seguinte, então nunca duplica envio) cujo início cai dentro da janela
[agora, agora + antecedência).

Standalone de propósito (sem depender do calendar-mcp nem do
tenant-panel — processos/deploys separados): duplica a lógica mínima de
envio via Graph API, só com urllib+pymongo (mesmo raciocínio do
usage_watch.py sobre LIMITES). Se os dois lugares divergirem, atualizar
nos dois.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
GRAPH_API_VERSION = "v21.0"
AGENDA_TEMPLATE_NAME = "agenda_confirmacao"

_DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def _formatar_quando(inicio_utc_naive: datetime) -> str:
    # Mesma conversão de fuso do resto do painel — start salvo no Mongo é
    # sempre UTC naive (ver check_and_book), horário exibido é Brasília.
    from zoneinfo import ZoneInfo
    start = inicio_utc_naive.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Sao_Paulo"))
    return f"{_DIAS_SEMANA[start.weekday()]} ({start.strftime('%d/%m')}) às {start.strftime('%H:%M')}"


def _graph_call(access_token: str, path: str, payload: dict) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": {"message": f"HTTP {exc.code}"}}
    except urllib.error.URLError as exc:
        return {"error": {"message": str(exc)}}


def _ensure_template(waba_id: str, access_token: str, done: set) -> None:
    if waba_id in done or not waba_id or not access_token:
        return
    resultado = _graph_call(
        access_token, f"{waba_id}/message_templates",
        {
            "name": AGENDA_TEMPLATE_NAME,
            "language": "pt_BR",
            "category": "UTILITY",
            "components": [
                {
                    "type": "BODY",
                    "text": "Oi! Confirmando seu compromisso: {{1}}.\nSe puder, toca em um dos botões abaixo.",
                    "example": {"body_text": [["quinta-feira (21/08) às 15:00"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Confirmar"},
                        {"type": "QUICK_REPLY", "text": "Remarcar"},
                    ],
                },
            ],
        },
    )
    erro = json.dumps(resultado.get("error") or {}).lower()
    if resultado.get("id") or "already" in erro:
        done.add(waba_id)


def _enviar(tenant: dict, agendamento: dict, done_templates: set) -> bool:
    access_token = tenant.get("access_token") or ""
    phone_number_id = tenant.get("phone_number_id") or ""
    waba_id = tenant.get("waba_id") or ""
    chat_id = agendamento.get("chat_id")
    if not (access_token and phone_number_id and waba_id and chat_id):
        return False
    _ensure_template(waba_id, access_token, done_templates)
    agendamento_id = str(agendamento["_id"])
    resultado = _graph_call(
        access_token, f"{phone_number_id}/messages",
        {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "template",
            "template": {
                "name": AGENDA_TEMPLATE_NAME,
                "language": {"code": "pt_BR"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": _formatar_quando(agendamento["inicio"])}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "0",
                     "parameters": [{"type": "payload", "payload": f"agenda_confirmar:{agendamento_id}"}]},
                    {"type": "button", "sub_type": "quick_reply", "index": "1",
                     "parameters": [{"type": "payload", "payload": f"agenda_remarcar:{agendamento_id}"}]},
                ],
            },
        },
    )
    return bool(resultado.get("messages") or [])


def run() -> None:
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    signups_coll = db["signups"]
    agendamentos_coll = db["agendamentos"]

    now = datetime.now(timezone.utc)
    live_tenants = list(signups_coll.find({"status": "live", "config.agendamento_ativo": True}))
    print(f"[agenda-lembrete] {len(live_tenants)} tenant(s) com agenda ativa")

    done_templates: set = set()
    total_enviado = 0
    for tenant in live_tenants:
        tenant_id = tenant.get("tenant_id")
        if not tenant_id:
            continue
        antecedencia_horas = (tenant.get("config") or {}).get("agendamento_confirmacao_antecedencia_horas", 24)
        try:
            antecedencia_horas = int(antecedencia_horas)
        except (TypeError, ValueError):
            antecedencia_horas = 24
        limite = now + timedelta(hours=antecedencia_horas)

        pendentes = list(agendamentos_coll.find({
            "tenant_id": tenant_id,
            "status": "agendado",
            "chat_id": {"$ne": None},
            "inicio": {"$gt": now.replace(tzinfo=None), "$lte": limite.replace(tzinfo=None)},
        }))
        for agendamento in pendentes:
            try:
                enviado = _enviar(tenant, agendamento, done_templates)
            except Exception as e:
                print(f"[agenda-lembrete] {tenant_id} agendamento={agendamento['_id']} ERRO: {e}")
                continue
            if enviado:
                agendamentos_coll.update_one(
                    {"_id": agendamento["_id"], "status": "agendado"},
                    {"$set": {"status": "aguardando_confirmacao"}},
                )
                total_enviado += 1
                print(f"[agenda-lembrete] {tenant_id} agendamento={agendamento['_id']} confirmação enviada")
            else:
                print(f"[agenda-lembrete] {tenant_id} agendamento={agendamento['_id']} envio falhou (template pendente de aprovação?)")

    print(f"[agenda-lembrete] concluído — {total_enviado} confirmação(ões) enviada(s)")


if __name__ == "__main__":
    run()
