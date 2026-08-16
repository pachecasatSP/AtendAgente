#!/usr/bin/env python3
"""Bootstrap único do calendar-mcp — servidor MCP multi-tenant de
agendamento (ver tools/calendar-mcp/server.py e
specs/agendamento-google-calendar.md pro desenho completo).

Roda NO SERVIDOR (root@62.238.103.17), mesmo padrão de
setup_admin_mcp.py. Idempotente. Diferente do admin-mcp: sem
DNS/Ingress — só os pods de tenant (mesmo namespace `atendagente`)
precisam alcançar esse serviço, então é só um ClusterIP interno.

A chave da conta de serviço da Google Cloud (GOOGLE_SERVICE_ACCOUNT_JSON)
precisa existir antes de rodar — não tem como gerar isso por aqui, é
criada manualmente no Google Cloud Console (ver specs/
agendamento-google-calendar.md, seção "Infra necessária").

Uso:
    GOOGLE_SERVICE_ACCOUNT_JSON="$(cat chave.json)" python3 setup_calendar_mcp.py
    python3 setup_calendar_mcp.py --dry-run
"""
import argparse
import json
import subprocess
import sys

NAMESPACE = "atendagente"
ENV_SECRET_NAME = "calendar-mcp-env"


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


def ensure_env_secret(service_account_json: str, mongo_uri: str) -> None:
    manifest = f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {ENV_SECRET_NAME}
  namespace: {NAMESPACE}
type: Opaque
stringData:
  MONGO_URI: "{mongo_uri}"
  GOOGLE_SERVICE_ACCOUNT_JSON: |
{chr(10).join('    ' + line for line in service_account_json.splitlines())}
"""
    kubectl_apply(manifest)


def build_service_manifest() -> str:
    start_cmd = (
        "pip install --quiet --no-cache-dir -r /app/calendar-mcp/requirements.txt && "
        "python /app/calendar-mcp/server.py"
    )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: calendar-mcp
  namespace: {NAMESPACE}
  labels: {{app: calendar-mcp}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: calendar-mcp}}
  template:
    metadata:
      labels: {{app: calendar-mcp}}
    spec:
      containers:
        - name: calendar-mcp
          image: python:3.12-slim
          command: ["sh", "-c", "{start_cmd}"]
          env:
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: {ENV_SECRET_NAME}
            - secretRef:
                name: object-storage-credentials
                optional: true
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
  name: calendar-mcp
  namespace: {NAMESPACE}
spec:
  selector: {{app: calendar-mcp}}
  ports:
    - port: 8000
      targetPort: 8000
"""


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("--- Deployment/Service do calendar-mcp (sem Ingress, só ClusterIP) ---")
        print(build_service_manifest())
        return

    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not service_account_json:
        print("ERRO: defina GOOGLE_SERVICE_ACCOUNT_JSON (conteúdo da chave JSON da conta de serviço).", file=sys.stderr)
        sys.exit(1)
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as e:
        print(f"ERRO: GOOGLE_SERVICE_ACCOUNT_JSON não é um JSON válido: {e}", file=sys.stderr)
        sys.exit(1)

    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        result = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "get", "secret", "mongo-credentials", "-o", "jsonpath={.data.MONGO_URI}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout:
            import base64
            mongo_uri = base64.b64decode(result.stdout).decode()
    if not mongo_uri:
        print("ERRO: MONGO_URI não encontrado (defina a variável ou garanta que o Secret mongo-credentials existe).", file=sys.stderr)
        sys.exit(1)

    try:
        print("1/2 Secret de credenciais (calendar-mcp-env)...")
        ensure_env_secret(service_account_json, mongo_uri)

        print("2/2 Deployment/Service...")
        kubectl_apply(build_service_manifest())

        print("\n✓ calendar-mcp no ar em http://calendar-mcp.atendagente.svc.cluster.local:8000")
        print(f"  Conta de serviço: {info.get('client_email')}")
        print("  (é esse e-mail que cada tenant precisa compartilhar a própria Google Agenda com)")
        print("  Tenants provisionados a partir de agora já registram essa ferramenta sozinhos")
        print("  (ver enable_calendar_mcp em provision_tenant.py); tenants já existentes")
        print("  precisam de uma chamada manual avulsa dessa mesma função.")
    except SetupError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
