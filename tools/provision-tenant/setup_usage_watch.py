#!/usr/bin/env python3
"""Bootstrap único do CronJob usage_watch — roda 1x/dia, conta contatos
únicos por tenant no mês corrente e compara com o limite do plano (ver
usage_watch.py). Recurso único do cluster, não por tenant — mesmo
padrão de setup_mongo.py.

Roda NO SERVIDOR (root@62.238.103.17). Idempotente.

Uso:
    python3 setup_usage_watch.py
    python3 setup_usage_watch.py --dry-run
    python3 setup_usage_watch.py --run-now   # dispara uma execução imediata pra testar
"""
import argparse
import subprocess
import sys

NAMESPACE = "atendagente"


class SetupError(RuntimeError):
    pass


def kubectl_apply(manifest_yaml: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=manifest_yaml, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise SetupError(f"kubectl apply falhou:\n{result.stderr}")
    print(result.stdout)


def build_manifest() -> str:
    start_cmd = (
        "pip install --quiet --no-cache-dir pymongo && python /app/usage_watch.py"
    )
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: usage-watch
  namespace: {NAMESPACE}
spec:
  schedule: "17 3 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: usage-watch
              image: python:3.12-slim
              command: ["sh", "-c", "{start_cmd}"]
              envFrom:
                - secretRef:
                    name: mongo-credentials
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
                path: /root/atendagente-tools/provision-tenant
                type: Directory
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-now", action="store_true", help="Dispara um Job avulso agora, pra testar sem esperar o horário.")
    args = parser.parse_args()

    manifest = build_manifest()

    if args.dry_run:
        print(manifest)
        return

    try:
        print("Aplicando CronJob usage-watch...")
        kubectl_apply(manifest)
        print(f"\n✓ CronJob criado — roda todo dia às 03:17 UTC.")

        if args.run_now:
            print("Disparando execução imediata (--run-now)...")
            result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "create", "job", "--from=cronjob/usage-watch", "usage-watch-manual"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise SetupError(f"kubectl create job falhou:\n{result.stderr}")
            print(result.stdout)
            print("Acompanhe com: kubectl -n atendagente logs -f job/usage-watch-manual")
    except SetupError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
