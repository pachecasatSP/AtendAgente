"""Fixtures do backoffice comercial: Mongo em memória (mongomock) e um
onboarding-service de mentira. Nada aqui toca rede nem Mongo de verdade."""
import os
import sys
from pathlib import Path

import mongomock
import pytest

# Variáveis lidas na importação de app.store / app.main / app.onboarding_client.
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:1/teste")  # porta morta: o MongoClient é lazy e nunca conecta
os.environ.setdefault("BACKOFFICE_SESSION_SECRET", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("ONBOARDING_ADMIN_KEY", "chave-de-teste")
os.environ.setdefault("ADMIN_PIN", "1234")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import onboarding_client, store  # noqa: E402

SENHA = "senha-de-teste-123"


@pytest.fixture
def db(monkeypatch):
    """Troca as coleções do store por coleções mongomock, limpas a cada teste."""
    banco = mongomock.MongoClient().db
    for nome, colecao in {
        "_vendedores": "vendedores",
        "_leads": "leads",
        "_atribuicoes": "atribuicoes_comissao",
        "_prospeccoes": "prospeccoes",
        "_prospeccao_estado": "prospeccao_estado",
    }.items():
        monkeypatch.setattr(store, nome, banco[colecao])
    return banco


class OnboardingFalso:
    """Registra o que o backoffice mandaria ao onboarding-service."""

    def __init__(self):
        self.tenants: list[dict] = []
        self.atribuicoes: list[tuple] = []
        self.erro = None  # exceção a levantar em atribuir_vendedor

    def listar_tenants(self):
        return [dict(t) for t in self.tenants]

    def atribuir_vendedor(self, tenant_id, vendedor_id, origem_venda):
        if self.erro:
            raise self.erro
        self.atribuicoes.append((tenant_id, vendedor_id, origem_venda))
        return {"ok": True}


@pytest.fixture
def onboarding(monkeypatch):
    falso = OnboardingFalso()
    monkeypatch.setattr(onboarding_client, "listar_tenants", falso.listar_tenants)
    monkeypatch.setattr(onboarding_client, "atribuir_vendedor", falso.atribuir_vendedor)
    return falso


def tenant(tenant_id="loja-a", plano="comecando", **extra):
    """Linha no formato de GET /api/admin/tenants do onboarding-service
    (plano é a CHAVE de PLANOS, em minúsculas, sem acento)."""
    return {
        "tenant_id": tenant_id,
        "dominio": f"{tenant_id}.atendpragente.com.br",
        "plano": plano,
        "criado_em": None,
        "gratuito": False,
        "vendedor_slug_hint": None,
        "vendedor_id": None,
        **extra,
    }


def logar(cliente, username):
    resposta = cliente.post("/login", data={"username": username, "password": SENHA})
    assert resposta.status_code == 303, resposta.text
    return cliente


@pytest.fixture
def novo_cliente(db, onboarding):
    """Fábrica de clientes HTTP logados: novo_cliente("ana") ou (…, role="admin")."""
    from starlette.testclient import TestClient

    from app.main import app

    def fabricar(username="ana", role="vendedor"):
        vendedor_id = store.create_vendedor(username.title(), f"{username}@x.com", "", username, SENHA, role=role)
        cliente = TestClient(app, follow_redirects=False)
        logar(cliente, username)
        return cliente, vendedor_id

    return fabricar
