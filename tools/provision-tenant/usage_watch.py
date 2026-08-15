#!/usr/bin/env python3
"""Roda 1x/dia como CronJob no namespace atendagente — conta, por tenant,
quantos contatos únicos (chat_id distintos) falaram com o bot desde o
dia 1 do mês corrente, compara com o limite do plano, e grava o
resultado no doc do signup pra consulta pelo painel do cliente e pela
ferramenta `alertas` da Duda (admin-mcp).

"Conversa" = chat_id único no mês (não sessão) — decisão explícita
(2026-08-15): mais justificável pro cliente entender ("quantas pessoas
diferentes te procuraram"), e não infla o número com múltiplas sessões
do mesmo contato ao longo do mês.

Não pausa nem cobra automaticamente nada — só mede e sinaliza
(usage_status: ok/warning/over). Ação sobre o excedente é sempre manual
(ver ferramenta `ativar` do admin-mcp e o pitch de R$0,14/conversa do
Sem Limite).
"""
import os
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]

# Mesmos limites de tools/onboarding-service/app/main.py (PLANOS) —
# duplicado aqui de propósito: esse job não importa o onboarding-service
# (processos/deploys separados), preferível a um import cross-serviço
# frágil por um dict de 2 linhas. Se os planos mudarem, atualizar nos
# dois lugares.
LIMITES = {
    "comecando": 1000,
    "crescendo": 5000,
}
WARNING_PCT = 0.8


def start_of_month() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()


def usage_status(count: int, limite: int | None) -> str:
    if not limite:  # Sem Limite ou plano desconhecido — nada pra alertar
        return "ok"
    pct = count / limite
    if pct >= 1.0:
        return "over"
    if pct >= WARNING_PCT:
        return "warning"
    return "ok"


def run() -> None:
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    signups_coll = db["signups"]
    sessions_coll = db["sessions"]

    month_start = start_of_month()
    live_tenants = list(signups_coll.find({"status": "live"}))
    print(f"[usage-watch] {len(live_tenants)} tenant(s) ao vivo, mês a partir de {month_start}")

    for signup in live_tenants:
        tenant_id = signup.get("tenant_id")
        if not tenant_id:
            continue
        limite = LIMITES.get(signup.get("plano"))
        chat_ids = sessions_coll.distinct(
            "chat_id",
            {"tenant_id": tenant_id, "started_at": {"$gte": month_start}, "chat_id": {"$ne": None}},
        )
        count = len(chat_ids)
        status = usage_status(count, limite)
        signups_coll.update_one(
            {"_id": signup["_id"]},
            {"$set": {
                "usage_current_month": count,
                "usage_limite": limite,
                "usage_pct": round(count / limite, 3) if limite else None,
                "usage_status": status,
                "usage_updated_at": datetime.now(timezone.utc),
            }},
        )
        print(f"[usage-watch] {tenant_id}: {count}/{limite or '∞'} -> {status}")


if __name__ == "__main__":
    run()
