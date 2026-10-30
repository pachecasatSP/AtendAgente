"""O env que o onboarding passa ao provisionamento tem de bastar para montar o Secret do tenant (antes faltava GROQ_API_KEY -> KeyError)."""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / "onboarding-service"), str(TOOLS / "provision-tenant")]

ENV = {"WHATSAPP_CLOUD_APP_ID": "1", "WHATSAPP_CLOUD_APP_SECRET": "s", "META_CONFIG_ID": "1", "OPENROUTER_API_KEY": "o",
       "GROQ_API_KEY": "g", "CLOUDFLARE_API_TOKEN": "c", "CLOUDFLARE_ZONE_ID": "z", "SERVER_IP": "1.2.3.4",
       "ASAAS_API_KEY": "a", "ASAAS_WEBHOOK_TOKEN": "w", "MONGO_URI": "mongodb://127.0.0.1:1/t"}


def carregar(monkeypatch, sem=None):
    for k, v in ENV.items():
        if k != sem:
            monkeypatch.setenv(k, v)
    if sem:
        monkeypatch.delenv(sem, raising=False)
    sys.modules.pop("app.main", None)
    sys.modules.pop("app", None)
    import app.main as m
    return m


def test_env_do_provisionamento_basta_para_o_secret_do_tenant(monkeypatch):
    import provision_tenant as pt
    m = carregar(monkeypatch)
    env = m.provisioning_env()
    env["WHATSAPP_ACCESS_TOKEN"] = "t"
    manifesto, _ = pt.build_secret_manifest("loja", "123", "456", env, {"telefone": "11900000000", "nome": "Ana"})
    assert 'GROQ_API_KEY: "g"' in manifesto


def test_sem_groq_falha_alto_no_provisionamento(monkeypatch):
    m = carregar(monkeypatch, sem="GROQ_API_KEY")
    with pytest.raises(KeyError, match="GROQ_API_KEY"):
        m.provisioning_env()
