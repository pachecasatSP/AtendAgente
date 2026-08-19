#!/usr/bin/env python3
"""Bootstrap único do CronJob lixeira-watch (Fase 9) — roda 1x/dia,
apaga de vez a infra dos tenants cancelados há mais de LIXEIRA_DIAS dias
(ver lixeira_watch.py). Recurso único do cluster, mesmo padrão de
setup_usage_watch.py, mas deployado em atendagente-onboarding (não
atendagente) pra reaproveitar o kubeconfig namespace-scoped e o
CLOUDFLARE_API_TOKEN já existentes lá (Secrets onboarding-service-
kubeconfig e onboarding-service-env, ver setup_onboarding_service.py) —
sem duplicar RBAC/credenciais.

Pré-requisito: setup_onboarding_service.py já rodado (cria esses dois
Secrets) — inclusive rodar de novo depois de 2026-08-19 se ainda não
rodou, porque o Role ganhou o verbo "delete" nessa mesma mudança.

Roda NO SERVIDOR (root@62.238.103.17). Idempotente.

Uso:
    python3 setup_lixeira_watch.py
    python3 setup_lixeira_watch.py --dry-run
    python3 setup_lixeira_watch.py --run-now   # dispara uma execução imediata pra testar
"""
import argparse
import subprocess
import sys

NAMESPACE = "atendagente-onboarding"
KUBECONFIG_SECRET_NAME = "onboarding-service-kubeconfig"
ENV_SECRET_NAME = "onboarding-service-env"


class SetupError(RuntimeError):
    pass


def resource_exists(kind: str, name: str, namespace: str) -> bool:
    result = subprocess.run(
        ["kubectl", "-n", namespace, "get", kind, name], capture_output=True, text=True
    )
    return result.returncode == 0


def kubectl_apply(manifest_yaml: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=manifest_yaml, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise SetupError(f"kubectl apply falhou:\n{result.stderr}")
    print(result.stdout)


def build_manifest() -> str:
    kubectl_install = (
        "apt-get update -qq && apt-get install -y -qq curl > /dev/null && "
        "curl -Lo /usr/local/bin/kubectl "
        "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl && "
        "chmod +x /usr/local/bin/kubectl"
    )
    start_cmd = (
        f"{kubectl_install} && "
        # pyyaml é usado por provision_tenant.py (import de módulo
        # inteiro, mesmo lixeira_watch.py só chamando delete_tenant_infra)
        # — sem isso o job crasha no import antes de rodar qualquer coisa.
        "pip install --quiet --no-cache-dir pymongo pyyaml && python /app/provision-tenant/lixeira_watch.py"
    )
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: lixeira-watch
  namespace: {NAMESPACE}
spec:
  schedule: "43 3 * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: lixeira-watch
              image: python:3.12-slim
              command: ["sh", "-c", "{start_cmd}"]
              env:
                - name: KUBECONFIG
                  value: /kubeconfig/kubeconfig
              envFrom:
                - secretRef:
                    name: mongo-credentials
                - secretRef:
                    name: {ENV_SECRET_NAME}
              volumeMounts:
                - name: tools
                  mountPath: /app
                  readOnly: true
                - name: kubeconfig
                  mountPath: /kubeconfig
                  readOnly: true
              resources:
                requests: {{cpu: "50m", memory: "128Mi"}}
                limits: {{cpu: "200m", memory: "256Mi"}}
          volumes:
            - name: tools
              # Monta a raiz de tools/ (não só provision-tenant/) porque
              # provision_tenant.py importa generate_soul de
              # ../soul-generator — mesmo hostPath do onboarding-service
              # (setup_onboarding_service.py), só o comando muda.
              hostPath:
                path: /root/atendagente-tools
                type: Directory
            - name: kubeconfig
              secret:
                secretName: {KUBECONFIG_SECRET_NAME}
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
        for secret_name in (KUBECONFIG_SECRET_NAME, ENV_SECRET_NAME):
            if not resource_exists("secret", secret_name, NAMESPACE):
                raise SetupError(
                    f"Secret '{secret_name}' não existe em {NAMESPACE} — "
                    "rode tools/onboarding-service/setup_onboarding_service.py primeiro."
                )

        print("Aplicando CronJob lixeira-watch...")
        kubectl_apply(manifest)
        print(f"\n✓ CronJob criado — roda todo dia às 03:43 UTC.")

        if args.run_now:
            print("Disparando execução imediata (--run-now)...")
            result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "create", "job", "--from=cronjob/lixeira-watch", "lixeira-watch-manual"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise SetupError(f"kubectl create job falhou:\n{result.stderr}")
            print(result.stdout)
            print(f"Acompanhe com: kubectl -n {NAMESPACE} logs -f job/lixeira-watch-manual")
    except SetupError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
