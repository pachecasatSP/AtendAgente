"""Leva TODAS as imagens do AtendPraGente em execucao no cluster para uma tag (o commit curto publicado pelo workflow `imagens`).

Descobre os Deployments e CronJobs dos namespaces atendagente-admin e atendagente cujas imagens sao ghcr.io/pachecasatsp/atendpragente-<nome>:<tag>
(inclui os tenants criados dinamicamente) e as variaveis TENANT_*_IMAGE do onboarding-service (as imagens dos tenants NOVOS). Troca so a tag:
nada de manifestos, nada de Secrets. Um patch por workload (um unico rollout), um Deployment por vez, esperando ficar pronto antes do proximo;
na primeira falha PARA e diz o que ja foi trocado e como voltar. Rodar de novo com a mesma tag e inofensivo.

  python implantar.py <tag> [--simular]          (KUBECONFIG ja apontando para o cluster; KUBECTL para trocar o binario)
O que isto NAO faz: aplicar manifestos novos (env, volumes, RBAC: continua `infra-aws/scripts/atendpragente/aplicar.sh`) nem regenerar o SOUL.md dos
tenants (`atualizar-tenant.sh`). Os Pods dos tenants reiniciam: ha uma breve pausa no WhatsApp de cada um.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

NAMESPACES = ("atendagente-admin", "atendagente")  # o onboarding-service e a Duda primeiro; depois os tenants
IMAGEM = re.compile(r"^(ghcr\.io/pachecasatsp/atendpragente-(?:tools|panel|sync|backoffice|hermes)):([0-9a-f]{7,40})$")
TAG_OK = re.compile(r"^[0-9a-f]{7,40}$")
TIMEOUT_ROLLOUT = os.environ.get("TIMEOUT_ROLLOUT", "300s")


def kubectl(*args, check=True):
    cmd = shlex.split(os.environ.get("KUBECTL", "kubectl")) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args[:4])} falhou (codigo {r.returncode}):\n{r.stderr.strip()}")
    return r


def trocar(valor, tag):
    """Mesma imagem do AtendPraGente com a tag nova; None se nao for uma (ou se ja estiver na tag)."""
    m = IMAGEM.match(valor or "")
    if not m or m.group(2) == tag:
        return None
    return f"{m.group(1)}:{tag}"


def _pod_spec(item):
    if item["kind"] == "Deployment":
        return item["spec"]["template"]["spec"], ("spec", "template", "spec")
    return item["spec"]["jobTemplate"]["spec"]["template"]["spec"], ("spec", "jobTemplate", "spec", "template", "spec")


def descobrir(tag):
    """Lista o que precisa mudar: [{ns, kind, nome, containers: [(nome, atual, novo, [(VAR, atual, novo)])]}]."""
    plano = []
    for ns in NAMESPACES:
        dados = json.loads(kubectl("-n", ns, "get", "deployments,cronjobs", "-o", "json").stdout)
        for item in sorted(dados["items"], key=lambda i: (i["kind"] != "Deployment", i["metadata"]["name"])):
            spec, caminho = _pod_spec(item)
            containers = []
            for c in spec.get("containers", []):
                nova = trocar(c.get("image"), tag)
                envs = [(e["name"], e["value"], trocar(e.get("value"), tag)) for e in c.get("env", []) if trocar(e.get("value"), tag)]
                if nova or envs:
                    containers.append((c["name"], c.get("image"), nova, envs))
            if containers:
                plano.append({"ns": ns, "kind": item["kind"], "nome": item["metadata"]["name"], "caminho": caminho, "containers": containers})
    return plano


def patch_de(item):
    cs = []
    for nome, _atual, nova, envs in item["containers"]:
        c = {"name": nome}
        if nova:
            c["image"] = nova
        if envs:
            c["env"] = [{"name": v, "value": novo} for v, _a, novo in envs]
        cs.append(c)
    corpo = {"containers": cs}
    for chave in reversed(item["caminho"]):
        corpo = {chave: corpo}
    return json.dumps(corpo)


def descrever(item):
    partes = []
    for nome, atual, nova, envs in item["containers"]:
        if nova:
            partes.append(f"{nome}: {atual.rsplit(':', 1)[1]} -> {nova.rsplit(':', 1)[1]}")
        partes += [f"env {v}: {a.rsplit(':', 1)[1]} -> {n.rsplit(':', 1)[1]}" for v, a, n in envs]
    return "; ".join(partes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tag")
    ap.add_argument("--simular", action="store_true", help="so mostra o que seria trocado")
    args = ap.parse_args(argv)
    if not TAG_OK.match(args.tag):
        raise SystemExit(f"tag '{args.tag}' nao parece um commit curto (hex, 7 a 40 caracteres).")

    plano = descobrir(args.tag)
    if not plano:
        # nada a trocar: ou ja esta tudo na tag, ou este nao e o cluster certo. Distingue, para nao dar "ok" no cluster errado.
        total = sum(len(json.loads(kubectl("-n", ns, "get", "deployments", "-o", "json").stdout)["items"]) for ns in NAMESPACES)
        if total == 0:
            raise SystemExit("nenhum Deployment em atendagente-admin/atendagente: este e o cluster certo?")
        print(f"Nada a trocar: tudo que usa imagens do AtendPraGente ja esta em {args.tag}.")
        return 0

    print(f"{'SIMULACAO: ' if args.simular else ''}{len(plano)} workload(s) para a tag {args.tag}")
    for item in plano:
        print(f"  {item['ns']}/{item['kind'].lower()}/{item['nome']}  {descrever(item)}")
    if args.simular:
        return 0

    feitos = []
    for item in plano:
        alvo = f"{item['kind'].lower()}/{item['nome']}"
        print(f">> {item['ns']}/{alvo}", flush=True)
        kubectl("-n", item["ns"], "patch", alvo, "--type", "strategic", "-p", patch_de(item))
        feitos.append(item)
        if item["kind"] == "Deployment":
            r = kubectl("-n", item["ns"], "rollout", "status", alvo, f"--timeout={TIMEOUT_ROLLOUT}", check=False)
            if r.returncode != 0:
                print(f"\nFALHA no rollout de {item['ns']}/{alvo}: {(r.stdout + r.stderr).strip()[-400:]}", file=sys.stderr)
                print(f"Parei aqui. Ja trocados: {', '.join(i['nome'] for i in feitos)}. Os demais continuam na tag antiga.", file=sys.stderr)
                print("Para investigar: kubectl -n %s describe %s ; para voltar, rode este script com a tag anterior." % (item["ns"], alvo), file=sys.stderr)
                return 1
    print(f"\nPronto: {len(feitos)} workload(s) em {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
