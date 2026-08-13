#!/usr/bin/env python3
"""Provisiona um tenant novo do AtendAgente de ponta a ponta (Fase 3).

Roda NO SERVIDOR (root@62.238.103.17), onde `kubectl` já aponta pro
cluster/namespace certo — não precisa de kubeconfig local. Colapsa num
comando só o que foi feito manualmente na Fase 1: registro DNS,
Secret + PVC + Deployment + Service + Ingress, geração do SOUL.md
(reaproveita tools/soul-generator/), e validação de /health.

Credenciais sensíveis SÓ por variável de ambiente — nunca no YAML do
tenant (que pode ir pro git) nem em argumento de linha de comando (fica
visível em `ps aux` e no histórico do shell).

Uso:
    export WHATSAPP_ACCESS_TOKEN=...
    export WHATSAPP_APP_SECRET=...
    export OPENROUTER_API_KEY=...
    export CLOUDFLARE_API_TOKEN=...
    export CLOUDFLARE_ZONE_ID=...      # zone do domínio usado no tenant
    export SERVER_IP=62.238.103.17     # IP pro registro DNS
    python3 provision_tenant.py tenants/exemplo-tenant.yaml

As mesmas duas pegadinhas descobertas na Fase 1 (ver
infra_atendagente_k3s na memória do projeto) já vêm embutidas no
template de manifests abaixo: GATEWAY_ALLOW_ALL_USERS=true e a chave do
provedor de LLM são sempre incluídas no Secret do tenant.
"""
import argparse
import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "soul-generator"))
from generate_soul import render as render_soul  # noqa: E402  (Fase 2)

NAMESPACE = "atendagente"
REQUIRED_ENV = [
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_APP_SECRET",
    "OPENROUTER_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ZONE_ID",
    "SERVER_IP",
]


class ProvisionError(RuntimeError):
    pass


def require_env(env: dict) -> None:
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        raise ProvisionError(
            "Faltam variáveis de ambiente obrigatórias: " + ", ".join(missing)
        )


def kubectl_apply(manifest_yaml: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest_yaml,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"kubectl apply falhou:\n{result.stderr}")
    print(result.stdout)


def kubectl_exec_stdin(deployment: str, remote_cmd: list[str], stdin_text: str) -> None:
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", "-i", f"deploy/{deployment}", "--", *remote_cmd],
        input=stdin_text,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"kubectl exec falhou:\n{result.stderr}")


def create_dns_record(subdomain_host: str, ip: str, token: str, zone_id: str) -> None:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    payload = json.dumps(
        {"type": "A", "name": subdomain_host, "content": ip, "ttl": 300, "proxied": False}
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        # Já existe o registro? Cloudflare retorna 81057 ("record already exists")
        # ou 81058 ("identical record already exists") nesse caso — não é fatal,
        # mesmo vindo como HTTP 400/HTTPError em vez de um success:false em 200.
        if any(err.get("code") in (81057, 81058) for err in body.get("errors", [])):
            print(f"  (registro DNS de {subdomain_host} já existia, seguindo)")
            return
        raise ProvisionError(f"Cloudflare API HTTP {e.code}: {body}") from e

    if not body.get("success"):
        if any(err.get("code") in (81057, 81058) for err in body.get("errors", [])):
            print(f"  (registro DNS de {subdomain_host} já existia, seguindo)")
            return
        raise ProvisionError(f"Cloudflare API rejeitou o registro: {body['errors']}")
    print(f"  DNS criado: {subdomain_host} -> {ip}")


def build_infra_manifest(tenant_id: str, host: str) -> str:
    """PVC + Deployment + Service + Ingress — mesmo padrão validado na Fase 1."""
    prefix = f"{tenant_id}-hermes"
    return f"""\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {prefix}-data
  namespace: {NAMESPACE}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources:
    requests:
      storage: 2Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {prefix}
  namespace: {NAMESPACE}
  labels: {{app: {prefix}, tenant: {tenant_id}}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: {prefix}}}
  template:
    metadata:
      labels: {{app: {prefix}, tenant: {tenant_id}}}
    spec:
      containers:
        - name: hermes
          image: nousresearch/hermes-agent:latest
          args: ["gateway", "run"]
          ports:
            - name: webhook
              containerPort: 8090
          envFrom:
            - secretRef:
                name: {prefix}-env
          volumeMounts:
            - name: data
              mountPath: /opt/data
          resources:
            requests: {{cpu: "250m", memory: "512Mi"}}
            limits: {{cpu: "1000m", memory: "1Gi"}}
          readinessProbe:
            httpGet: {{path: /health, port: 8090}}
            initialDelaySeconds: 15
            periodSeconds: 10
          livenessProbe:
            httpGet: {{path: /health, port: 8090}}
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: {prefix}-data
---
apiVersion: v1
kind: Service
metadata:
  name: {prefix}
  namespace: {NAMESPACE}
spec:
  selector: {{app: {prefix}}}
  ports:
    - name: webhook
      port: 80
      targetPort: 8090
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {prefix}
  namespace: {NAMESPACE}
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: traefik
  rules:
    - host: {host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {prefix}
                port:
                  number: 80
  tls:
    - hosts: [{host}]
      secretName: {prefix}-tls
"""


def build_secret_manifest(tenant_id: str, phone_number_id: str, waba_id: str, env: dict) -> str:
    prefix = f"{tenant_id}-hermes"
    verify_token = secrets.token_urlsafe(24)
    return f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {prefix}-env
  namespace: {NAMESPACE}
type: Opaque
stringData:
  WHATSAPP_CLOUD_PHONE_NUMBER_ID: "{phone_number_id}"
  WHATSAPP_CLOUD_WABA_ID: "{waba_id}"
  WHATSAPP_CLOUD_VERIFY_TOKEN: "{verify_token}"
  WHATSAPP_CLOUD_ENABLED: "true"
  WHATSAPP_CLOUD_ACCESS_TOKEN: "{env['WHATSAPP_ACCESS_TOKEN']}"
  WHATSAPP_CLOUD_APP_SECRET: "{env['WHATSAPP_APP_SECRET']}"
  OPENROUTER_API_KEY: "{env['OPENROUTER_API_KEY']}"
  GATEWAY_ALLOW_ALL_USERS: "true"
""", verify_token


def wait_for_health(deployment: str, timeout_s: int = 120) -> bool:
    prefix = deployment
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", f"deploy/{prefix}", f"--timeout={timeout_s}s"],
        text=True,
        capture_output=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return False
    return True


def provision(tenant_config_path: Path, env: dict, dry_run: bool = False) -> None:
    config = yaml.safe_load(tenant_config_path.read_text(encoding="utf-8"))

    tenant_id = config["tenant_id"]
    host = config["dominio"]
    phone_number_id = config["whatsapp"]["phone_number_id"]
    waba_id = config["whatsapp"]["waba_id"]
    prefix = f"{tenant_id}-hermes"

    print(f"== Provisionando tenant '{tenant_id}' ({host}) {'[DRY RUN]' if dry_run else ''} ==")

    secret_yaml, verify_token = build_secret_manifest(tenant_id, phone_number_id, waba_id, env)
    infra_yaml = build_infra_manifest(tenant_id, host)
    soul_text = render_soul(config)

    if dry_run:
        print("--- Secret (valores sensíveis mascarados) ---")
        for line in secret_yaml.splitlines():
            if any(k in line for k in ("ACCESS_TOKEN", "APP_SECRET", "OPENROUTER_API_KEY", "VERIFY_TOKEN")):
                key = line.split(":", 1)[0]
                print(f"{key}: <mascarado>")
            else:
                print(line)
        print("--- Manifests de infra (PVC/Deployment/Service/Ingress) ---")
        print(infra_yaml)
        print("--- SOUL.md gerado ---")
        print(soul_text)
        print(f"(dry-run: nenhuma chamada real a kubectl/Cloudflare foi feita; "
              f"registro DNS seria {host} -> {env.get('SERVER_IP') or '<SERVER_IP não setado>'})")
        return

    print("1/5 Registro DNS...")
    create_dns_record(host, env["SERVER_IP"], env["CLOUDFLARE_API_TOKEN"], env["CLOUDFLARE_ZONE_ID"])

    print("2/5 Secret (credenciais)...")
    kubectl_apply(secret_yaml)

    print("3/5 PVC + Deployment + Service + Ingress...")
    kubectl_apply(infra_yaml)

    print("4/5 Aguardando pod ficar pronto...")
    if not wait_for_health(prefix):
        raise ProvisionError(
            f"Pod não ficou pronto a tempo — inspecionar com "
            f"'kubectl -n {NAMESPACE} logs -l app={prefix}' e os arquivos em "
            f"/opt/data/logs/ dentro do pod (kubectl logs pode atrasar, ver "
            f"memória infra_atendagente_k3s)."
        )

    print("5/5 Gerando e publicando SOUL.md...")
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat > /opt/data/SOUL.md"], soul_text)
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}"], check=True)
    wait_for_health(prefix)

    print(f"\n✓ Tenant '{tenant_id}' no ar em https://{host}/whatsapp/webhook")
    print(f"  Verify token pro cadastro do webhook na Meta: {verify_token}")
    print(
        "  (não fica salvo em lugar nenhum além do Secret do cluster — "
        "anote agora se for configurar o webhook na Meta.)"
    )


def main() -> None:
    # Garante UTF-8 na saída mesmo em console Windows (cp1252 por padrão),
    # já que SOUL/manifests podem conter emoji ou acentuação.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tenant_yaml", type=Path)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Renderiza SOUL/manifests e mostra o que seria feito, sem chamar kubectl/Cloudflare de verdade.",
    )
    args = parser.parse_args()

    import os

    env = {k: os.environ.get(k, "") for k in REQUIRED_ENV}
    try:
        if args.dry_run:
            # Em dry-run, credenciais ausentes viram placeholders visíveis em vez de erro,
            # pra dar pra testar a lógica de geração sem ter as chaves reais em mãos.
            for k in env:
                if not env[k]:
                    env[k] = f"<{k}-nao-setado>"
        else:
            require_env(env)
        provision(args.tenant_yaml, env, dry_run=args.dry_run)
    except ProvisionError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
