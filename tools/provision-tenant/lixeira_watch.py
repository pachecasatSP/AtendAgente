#!/usr/bin/env python3
"""Roda 1x/dia como CronJob no namespace atendagente-onboarding (Fase 9)
— varre tenants cancelados cuja janela de restauração (`LIXEIRA_DIAS`,
ver tools/onboarding-service/app/store.py — duplicado aqui de propósito,
mesmo padrão de LIMITES em usage_watch.py) já venceu, e apaga a infra de
verdade: registro DNS na Cloudflare + Deployment/Service/Ingress/Secret/
PVC (hermes + painel) no namespace atendagente, e o login do painel
(panel_auth). Não apaga o doc de `signups` — só marca status="excluido"
(auditoria/cobrança).

Roda sem confirmação humana de propósito (decisão 2026-08-19): é
aplicação de um prazo já comunicado ao cliente no momento do
cancelamento (ver tools/tenant-panel/templates/configuracoes.html), não
uma decisão de IA em tempo real — mesmo racional de "não travar em PIN"
que usage_watch.py já usa pra só medir/sinalizar. Visibilidade pra Duda
fica na ferramenta `lixeira` (admin-mcp), que só consulta.

Precisa do mesmo kubeconfig namespace-scoped do onboarding-service
(Secret onboarding-service-kubeconfig, ver setup_onboarding_service.py)
e do CLOUDFLARE_API_TOKEN/CLOUDFLARE_ZONE_ID do Secret
onboarding-service-env — ver setup_lixeira_watch.py pro bootstrap.
"""
import os
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from provision_tenant import ProvisionError, delete_tenant_infra

MONGO_URI = os.environ["MONGO_URI"]
CLOUDFLARE_API_TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]
CLOUDFLARE_ZONE_ID = os.environ["CLOUDFLARE_ZONE_ID"]
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "atendpragente.com.br")

# Duplicado de tools/onboarding-service/app/store.py (LIXEIRA_DIAS) —
# processos/deploys separados, mesma decisão de não fazer import
# cross-serviço por uma constante. Se mudar, atualizar nos dois lugares.
LIXEIRA_DIAS = 10


def run() -> None:
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    signups_coll = db["signups"]
    panel_auth_coll = db["panel_auth"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=LIXEIRA_DIAS)
    pendentes = list(signups_coll.find({
        "status": "cancelado",
        "cancelamento_autorizado_em": {"$lte": cutoff},
    }))
    print(f"[lixeira-watch] {len(pendentes)} tenant(s) passaram dos {LIXEIRA_DIAS} dias de lixeira")

    for signup in pendentes:
        tenant_id = signup.get("tenant_id")
        signup_id = signup["_id"]
        if not tenant_id:
            continue
        host = f"{tenant_id}.{BASE_DOMAIN}"
        try:
            delete_tenant_infra(tenant_id, host, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID)
        except ProvisionError as e:
            print(f"[lixeira-watch] {tenant_id}: falhou, tenta de novo no próximo ciclo: {e}")
            continue
        panel_auth_coll.delete_one({"_id": tenant_id})
        signups_coll.update_one(
            {"_id": signup_id},
            {"$set": {"status": "excluido", "excluido_em": datetime.now(timezone.utc)}},
        )
        print(f"[lixeira-watch] {tenant_id}: excluído definitivamente")


if __name__ == "__main__":
    run()
