#!/usr/bin/env python3
"""Provisiona o MongoDB compartilhado do namespace atendagente (Fase 5 do
roadmap multi-tenant) — recurso único do cluster, roda UMA VEZ, nunca por
tenant (diferente de provision_tenant.py).

Cria: PVC (mongo-data), Secret (mongo-credentials, com MONGO_URI pronto),
Deployment (mongo, imagem oficial mongo:7), Service (ClusterIP, sem
Ingress), e o ConfigMap com o script do sidecar de sincronização
(mongo_sync/sync_conversations.py) que provision_tenant.py monta em cada
tenant.

Idempotente: se o Secret mongo-credentials já existe, reaproveita as
credenciais em vez de gerar novas (evita quebrar sidecars já rodando com
a senha antiga).

Uso (no servidor, onde kubectl já aponta pro cluster certo):
    python3 setup_mongo.py
    python3 setup_mongo.py --dry-run   # só mostra o que seria aplicado
"""
import argparse
import secrets
import subprocess
import sys
from pathlib import Path

NAMESPACE = "atendagente"


class SetupError(RuntimeError):
    pass


def kubectl_apply(manifest_yaml: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest_yaml,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SetupError(f"kubectl apply falhou:\n{result.stderr}")
    print(result.stdout)


def secret_exists(name: str) -> bool:
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "get", "secret", name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def build_secret_manifest() -> tuple[str, bool]:
    """Retorna (manifest_yaml, reused). Se o Secret já existe, não gera
    credenciais novas — só confirma que ele está lá (kubectl apply em cima
    de um Secret existente com stringData diferente reescreveria a senha,
    então nesse caso simplesmente pulamos a aplicação)."""
    if secret_exists("mongo-credentials"):
        return "", True

    user = "atendagente"
    password = secrets.token_urlsafe(24)
    uri = f"mongodb://{user}:{password}@mongo.{NAMESPACE}.svc.cluster.local:27017/atendagente?authSource=admin"
    manifest = f"""\
apiVersion: v1
kind: Secret
metadata:
  name: mongo-credentials
  namespace: {NAMESPACE}
type: Opaque
stringData:
  MONGO_ROOT_USER: "{user}"
  MONGO_ROOT_PASSWORD: "{password}"
  MONGO_URI: "{uri}"
"""
    return manifest, False


def build_infra_manifest() -> str:
    return f"""\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mongo-data
  namespace: {NAMESPACE}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources:
    requests:
      storage: 5Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mongo
  namespace: {NAMESPACE}
  labels: {{app: mongo}}
spec:
  replicas: 1
  strategy: {{type: Recreate}}
  selector:
    matchLabels: {{app: mongo}}
  template:
    metadata:
      labels: {{app: mongo}}
    spec:
      containers:
        - name: mongo
          image: mongo:7
          ports:
            - containerPort: 27017
          env:
            - name: MONGO_INITDB_ROOT_USERNAME
              valueFrom: {{secretKeyRef: {{name: mongo-credentials, key: MONGO_ROOT_USER}}}}
            - name: MONGO_INITDB_ROOT_PASSWORD
              valueFrom: {{secretKeyRef: {{name: mongo-credentials, key: MONGO_ROOT_PASSWORD}}}}
            - name: MONGO_INITDB_DATABASE
              value: atendagente
          volumeMounts:
            - name: data
              mountPath: /data/db
          resources:
            requests: {{cpu: "250m", memory: "256Mi"}}
            limits: {{cpu: "500m", memory: "512Mi"}}
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: mongo-data
---
apiVersion: v1
kind: Service
metadata:
  name: mongo
  namespace: {NAMESPACE}
spec:
  selector: {{app: mongo}}
  ports:
    - port: 27017
      targetPort: 27017
"""


def build_configmap_manifest(sync_script_path: Path) -> str:
    script_content = sync_script_path.read_text(encoding="utf-8")
    # indenta o conteúdo do script pra caber num bloco literal YAML (|)
    indented = "\n".join(
        "    " + line if line else "" for line in script_content.splitlines()
    )
    return f"""\
apiVersion: v1
kind: ConfigMap
metadata:
  name: mongo-sync-script
  namespace: {NAMESPACE}
data:
  sync_conversations.py: |
{indented}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra os manifests sem chamar kubectl de verdade.",
    )
    args = parser.parse_args()

    sync_script_path = Path(__file__).parent / "mongo_sync" / "sync_conversations.py"
    if not sync_script_path.exists():
        print(f"ERRO: não encontrei {sync_script_path}", file=sys.stderr)
        sys.exit(1)

    secret_yaml, reused = build_secret_manifest()
    infra_yaml = build_infra_manifest()
    configmap_yaml = build_configmap_manifest(sync_script_path)

    if args.dry_run:
        print("--- Secret (mongo-credentials) ---")
        if reused:
            print("(já existe — não seria recriado)")
        else:
            print("(seria criado; valores mascarados)")
            for line in secret_yaml.splitlines():
                if any(k in line for k in ("MONGO_ROOT_PASSWORD", "MONGO_URI")):
                    key = line.split(":", 1)[0]
                    print(f"{key}: <mascarado>")
                else:
                    print(line)
        print("--- PVC + Deployment + Service (mongo) ---")
        print(infra_yaml)
        print("--- ConfigMap (mongo-sync-script) ---")
        print(f"(script de {sync_script_path}, {len(configmap_yaml)} bytes de manifest)")
        return

    print("1/3 Secret (credenciais)...")
    if reused:
        print("  (mongo-credentials já existe, reaproveitando)")
    else:
        kubectl_apply(secret_yaml)

    print("2/3 PVC + Deployment + Service...")
    kubectl_apply(infra_yaml)

    print("3/3 ConfigMap (script de sincronização)...")
    kubectl_apply(configmap_yaml)

    print("\n✓ MongoDB compartilhado provisionado no namespace atendagente.")
    print("  Rode 'kubectl -n atendagente rollout status deploy/mongo' pra acompanhar.")


if __name__ == "__main__":
    main()
