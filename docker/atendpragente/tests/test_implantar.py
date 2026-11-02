"""implantar.py: troca so a tag das imagens do AtendPraGente, um Deployment por vez, e para na primeira falha."""
import json
import sys
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import implantar as im  # noqa: E402

R = "ghcr.io/pachecasatsp/atendpragente-"


def deploy(nome, containers):
    return {"kind": "Deployment", "metadata": {"name": nome},
            "spec": {"template": {"spec": {"containers": [{"name": n, "image": i, **({"env": e} if e else {})} for n, i, e in containers]}}}}


def cron(nome, imagem):
    return {"kind": "CronJob", "metadata": {"name": nome},
            "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [{"name": "c", "image": imagem}]}}}}}}


ESTADO = {
    "atendagente-admin": [
        deploy("onboarding-service", [("onboarding", f"{R}tools:aaaaaaa", [
            {"name": "TENANT_HERMES_IMAGE", "value": f"{R}hermes:aaaaaaa"},
            {"name": "TENANT_PANEL_IMAGE", "value": f"{R}panel:aaaaaaa"},
            {"name": "MONGO_URI", "value": "mongodb://mongo:27017"},
        ])]),
        deploy("backoffice", [("backoffice", f"{R}backoffice:aaaaaaa", None)]),
        deploy("mongo-backoffice", [("mongo", "mongo@sha256:1234", None)]),
        deploy("outro-produto", [("app", "ghcr.io/pachecasatsp/hupyflow-api:aaaaaaa", None), ("proxy", "nginx:abcdef1", None)]),
        cron("lixeira-watch", f"{R}tools:aaaaaaa"),
    ],
    "atendagente": [
        deploy("linda-ana-hermes", [("hermes", f"{R}hermes:aaaaaaa", None), ("mongo-sync", f"{R}sync:aaaaaaa", None)]),
        deploy("linda-ana-hermes-panel", [("panel", f"{R}panel:aaaaaaa", None)]),
        deploy("mongo", [("mongo", "mongo@sha256:abcd", None)]),
    ],
}


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    estado, log = tmp_path / "estado.json", tmp_path / "log.txt"
    estado.write_text(json.dumps(ESTADO))
    log.write_text("")
    monkeypatch.setenv("KUBECTL", f'"{Path(sys.executable).as_posix()}" "{(AQUI / "kubectl_falso.py").as_posix()}"')
    monkeypatch.setenv("FAKE_STATE", str(estado))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_FALHA_ROLLOUT", raising=False)

    class C:
        def estado(self):
            return json.loads(estado.read_text())

        def log(self):
            return log.read_text().splitlines()

        def item(self, ns, nome):
            return next(i for i in self.estado()[ns] if i["metadata"]["name"] == nome)

        def imagens(self, ns, nome):
            item = self.item(ns, nome)
            spec = item["spec"]["template"]["spec"] if item["kind"] == "Deployment" else item["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            return {c["name"]: c["image"] for c in spec["containers"]}
    return C()


def test_troca_a_tag_de_tudo_que_e_do_atendpragente(cluster):
    assert im.main(["bbbbbbb"]) == 0
    assert cluster.imagens("atendagente-admin", "backoffice") == {"backoffice": f"{R}backoffice:bbbbbbb"}
    assert cluster.imagens("atendagente-admin", "onboarding-service") == {"onboarding": f"{R}tools:bbbbbbb"}
    assert cluster.imagens("atendagente-admin", "lixeira-watch") == {"c": f"{R}tools:bbbbbbb"}
    assert cluster.imagens("atendagente", "linda-ana-hermes") == {"hermes": f"{R}hermes:bbbbbbb", "mongo-sync": f"{R}sync:bbbbbbb"}
    assert cluster.imagens("atendagente", "linda-ana-hermes-panel") == {"panel": f"{R}panel:bbbbbbb"}


def test_imagens_dos_tenants_novos_acompanham_as_variaveis_do_onboarding(cluster):
    im.main(["bbbbbbb"])
    envs = {e["name"]: e["value"] for e in cluster.item("atendagente-admin", "onboarding-service")["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert envs["TENANT_HERMES_IMAGE"] == f"{R}hermes:bbbbbbb" and envs["TENANT_PANEL_IMAGE"] == f"{R}panel:bbbbbbb"
    assert envs["MONGO_URI"] == "mongodb://mongo:27017"  # as outras variaveis ficam como estavam


def test_nao_toca_no_que_nao_e_do_atendpragente(cluster):
    im.main(["bbbbbbb"])
    assert cluster.imagens("atendagente-admin", "mongo-backoffice") == {"mongo": "mongo@sha256:1234"}
    assert cluster.imagens("atendagente", "mongo") == {"mongo": "mongo@sha256:abcd"}
    assert cluster.imagens("atendagente-admin", "outro-produto") == {"app": "ghcr.io/pachecasatsp/hupyflow-api:aaaaaaa", "proxy": "nginx:abcdef1"}
    assert not any(("mongo" in linha or "outro" in linha) and "patch" in linha for linha in cluster.log())


def test_um_patch_por_workload_e_rollout_so_dos_deployments(cluster):
    im.main(["bbbbbbb"])
    patches = [linha for linha in cluster.log() if " patch " in f" {linha} "]
    assert len(patches) == 5  # onboarding, backoffice, lixeira-watch, linda-ana-hermes, linda-ana-hermes-panel
    rollouts = [linha for linha in cluster.log() if " rollout " in f" {linha} "]
    assert len(rollouts) == 4 and not any("cronjob" in r for r in rollouts)


def test_rodar_de_novo_nao_faz_nada(cluster, capsys):
    im.main(["bbbbbbb"])
    antes = cluster.estado()
    n_patches = len([l for l in cluster.log() if " patch " in f" {l} "])
    assert im.main(["bbbbbbb"]) == 0
    assert "Nada a trocar" in capsys.readouterr().out
    assert cluster.estado() == antes and len([l for l in cluster.log() if " patch " in f" {l} "]) == n_patches


def test_simular_nao_grava_nada(cluster, capsys):
    antes = cluster.estado()
    assert im.main(["bbbbbbb", "--simular"]) == 0
    assert cluster.estado() == antes and not any(" patch " in f" {l} " for l in cluster.log())
    assert "SIMULACAO" in capsys.readouterr().out


def test_para_na_primeira_falha_de_rollout(cluster, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_FALHA_ROLLOUT", "backoffice")
    assert im.main(["bbbbbbb"]) == 1
    err = capsys.readouterr().err
    assert "FALHA no rollout" in err and "backoffice" in err and "tag anterior" in err
    # o que vinha depois do backoffice nao foi tocado
    assert cluster.imagens("atendagente", "linda-ana-hermes") == {"hermes": f"{R}hermes:aaaaaaa", "mongo-sync": f"{R}sync:aaaaaaa"}


def test_tag_invalida_e_recusada(cluster):
    for ruim in ("latest", "ABCDEF1", "abc", "zzzzzzz"):
        with pytest.raises(SystemExit, match="commit curto"):
            im.main([ruim])


def test_cluster_errado_sem_deployments_nao_diz_ok(cluster, tmp_path, monkeypatch):
    vazio = tmp_path / "vazio.json"
    vazio.write_text(json.dumps({"atendagente-admin": [], "atendagente": []}))
    monkeypatch.setenv("FAKE_STATE", str(vazio))
    with pytest.raises(SystemExit, match="cluster certo"):
        im.main(["bbbbbbb"])
