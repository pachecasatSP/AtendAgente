#!/usr/bin/env python3
"""Bootstrap único do CronJob agenda-lembrete (Fase 11) — roda a cada 15
minutos, manda a confirmação de agendamento por WhatsApp pra compromissos
dentro da janela configurada por tenant (ver agenda_lembrete_cron.py).
Recurso único do cluster, mesmo padrão de setup_usage_watch.py.

Roda NO SERVIDOR (root@62.238.103.17). Idempotente.

Uso:
    python3 setup_agenda_lembrete_cron.py
    python3 setup_agenda_lembrete_cron.py --dry-run
    python3 setup_agenda_lembrete_cron.py --run-now   # dispara uma execução imediata pra testar
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
        "pip install --quiet --no-cache-dir pymongo && python /app/agenda_lembrete_cron.py"
    )
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: agenda-lembrete
  namespace: {NAMESPACE}
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: agenda-lembrete
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
        print("Aplicando CronJob agenda-lembrete...")
        kubectl_apply(manifest)
        print("\n✓ CronJob criado — roda a cada 15 minutos.")

        if args.run_now:
            print("Disparando execução imediata (--run-now)...")
            result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "create", "job", "--from=cronjob/agenda-lembrete", f"agenda-lembrete-manual-{int(__import__('time').time())}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise SetupError(f"kubectl create job falhou:\n{result.stderr}")
            print(result.stdout)
            print("Acompanhe com: kubectl -n atendagente logs -f job/<nome-do-job-acima>")
    except SetupError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
