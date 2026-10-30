"""Pod de tenant: imagens por configuração, sem hostPath/ConfigMap de código (Fase 13)."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provision_tenant as pt  # noqa: E402

IMG = {
    "TENANT_HERMES_IMAGE": "ghcr.io/x/atendpragente-hermes:abc1234",
    "TENANT_PANEL_IMAGE": "ghcr.io/x/atendpragente-panel:abc1234",
    "TENANT_SYNC_IMAGE": "ghcr.io/x/atendpragente-sync:abc1234",
}


def docs(env):
    return list(yaml.safe_load_all(pt.build_infra_manifest("loja", "loja.exemplo.com.br", pt.tenant_images(env))))


def pods(ds):
    return {d["metadata"]["name"]: d["spec"]["template"]["spec"] for d in ds if d["kind"] == "Deployment"}


def test_imagens_vem_da_configuracao_e_sem_codigo_montado():
    p = pods(docs(IMG))
    assert p["loja-hermes"]["containers"][0]["image"] == IMG["TENANT_HERMES_IMAGE"]
    assert p["loja-hermes"]["containers"][1]["image"] == IMG["TENANT_SYNC_IMAGE"]
    assert p["loja-hermes-panel"]["containers"][0]["image"] == IMG["TENANT_PANEL_IMAGE"]
    texto = pt.build_infra_manifest("loja", "h.com", pt.tenant_images(IMG))
    for proibido in ("hostPath", "configMap", ":latest", "pip install", "python:3.12-slim"):
        assert proibido not in texto
    for pod in p.values():
        assert all("hostPath" not in v for v in pod.get("volumes", []))


def test_sync_roda_com_o_uid_do_hermes_e_le_o_volume_somente_leitura():
    sync = pods(docs(IMG))["loja-hermes"]["containers"][1]
    assert sync["securityContext"]["runAsUser"] == 10000
    assert sync["volumeMounts"] == [{"name": "data", "mountPath": "/opt/data", "readOnly": True}]


def test_pull_secret_so_quando_configurado():
    assert "imagePullSecrets" not in pods(docs(IMG))["loja-hermes"]
    com = pods(docs({**IMG, "TENANT_IMAGE_PULL_SECRET": "ghcr-pull-secret"}))
    for nome in ("loja-hermes", "loja-hermes-panel"):
        assert com[nome]["imagePullSecrets"] == [{"name": "ghcr-pull-secret"}]


def test_sem_imagem_configurada_falha_alto():
    with pytest.raises(pt.ProvisionError, match="TENANT_PANEL_IMAGE"):
        pt.tenant_images({k: v for k, v in IMG.items() if k != "TENANT_PANEL_IMAGE"})


def test_dry_run_usa_placeholders_visiveis():
    assert pt.tenant_images({}, placeholders=True)["hermes"] == "<TENANT_HERMES_IMAGE-nao-setado>"
