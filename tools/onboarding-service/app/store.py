"""CRUD fino sobre a coleção `signups` no MongoDB compartilhado
(reaproveita mongo.atendagente.svc.cluster.local, Fase 5) — controle do
estado de cada cadastro em andamento, não dados de conversa.

Nota de dívida técnica (ver plano da Fase 4): o access_token do WABA do
cliente fica gravado aqui entre o passo de troca de code e o passo de
provisionamento (duas requisições HTTP separadas) — não é um cofre de
segredos dedicado, mas aceitável pra V1 do trial já que o Mongo vive
dentro do namespace protegido. Não normalizar esse padrão pra outros
segredos.
"""
import os
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
_client = MongoClient(MONGO_URI)
_db = _client.get_default_database()
_signups = _db["signups"]


def create_signup(waba_id: str, phone_number_id: str, access_token: str) -> str:
    signup_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    _signups.insert_one(
        {
            "_id": signup_id,
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "access_token": access_token,
            "tenant_id": None,
            "status": "code_exchanged",
            "plano": "trial",
            "erro": None,
            "criado_em": now,
            "atualizado_em": now,
        }
    )
    return signup_id


def get_signup(signup_id: str) -> dict | None:
    return _signups.find_one({"_id": signup_id})


def update_signup(signup_id: str, **fields) -> None:
    fields["atualizado_em"] = datetime.now(timezone.utc)
    _signups.update_one({"_id": signup_id}, {"$set": fields})
