#!/usr/bin/env python3
"""Bootstrap único do onboarding-service (Fase 4) — namespace próprio
(atendagente-onboarding), RBAC + kubeconfig namespace-scoped restrito a
`atendagente` (NUNCA o kubeconfig admin do cluster, que também
alcançaria o namespace `consultor`), cópia do MONGO_URI compartilhado,
e o Deployment/Service/Ingress do serviço em si.

Roda NO SERVIDOR (root@62.238.103.17) com o kubeconfig admin
(/etc/rancher/k3s/k3s.yaml) só nesse momento de bootstrap — mesmo
padrão de setup_mongo.py. Idempotente.

Pré-requisito: o Secret `onboarding-service-env` (App Secret/ID da
Meta, Config ID, Cloudflare, OpenRouter, SERVER_IP) precisa existir
ANTES de rodar isso, criado manualmente pelo operador via kubectl
interativo — nunca colado no chat do agente. Este script imprime o
comando exato se o Secret não existir.

Uso:
    # token da Cloudflare lido de /root/.cloudflare_api_token (mesmo
    # arquivo já usado por reprovision-teste-atendagente.sh); zone ID e
    # SERVER_IP já têm default pro cluster atual, só exporte se for
    # diferente.
    python3 setup_onboarding_service.py
    python3 setup_onboarding_service.py --dry-run
"""
import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "provision-tenant"))
from provision_tenant import ProvisionError, create_dns_record  # noqa: E402

ONBOARDING_NAMESPACE = "atendagente-onboarding"
TENANT_NAMESPACE = "atendagente"
SA_NAME = "onboarding-service"
TOKEN_SECRET_NAME = "onboarding-service-token"
KUBECONFIG_SECRET_NAME = "onboarding-service-kubeconfig"
ENV_SECRET_NAME = "onboarding-service-env"
ONBOARDING_HOST = "onboarding.colocar-me.com.br"

REQUIRED_ENV_SECRET_KEYS = [
    "WHATSAPP_CLOUD_APP_ID",
    "WHATSAPP_CLOUD_APP_SECRET",
    "META_CONFIG_ID",
    "OPENROUTER_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ZONE_ID",
    "SERVER_IP",
]


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


def build_namespace_and_rbac_manifest() -> str:
    return f"""\
apiVersion: v1
kind: Namespace
metadata:
  name: {ONBOARDING_NAMESPACE}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {SA_NAME}
  namespace: {ONBOARDING_NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: onboarding-service-tenant-manager
  namespace: {TENANT_NAMESPACE}
rules:
  - apiGroups: [""]
    resources: ["services", "persistentvolumeclaims", "secrets", "pods"]
    verbs: ["get", "list", "create", "update", "patch"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "create", "update", "patch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: onboarding-service-tenant-manager
  namespace: {TENANT_NAMESPACE}
subjects:
  - kind: ServiceAccount
    name: {SA_NAME}
    namespace: {ONBOARDING_NAMESPACE}
roleRef:
  kind: Role
  name: onboarding-service-tenant-manager
  apiGroup: rbac.authorization.k8s.io
"""


def build_token_secret_manifest() -> str:
    return f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {TOKEN_SECRET_NAME}
  namespace: {ONBOARDING_NAMESPACE}
  annotations:
    kubernetes.io/service-account.name: {SA_NAME}
type: kubernetes.io/service-account-token
"""


def wait_for_token_secret(timeout_s: int = 30) -> tuple[str, str]:
    """Espera o controller de ServiceAccount preencher token/ca.crt no
    Secret clássico (mecanismo durável, não o TokenRequest de 1h)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = subprocess.run(
            ["kubectl", "-n", ONBOARDING_NAMESPACE, "get", "secret", TOKEN_SECRET_NAME, "-o", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout).get("data", {})
            if "token" in data and "ca.crt" in data:
                token = base64.b64decode(data["token"]).decode()
                return token, data["ca.crt"]
        time.sleep(2)
    raise SetupError(
        "Timeout esperando o controller preencher token/ca.crt no Secret "
        f"{TOKEN_SECRET_NAME} — checar se o cluster ainda suporta o "
        "mecanismo clássico de token de ServiceAccount."
    )


def build_kubeconfig_secret_manifest(token: str, ca_crt_b64: str) -> str:
    kubeconfig_yaml = f"""\
apiVersion: v1
kind: Config
clusters:
  - name: atendagente
    cluster:
      server: https://kubernetes.default.svc
      certificate-authority-data: {ca_crt_b64}
contexts:
  - name: atendagente
    context:
      cluster: atendagente
      namespace: {TENANT_NAMESPACE}
      user: onboarding-service
current-context: atendagente
users:
  - name: onboarding-service
    user:
      token: {token}
"""
    indented = "\n".join("    " + line if line else "" for line in kubeconfig_yaml.splitlines())
    return f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {KUBECONFIG_SECRET_NAME}
  namespace: {ONBOARDING_NAMESPACE}
type: Opaque
stringData:
  kubeconfig: |
{indented}
"""


def copy_mongo_uri_secret() -> None:
    if resource_exists("secret", "mongo-credentials", ONBOARDING_NAMESPACE):
        print("  (mongo-credentials já existe em atendagente-onboarding, pulando cópia)")
        return
    result = subprocess.run(
        ["kubectl", "-n", TENANT_NAMESPACE, "get", "secret", "mongo-credentials", "-o", "jsonpath={.data.MONGO_URI}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SetupError(
            f"Não consegui ler mongo-credentials em {TENANT_NAMESPACE} — "
            "rodar tools/provision-tenant/setup_mongo.py primeiro."
        )
    mongo_uri_b64 = result.stdout.strip()
    kubectl_apply(
        f"""\
apiVersion: v1
kind: Secret
metadata:
  name: mongo-credentials
  namespace: {ONBOARDING_NAMESPACE}
type: Opaque
data:
  MONGO_URI: {mongo_uri_b64}
"""
    )


def check_env_secret() -> None:
    if resource_exists("secret", ENV_SECRET_NAME, ONBOARDING_NAMESPACE):
        return
    example_lines = "\n".join(f"{k}=..." for k in REQUIRED_ENV_SECRET_KEYS)
    raise SetupError(
        f"Secret '{ENV_SECRET_NAME}' não existe em {ONBOARDING_NAMESPACE}. "
        "Crie primeiro, digitando os valores direto no servidor via SSH "
        "interativo (nunca colado no chat do agente):\n\n"
        f"  kubectl create namespace {ONBOARDING_NAMESPACE} 2>/dev/null\n"
        "  umask 077 && cat > /root/.onboarding-service-env <<'EOF'\n"
        f"{example_lines}\n"
        "EOF\n"
        f"  kubectl -n {ONBOARDING_NAMESPACE} create secret generic {ENV_SECRET_NAME} "
        "--from-env-file=/root/.onboarding-service-env\n"
        "  shred -u /root/.onboarding-service-env\n\n"
        "Rode este script de novo depois."
    )


def build_service_manifest() -> str:
    # Sem aspas ao redor da URL: o comando inteiro já vai entre aspas
    # duplas no YAML (["sh", "-c", "..."]), aspas duplas aninhadas
    # quebrariam o parser. A URL não tem espaço, não precisa de quoting.
    kubectl_install = (
        "apt-get update -qq && apt-get install -y -qq curl > /dev/null && "
        "curl -Lo /usr/local/bin/kubectl "
        "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl && "
        "chmod +x /usr/local/bin/kubectl"
    )
    start_cmd = (
        f"{kubectl_install} && "
        "pip install --quiet --no-cache-dir -r /app/onboarding-service/requirements.txt && "
        "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir /app/onboarding-service"
    )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: onboarding-service
  namespace: {ONBOARDING_NAMESPACE}
  labels: {{app: onboarding-service}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: onboarding-service}}
  template:
    metadata:
      labels: {{app: onboarding-service}}
    spec:
      serviceAccountName: {SA_NAME}
      containers:
        - name: onboarding-service
          image: python:3.12-slim
          command: ["sh", "-c", "{start_cmd}"]
          env:
            - name: KUBECONFIG
              value: /kubeconfig/kubeconfig
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: {ENV_SECRET_NAME}
            - secretRef:
                name: mongo-credentials
          volumeMounts:
            - name: tools
              mountPath: /app
              readOnly: true
            - name: kubeconfig
              mountPath: /kubeconfig
              readOnly: true
          resources:
            requests: {{cpu: "100m", memory: "256Mi"}}
            limits: {{cpu: "500m", memory: "512Mi"}}
          readinessProbe:
            httpGet: {{path: /health, port: 8000}}
            initialDelaySeconds: 20
            periodSeconds: 10
      volumes:
        - name: tools
          hostPath:
            path: /root/atendagente-tools
            type: Directory
        - name: kubeconfig
          secret:
            secretName: {KUBECONFIG_SECRET_NAME}
---
apiVersion: v1
kind: Service
metadata:
  name: onboarding-service
  namespace: {ONBOARDING_NAMESPACE}
spec:
  selector: {{app: onboarding-service}}
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: onboarding-service
  namespace: {ONBOARDING_NAMESPACE}
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: traefik
  rules:
    - host: {ONBOARDING_HOST}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: onboarding-service
                port:
                  number: 8000
  tls:
    - hosts: [{ONBOARDING_HOST}]
      secretName: onboarding-service-tls
"""


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import os

    try:
        if args.dry_run:
            print("--- Namespace + ServiceAccount + RBAC ---")
            print(build_namespace_and_rbac_manifest())
            print("--- Token Secret (template) ---")
            print(build_token_secret_manifest())
            print("--- Kubeconfig Secret: gerado só em execução real (depende do token) ---")
            print("--- Deployment/Service/Ingress do onboarding-service ---")
            print(build_service_manifest())
            return

        server_ip = os.environ.get("SERVER_IP", "62.238.103.17")
        cf_zone = os.environ.get("CLOUDFLARE_ZONE_ID", "5435cf54669fa51f002f1e2a8b59ae61")

        cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not cf_token:
            token_file = Path(os.environ.get("CLOUDFLARE_TOKEN_FILE", "/root/.cloudflare_api_token"))
            if token_file.exists():
                cf_token = token_file.read_text(encoding="utf-8").strip()
        if not cf_token:
            raise SetupError(
                f"Token da Cloudflare não encontrado — crie {token_file} "
                "(umask 077 && cat > ... , cola o token, Ctrl-D) ou exporte "
                "CLOUDFLARE_API_TOKEN."
            )

        print("1/6 Namespace + ServiceAccount + RBAC...")
        kubectl_apply(build_namespace_and_rbac_manifest())

        print("2/6 Token do ServiceAccount...")
        if not resource_exists("secret", TOKEN_SECRET_NAME, ONBOARDING_NAMESPACE):
            kubectl_apply(build_token_secret_manifest())
        token, ca_crt_b64 = wait_for_token_secret()

        print("3/6 Kubeconfig namespace-scoped (Secret)...")
        kubectl_apply(build_kubeconfig_secret_manifest(token, ca_crt_b64))

        print("4/6 Copiando MONGO_URI compartilhado...")
        copy_mongo_uri_secret()

        print("5/6 Checando Secret de credenciais do onboarding-service...")
        check_env_secret()

        print("6/6 DNS + Deployment/Service/Ingress...")
        create_dns_record(ONBOARDING_HOST, server_ip, cf_token, cf_zone)
        kubectl_apply(build_service_manifest())

        print(f"\n✓ onboarding-service no ar em https://{ONBOARDING_HOST}/signup")
    except (SetupError, ProvisionError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
