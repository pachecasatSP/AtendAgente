#!/usr/bin/env python3
"""Bootstrap único do admin-mcp — servidor MCP de ferramentas
administrativas (criar token de gratuidade, listar/pausar tenants) que
só a Duda consome, via `hermes mcp add` no outro cluster.

Roda NO SERVIDOR (root@62.238.103.17), mesmo padrão de
setup_onboarding_service.py / setup_mongo.py. Idempotente.

Gera os segredos de serviço (MCP_AUTH_TOKEN, ONBOARDING_ADMIN_KEY) na
primeira execução e reaproveita se já existirem — nunca os imprime
depois de criados. O ADMIN_PIN (o PIN que o humano digita pra Duda) é
passado por fora, via variável de ambiente, porque esse sim precisa ser
memorizável e comunicado ao operador.

Uso:
    ADMIN_PIN=123456 python3 setup_admin_mcp.py
    python3 setup_admin_mcp.py --dry-run
"""
import argparse
import base64
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "provision-tenant"))
from provision_tenant import ProvisionError, create_dns_record  # noqa: E402

NAMESPACE = "atendagente"
ENV_SECRET_NAME = "admin-mcp-env"
HOST = "admin-mcp.atendpragente.com.br"


class SetupError(RuntimeError):
    pass


def kubectl_apply(manifest_yaml: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=manifest_yaml, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise SetupError(f"kubectl apply falhou:\n{result.stderr}")
    print(result.stdout)


def resource_exists(kind: str, name: str, namespace: str) -> bool:
    result = subprocess.run(
        ["kubectl", "-n", namespace, "get", kind, name], capture_output=True, text=True
    )
    return result.returncode == 0


def ensure_env_secret(admin_pin: str) -> None:
    if resource_exists("secret", ENV_SECRET_NAME, NAMESPACE):
        print(f"  ({ENV_SECRET_NAME} já existe, reaproveitando MCP_AUTH_TOKEN/ONBOARDING_ADMIN_KEY)")
        # ADMIN_PIN pode mudar sem regenerar os outros segredos.
        pin_b64 = base64.b64encode(admin_pin.encode()).decode()
        subprocess.run(
            ["kubectl", "-n", NAMESPACE, "patch", "secret", ENV_SECRET_NAME, "--type=json",
             "-p", f'[{{"op":"replace","path":"/data/ADMIN_PIN","value":"{pin_b64}"}}]'],
            check=True,
        )
        return

    mcp_auth_token = secrets.token_urlsafe(32)
    admin_api_key = secrets.token_urlsafe(32)
    kubectl_apply(f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {ENV_SECRET_NAME}
  namespace: {NAMESPACE}
type: Opaque
stringData:
  MCP_AUTH_TOKEN: "{mcp_auth_token}"
  ADMIN_PIN: "{admin_pin}"
  ONBOARDING_ADMIN_KEY: "{admin_api_key}"
  ONBOARDING_BASE_URL: "https://onboarding.atendpragente.com.br"
""")
    # onboarding-service precisa da MESMA chave pra aceitar as chamadas
    # do admin-mcp em /api/admin/*.
    subprocess.run(
        ["kubectl", "-n", "atendagente-onboarding", "patch", "secret", "onboarding-service-env",
         "--type=json", "-p",
         f'[{{"op":"add","path":"/data/ADMIN_API_KEY","value":"{base64.b64encode(admin_api_key.encode()).decode()}"}}]'],
        check=True,
    )
    subprocess.run(
        ["kubectl", "-n", "atendagente-onboarding", "rollout", "restart", "deploy/onboarding-service"],
        check=True,
    )
    print("  MCP_AUTH_TOKEN e ONBOARDING_ADMIN_KEY gerados (não impressos — ficam só nos Secrets).")
    print("  Guarde a URL/token de conexão que o passo final deste script vai imprimir.")


def build_service_manifest() -> str:
    start_cmd = (
        "pip install --quiet --no-cache-dir -r /app/admin-mcp/requirements.txt && "
        "python /app/admin-mcp/server.py"
    )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: admin-mcp
  namespace: {NAMESPACE}
  labels: {{app: admin-mcp}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: admin-mcp}}
  template:
    metadata:
      labels: {{app: admin-mcp}}
    spec:
      containers:
        - name: admin-mcp
          image: python:3.12-slim
          command: ["sh", "-c", "{start_cmd}"]
          env:
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: {ENV_SECRET_NAME}
          volumeMounts:
            - name: tools
              mountPath: /app
              readOnly: true
          resources:
            requests: {{cpu: "50m", memory: "128Mi"}}
            limits: {{cpu: "200m", memory: "256Mi"}}
      volumes:
        - name: tools
          hostPath:
            path: /root/atendagente-tools
            type: Directory
---
apiVersion: v1
kind: Service
metadata:
  name: admin-mcp
  namespace: {NAMESPACE}
spec:
  selector: {{app: admin-mcp}}
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: admin-mcp
  namespace: {NAMESPACE}
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: traefik
  rules:
    - host: {HOST}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: admin-mcp
                port:
                  number: 8000
  tls:
    - hosts: [{HOST}]
      secretName: admin-mcp-tls
"""


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("--- Deployment/Service/Ingress do admin-mcp ---")
        print(build_service_manifest())
        return

    admin_pin = os.environ.get("ADMIN_PIN")
    if not admin_pin:
        print("ERRO: defina ADMIN_PIN (o PIN que você vai digitar pra Duda) antes de rodar.", file=sys.stderr)
        sys.exit(1)

    server_ip = os.environ.get("SERVER_IP", "62.238.103.17")
    cf_zone = os.environ.get("CLOUDFLARE_ZONE_ID", "eff07b89ce80fc01d01533b3327b209a")
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not cf_token:
        token_file = Path(os.environ.get("CLOUDFLARE_TOKEN_FILE", "/root/.cloudflare_api_token"))
        if token_file.exists():
            cf_token = token_file.read_text(encoding="utf-8").strip()
    if not cf_token:
        print("ERRO: token da Cloudflare não encontrado.", file=sys.stderr)
        sys.exit(1)

    try:
        print("1/3 Secret de credenciais (admin-mcp-env)...")
        ensure_env_secret(admin_pin)

        print("2/3 DNS...")
        create_dns_record(HOST, server_ip, cf_token, cf_zone)

        print("3/3 Deployment/Service/Ingress...")
        kubectl_apply(build_service_manifest())

        print(f"\n✓ admin-mcp no ar em https://{HOST}")
        print("  Pra conectar a Duda: no servidor 2.28.15.6,")
        print("  kubectl -n hermes exec -it deploy/hermes-duda -- hermes mcp add atendpragente-admin \\")
        print(f"    --url https://{HOST}/mcp --auth header")
        print("  (vai pedir o valor do header — usar: Authorization: Bearer <MCP_AUTH_TOKEN>,")
        print("   pegue o token com: kubectl -n atendagente get secret admin-mcp-env -o jsonpath='{.data.MCP_AUTH_TOKEN}' | base64 -d)")
    except (SetupError, ProvisionError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
