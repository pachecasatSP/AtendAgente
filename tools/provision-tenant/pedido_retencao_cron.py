#!/usr/bin/env python3
"""Roda 1x/dia como CronJob no namespace atendagente (Fase 12) —
retenção com prazo, mesmo espírito da lixeira (Fase 9):

1. Pedido sem conclusão (nunca chegou a "pago") há mais de
   PEDIDO_RETENCAO_DIAS dias vira "cancelado" automaticamente —
   mitiga carrinho abandonado sem deixar lixo acumulando.
2. Comprovante (dado financeiro/pessoal) é apagado do object storage
   PEDIDO_RETENCAO_DIAS dias depois de recebido — minimização de
   finalidade e direito à eliminação (LGPD art. 6º/15º/16º): depois
   que a finalidade (confirmar aquele pagamento específico) se esgota,
   não há motivo pra manter guardado. O doc do pedido em si continua
   (histórico de venda), só a imagem some.

Não manda lembrete de carrinho abandonado — isso ficou previsto pro
schema (status/criado_em já suportam) mas explicitamente não
implementado nesta fase (decisão do usuário, 2026-08-20).
"""
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config as BotoConfig
from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
PEDIDO_RETENCAO_DIAS = int(os.environ.get("PEDIDO_RETENCAO_DIAS", "30"))

OBJECT_STORAGE_ENDPOINT = os.environ.get("OBJECT_STORAGE_ENDPOINT", "")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "")
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "")

_s3_client = None


def _object_storage_configured() -> bool:
    return bool(OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_BUCKET and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY)


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3", endpoint_url=OBJECT_STORAGE_ENDPOINT,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


def cancelar_pedidos_orfaos(pedidos_coll, limite: datetime) -> int:
    """Só cancela pedido genuinamente abandonado — "aberto" (cliente
    nunca fechou pela conversa) ou "aguardando_pagamento" (fechou, mas
    nunca mandou comprovante). NÃO inclui "comprovante_recebido": o
    cliente já pagou e mandou prova, só falta o tenant confirmar
    manualmente — cancelar isso perderia uma venda de verdade."""
    resultado = pedidos_coll.update_many(
        {"status": {"$in": ["aberto", "aguardando_pagamento"]}, "criado_em": {"$lt": limite}},
        {"$set": {"status": "cancelado", "cancelado_em": datetime.now(timezone.utc), "cancelado_motivo": "orfao_30_dias"}},
    )
    return resultado.modified_count


def apagar_comprovantes_vencidos(pedidos_coll, limite: datetime) -> int:
    if not _object_storage_configured():
        print("[pedido-retencao] object storage não configurado — pulando limpeza de comprovantes")
        return 0
    candidatos = list(pedidos_coll.find({
        "comprovante_key": {"$exists": True, "$ne": None},
        "comprovante_recebido_em": {"$lt": limite},
    }))
    apagados = 0
    for pedido in candidatos:
        try:
            _get_s3_client().delete_object(Bucket=OBJECT_STORAGE_BUCKET, Key=pedido["comprovante_key"])
        except Exception as e:
            print(f"[pedido-retencao] falha ao apagar comprovante de {pedido['_id']}: {e}")
            continue
        pedidos_coll.update_one(
            {"_id": pedido["_id"]},
            {"$unset": {"comprovante_key": ""}, "$set": {"comprovante_apagado_em": datetime.now(timezone.utc)}},
        )
        apagados += 1
    return apagados


def run() -> None:
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    pedidos_coll = db["pedidos"]

    limite = datetime.now(timezone.utc) - timedelta(days=PEDIDO_RETENCAO_DIAS)
    limite_naive = limite.replace(tzinfo=None)  # Mongo grava datetime naive-UTC, ver check_and_book

    cancelados = cancelar_pedidos_orfaos(pedidos_coll, limite_naive)
    print(f"[pedido-retencao] {cancelados} pedido(s) órfão(s) cancelado(s) (> {PEDIDO_RETENCAO_DIAS} dias sem conclusão)")

    apagados = apagar_comprovantes_vencidos(pedidos_coll, limite_naive)
    print(f"[pedido-retencao] {apagados} comprovante(s) apagado(s) (> {PEDIDO_RETENCAO_DIAS} dias)")


if __name__ == "__main__":
    run()
