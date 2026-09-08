#!/usr/bin/env python3
"""Bootstrap único do backoffice comercial (Frente 2) — namespace
`hermes` (mesma máquina da Duda, 2.28.15.6, NÃO o namespace
`atendagente` do onboarding-service — ver "Topologia de deploy" no
plano e specs/comissao-vendedores.md). Sobe um Mongo PRÓPRIO (não o
compartilhado do atendagente, inalcançável daqui — confirmado por SSH:
não existe rede privada Hetzner entre os dois servidores) + o Deployment/
Service/Ingress do backoffice em si.

Diferente do onboarding-service, este serviço NÃO fala com kubectl (não
provisiona tenant, só CRM/comissão) — não precisa de ServiceAccount/RBAC/
kubeconfig secret, só Mongo próprio + credenciais pra chamar a API HTTP
pública do onboarding-service (ONBOARDING_ADMIN_KEY).

Roda NO SERVIDOR (root@2.28.15.6) com o kubeconfig admin
(/etc/rancher/k3s/k3s.yaml) só nesse momento de bootstrap — mesmo padrão
de setup_onboarding_service.py. Idempotente.

Pré-requisito: o Secret `backoffice-env` (BACKOFFICE_SESSION_SECRET,
ONBOARDING_ADMIN_KEY — a mesma chave que o onboarding-service já usa
pro admin-mcp, ver ONBOARDING_ADMIN_KEY em onboarding-service-env nesse
outro servidor —, ADMIN_PIN) precisa existir ANTES de rodar isso,
criado manualmente pelo operador via kubectl interativo — nunca colado
no chat do agente. Este script imprime o comando exato se o Secret não
existir.

Uso:
    python3 setup_backoffice.py
    python3 setup_backoffice.py --dry-run
"""
import argparse
import subprocess
import sys

NAMESPACE = "hermes"
ENV_SECRET_NAME = "backoffice-env"
MONGO_SECRET_NAME = "backoffice-mongo-uri"
BACKOFFICE_HOST = "backoffice.atendpragente.com.br"
# Diretório no servidor onde o conteúdo de tools/comercial-backoffice/
# precisa estar sincronizado (git pull / rsync) ANTES de rodar este
# script — mesmo padrão hostPath do onboarding-service
# (/root/atendagente-tools lá, /root/comercial-backoffice aqui).
CODE_HOSTPATH = "/root/comercial-backoffice"

REQUIRED_ENV_SECRET_KEYS = [
    "BACKOFFICE_SESSION_SECRET",
    "ONBOARDING_ADMIN_KEY",
    "ADMIN_PIN",
    "GOOGLE_PLACES_API_KEY",
]

# Orçamento diário da prospecção automática (app/prospeccao.py) — 50
# chamadas/dia fica bem dentro da cota grátis do Google Places
# (5.000/mês no tier Text Search Pro), custo zero de infra comercial.
# O botão "buscar mais" no /prospeccao soma chamadas extras ao mesmo
# dia sob decisão humana (PIN), fora desse orçamento fixo.
PROSPECCAO_CHAMADAS_POR_DIA = 50


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


def check_env_secret() -> None:
    if resource_exists("secret", ENV_SECRET_NAME, NAMESPACE):
        return
    example_lines = "\n".join(f"{k}=..." for k in REQUIRED_ENV_SECRET_KEYS)
    raise SetupError(
        f"Secret '{ENV_SECRET_NAME}' não existe em {NAMESPACE}. "
        "Crie primeiro, digitando os valores direto no servidor via SSH "
        "interativo (nunca colado no chat do agente):\n\n"
        f"  kubectl create namespace {NAMESPACE} 2>/dev/null\n"
        "  umask 077 && cat > /root/.backoffice-env <<'EOF'\n"
        f"{example_lines}\n"
        "EOF\n"
        f"  kubectl -n {NAMESPACE} create secret generic {ENV_SECRET_NAME} "
        "--from-env-file=/root/.backoffice-env\n"
        "  shred -u /root/.backoffice-env\n\n"
        "Rode este script de novo depois."
    )


def build_mongo_manifest() -> str:
    """Mongo próprio, single-pod, sem auth (só ClusterIP interno ao
    namespace `hermes` — mesmo nível de exposição que o Mongo do
    atendagente tem hoje dentro daquele cluster)."""
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mongo-backoffice
  namespace: {NAMESPACE}
  labels: {{app: mongo-backoffice}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: mongo-backoffice}}
  template:
    metadata:
      labels: {{app: mongo-backoffice}}
    spec:
      containers:
        - name: mongo
          image: mongo:7
          ports: [{{containerPort: 27017}}]
          volumeMounts:
            - name: data
              mountPath: /data/db
          resources:
            requests: {{cpu: "50m", memory: "256Mi"}}
            limits: {{cpu: "300m", memory: "512Mi"}}
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: mongo-backoffice-data
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mongo-backoffice-data
  namespace: {NAMESPACE}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests: {{storage: 2Gi}}
---
apiVersion: v1
kind: Service
metadata:
  name: mongo-backoffice
  namespace: {NAMESPACE}
spec:
  selector: {{app: mongo-backoffice}}
  ports:
    - port: 27017
      targetPort: 27017
"""


def build_mongo_uri_secret_manifest() -> str:
    import base64

    uri = f"mongodb://mongo-backoffice.{NAMESPACE}.svc.cluster.local:27017/backoffice"
    uri_b64 = base64.b64encode(uri.encode()).decode()
    return f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {MONGO_SECRET_NAME}
  namespace: {NAMESPACE}
type: Opaque
data:
  MONGO_URI: {uri_b64}
"""


def build_service_manifest() -> str:
    start_cmd = (
        "pip install --quiet --no-cache-dir -r /app/requirements.txt && "
        "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir /app "
        "--proxy-headers --forwarded-allow-ips='*'"
    )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backoffice
  namespace: {NAMESPACE}
  labels: {{app: backoffice}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: backoffice}}
  template:
    metadata:
      labels: {{app: backoffice}}
    spec:
      containers:
        - name: backoffice
          image: python:3.12-slim
          command: ["sh", "-c", "{start_cmd}"]
          env:
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: {ENV_SECRET_NAME}
            - secretRef:
                name: {MONGO_SECRET_NAME}
          volumeMounts:
            - name: code
              mountPath: /app
              readOnly: true
          resources:
            requests: {{cpu: "100m", memory: "256Mi"}}
            limits: {{cpu: "500m", memory: "512Mi"}}
          readinessProbe:
            httpGet: {{path: /health, port: 8000}}
            initialDelaySeconds: 15
            periodSeconds: 10
      volumes:
        - name: code
          hostPath:
            path: {CODE_HOSTPATH}
            type: Directory
---
apiVersion: v1
kind: Service
metadata:
  name: backoffice
  namespace: {NAMESPACE}
spec:
  selector: {{app: backoffice}}
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: backoffice
  namespace: {NAMESPACE}
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: traefik
  rules:
    - host: {BACKOFFICE_HOST}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: backoffice
                port:
                  number: 8000
  tls:
    - hosts: [{BACKOFFICE_HOST}]
      secretName: backoffice-tls
"""


def build_prospeccao_cronjob_manifest() -> str:
    """Roda 1x/dia, mesmo hostPath/imagem/env do Deployment principal —
    não é um serviço separado, só um lote em batch de
    app/prospeccao.py::buscar_lote (ver docstring lá)."""
    cmd = (
        "pip install --quiet --no-cache-dir -r /app/requirements.txt && "
        f"python -c 'from app.prospeccao import buscar_lote; print(buscar_lote({PROSPECCAO_CHAMADAS_POR_DIA}))'"
    )
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: backoffice-prospeccao
  namespace: {NAMESPACE}
spec:
  schedule: "0 9 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 0
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: prospeccao
              image: python:3.12-slim
              command: ["sh", "-c", "{cmd}"]
              envFrom:
                - secretRef:
                    name: {ENV_SECRET_NAME}
                - secretRef:
                    name: {MONGO_SECRET_NAME}
              volumeMounts:
                - name: code
                  mountPath: /app
                  readOnly: true
              resources:
                requests: {{cpu: "100m", memory: "256Mi"}}
                limits: {{cpu: "500m", memory: "512Mi"}}
          volumes:
            - name: code
              hostPath:
                path: {CODE_HOSTPATH}
                type: Directory
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

    try:
        print(f"--- Código precisa estar sincronizado em {CODE_HOSTPATH} antes de aplicar ---")
        check_env_secret()

        manifests = [
            ("Mongo próprio (Deployment + PVC + Service)", build_mongo_manifest()),
            ("Secret MONGO_URI (aponta pro Mongo acima)", build_mongo_uri_secret_manifest()),
            ("Backoffice (Deployment + Service + Ingress)", build_service_manifest()),
            ("Prospecção automática (CronJob diário)", build_prospeccao_cronjob_manifest()),
        ]
        for label, manifest in manifests:
            print(f"--- {label} ---")
            print(manifest if args.dry_run else "(aplicando...)")
            if not args.dry_run:
                kubectl_apply(manifest)

        if args.dry_run:
            print("\n--dry-run: nada foi aplicado.")
        else:
            print(f"\nOK. Confira com: kubectl -n {NAMESPACE} get pods,ingress")
            print(f"DNS: aponte {BACKOFFICE_HOST} (A) pro IP 2.28.15.6 na Cloudflare, se ainda não estiver.")
    except SetupError as exc:
        print(f"\nERRO: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
