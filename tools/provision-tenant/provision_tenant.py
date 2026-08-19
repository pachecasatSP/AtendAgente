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
import base64
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


def tenant_exists(tenant_id: str) -> bool:
    """Fonte da verdade pra disponibilidade de slug: existe Deployment
    <tenant_id>-hermes no namespace do tenant? (Mongo pode estar
    desatualizado; isso não pode)."""
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "get", "deploy", f"{tenant_id}-hermes"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def set_tenant_enabled(tenant_id: str, enabled: bool) -> None:
    """Liga/desliga o WhatsApp de um tenant já provisionado sem apagar
    nada — pra `/api/admin/tenants/{id}/enabled` (ferramenta de
    pausar/reativar da Duda). Reversível: só troca o valor no Secret e
    reinicia os Deployments. Reinicia hermes E painel — os dois leem
    WHATSAPP_CLOUD_ENABLED do mesmo Secret só na inicialização (ver
    TENANT_ENABLED em tools/tenant-panel/app.py), então sem reiniciar o
    painel ele continuaria mostrando as conversas normalmente com o
    WhatsApp já pausado."""
    prefix = f"{tenant_id}-hermes"
    value_b64 = base64.b64encode(str(enabled).lower().encode()).decode()
    patch = json.dumps([{"op": "replace", "path": "/data/WHATSAPP_CLOUD_ENABLED", "value": value_b64}])
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "patch", "secret", f"{prefix}-env", "--type=json", "-p", patch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"kubectl patch (enabled={enabled}) falhou:\n{result.stderr}")
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}"], check=True)
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}-panel"], check=True)


def set_tenant_phone_number(tenant_id: str, phone_number_id: str) -> None:
    """Troca o WHATSAPP_CLOUD_PHONE_NUMBER_ID de um tenant já configurado
    (Fase 10) — waba_id/access_token continuam os mesmos (mesma WABA, só
    outro número dela). Só reinicia o Hermes: o painel recebe esse valor
    no mesmo Secret mas não usa pra nada funcional (não manda mensagem
    nem decide roteamento), reiniciar ele também só aumentaria o tempo
    de corte à toa. A inscrição do App na WABA é por WABA inteira (ver
    subscribe_app_to_waba), não por número — não precisa mexer em
    webhook nenhum aqui."""
    prefix = f"{tenant_id}-hermes"
    value_b64 = base64.b64encode(phone_number_id.encode()).decode()
    patch = json.dumps([{"op": "replace", "path": "/data/WHATSAPP_CLOUD_PHONE_NUMBER_ID", "value": value_b64}])
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "patch", "secret", f"{prefix}-env", "--type=json", "-p", patch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"kubectl patch (phone_number_id) falhou:\n{result.stderr}")
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}"], check=True)
    if not wait_for_health(prefix):
        raise ProvisionError(f"Pod de '{tenant_id}' não ficou pronto a tempo depois da troca de número.")


def scale_tenant(tenant_id: str, replicas: int) -> None:
    """Sobe/desce o número de réplicas dos dois Deployments de um tenant
    (hermes + painel) — pra Fase 9 (lixeira): `cancelar` escala pra 0 (pod
    para de verdade, custo de compute zerado) e `reativar` escala de volta
    pra 1. Diferente de `set_tenant_enabled`: não mexe no Secret nem no
    flag WHATSAPP_CLOUD_ENABLED, só no número de réplicas — Secret/PVC/
    Ingress/DNS/TLS ficam intactos, é isso que torna a restauração rápida
    (sem esperar novo certificado Let's Encrypt nem propagação de DNS)."""
    prefix = f"{tenant_id}-hermes"
    for deploy in (prefix, f"{prefix}-panel"):
        result = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "scale", f"deploy/{deploy}", f"--replicas={replicas}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise ProvisionError(f"kubectl scale {deploy} --replicas={replicas} falhou:\n{result.stderr}")


def delete_dns_record(subdomain_host: str, token: str, zone_id: str) -> None:
    """Contraparte de `create_dns_record` — usada na exclusão definitiva
    (Fase 9, passados os 10 dias de lixeira sem restauração). Busca o
    registro pelo nome (a API não aceita delete por nome direto, só por
    id) e apaga; se não existir mais (já removido manualmente, ex:
    `use-bisteco` em 2026-08-19), não é erro — só segue."""
    zone_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    req = urllib.request.Request(
        f"{zone_url}?name={subdomain_host}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ProvisionError(f"Cloudflare API HTTP {e.code} ao buscar {subdomain_host}: {e.read().decode()}") from e

    records = body.get("result") or []
    if not records:
        print(f"  (registro DNS de {subdomain_host} já não existia, seguindo)")
        return

    for record in records:
        del_req = urllib.request.Request(
            f"{zone_url}/{record['id']}", method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(del_req) as resp:
                json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ProvisionError(f"Cloudflare API HTTP {e.code} ao apagar {subdomain_host}: {e.read().decode()}") from e
    print(f"  DNS removido: {subdomain_host}")


def delete_tenant_infra(tenant_id: str, host: str, cloudflare_token: str, cloudflare_zone: str) -> None:
    """Exclusão definitiva de um tenant cancelado (Fase 9, disparada pelo
    CronJob `lixeira_watch.py` passados os 10 dias sem pedido de
    restauração) — remove o registro DNS na Cloudflare e todos os
    recursos K8s do tenant (Deployment/Service/Ingress/Secret/PVC, hermes
    + painel). Não apaga o doc de `signups` no Mongo nem `panel_auth` —
    isso é responsabilidade de quem chama (mantém o registro pra
    auditoria/cobrança, ver `store.mark_deleted`). Irreversível: depois
    disso só reprovisionando do zero."""
    prefix = f"{tenant_id}-hermes"
    delete_dns_record(host, cloudflare_token, cloudflare_zone)
    for kind, names in (
        ("ingress", [prefix]),
        ("deploy", [prefix, f"{prefix}-panel"]),
        ("svc", [prefix, f"{prefix}-panel"]),
        ("secret", [f"{prefix}-env", f"{prefix}-tls"]),
        ("pvc", [f"{prefix}-data"]),
    ):
        for name in names:
            result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "delete", kind, name, "--ignore-not-found"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise ProvisionError(f"kubectl delete {kind}/{name} falhou:\n{result.stderr}")
    print(f"  Infra K8s de '{tenant_id}' removida.")


def apply_display_defaults(prefix: str) -> None:
    """`display.memory_notifications` (Hermes default: "on") faz o
    processo de revisão em segundo plano do agente imprimir "💾 Memory
    updated" no meio da conversa — inofensivo pra uso interno (Duda),
    mas um vazamento de mecanismo interno pro cliente final de um tenant
    (WhatsApp de atendimento). provision_tenant.py nunca escrevia
    config.yaml nenhum, então todo tenant novo herdava esse "on" — desligado
    aqui, na mesma janela de restart do passo 5/5, pra não custar um
    segundo rollout."""
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", f"deploy/{prefix}", "--",
         "hermes", "config", "set", "display.memory_notifications", "off"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"hermes config set (display.memory_notifications) falhou:\n{result.stderr}")

    # Sem isso, o Hermes pede o max_tokens de saída máximo do modelo por
    # padrão (128000 pro Claude Opus) — a API cobra pré-autorização por
    # esse teto, não pelo uso real, e derruba a requisição com HTTP 402
    # assim que o saldo compartilhado da OPENROUTER_API_KEY (mesma chave
    # pra todos os tenants) fica pequeno, mesmo a resposta real custando
    # centavos. 8000 tokens é de sobra pra resposta de WhatsApp.
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", f"deploy/{prefix}", "--",
         "hermes", "config", "set", "model.max_tokens", "8000"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"hermes config set (model.max_tokens) falhou:\n{result.stderr}")

    # Sem isso, o tenant herda o default embutido na imagem
    # nousresearch/hermes-agent:latest — que não é fixo (tag :latest) e
    # em algum momento passou a ser um modelo pago da Anthropic
    # (anthropic/claude-opus-4.6) em vez do modelo pretendido, sem
    # provision_tenant.py nunca ter travado isso explicitamente.
    # Descoberto 2026-08-19 comparando os 3 tenants live, todos rodando
    # Opus pago silenciosamente há tempo indeterminado.
    #
    # Duas tentativas de SKU :free no mesmo dia não seguraram:
    # meta-llama/llama-3.3-70b-instruct:free foi descontinuado pela
    # OpenRouter (HTTP 404, "unavailable for free") e o substituto
    # openai/gpt-oss-20b:free saiu ok tecnicamente mas deu qualidade de
    # resposta ruim (além de rate-limit ocasional no pool compartilhado).
    # Decisão final: mesmo modelo pago já usado pela Duda,
    # openai/gpt-5.6-luna (ver infra_admin_mcp — trocado nela por causa
    # de tool-calling não confiável em modelo :free) — custo real mas
    # pequeno (centavos por conversa) e qualidade/confiabilidade
    # consistente, o que passou a valer mais que economizar no modelo
    # depois de perder tempo com dois SKUs grátis quebrados. **Nunca usar
    # `gpt-5-luna` (sem o `.6`) — não existe, já derrubou produção antes.**
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", f"deploy/{prefix}", "--",
         "hermes", "config", "set", "model.default", "openai/gpt-5.6-luna"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProvisionError(f"hermes config set (model.default) falhou:\n{result.stderr}")


def publish_soul(tenant_id: str, config: dict) -> None:
    """Regenera o SOUL.md a partir de `config` e publica no pod do
    tenant já provisionado — mesmo passo 5/5 do `provision()`, extraído
    pra reuso pela fila de alterações de comportamento vindas do painel
    do cliente (ver /api/admin/soul-pending no onboarding-service)."""
    prefix = f"{tenant_id}-hermes"
    soul_text = render_soul(config)
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat > /opt/data/SOUL.md"], soul_text)
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}"], check=True)
    wait_for_health(prefix)


def publish_catalog(tenant_id: str, csv_text: str) -> None:
    """Publica o CSV do catálogo (derivado de `catalogo_itens` no Mongo,
    ver onboarding-service/app/store.py) no pod do tenant já
    provisionado. Sem `rollout restart` — diferente do SOUL.md, a
    ferramenta `read_file` do Hermes lê /opt/data/catalogo.csv ao vivo a
    cada chamada, não só na inicialização do processo."""
    prefix = f"{tenant_id}-hermes"
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat > /opt/data/catalogo.csv"], csv_text)


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


def subscribe_app_to_waba(waba_id: str, access_token: str, verify_token: str, callback_uri: str) -> None:
    """Registra o webhook do tenant pra essa WABA específica (Fase 4).

    A console da Meta não tem UI pra isso em contas Tech Provider — sem
    essa chamada, o tráfego da WABA do cliente cai no webhook padrão do
    App em vez de chegar no pod do tenant. Fica fora de provision() de
    propósito: os fluxos manuais (reprovision-teste-atendagente.sh) não
    têm um access_token de WABA recém-obtido via Embedded Signup à mão.
    """
    url = f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps"
    payload = json.dumps(
        {"override_callback_uri": callback_uri, "verify_token": verify_token}
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ProvisionError(
            f"Falha ao registrar webhook override na WABA {waba_id}: "
            f"HTTP {e.code}: {e.read().decode()}"
        ) from e

    if not body.get("success"):
        raise ProvisionError(f"Meta rejeitou o override de webhook: {body}")
    print(f"  Webhook override registrado pra WABA {waba_id} -> {callback_uri}")


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
            - name: whatsapp-cloud-patch
              mountPath: /opt/hermes/gateway/platforms/whatsapp_cloud.py
              subPath: whatsapp_cloud.py
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
        - name: mongo-sync
          image: python:3.12-slim
          command: ["sh", "-c", "pip install --quiet --no-cache-dir pymongo && python /scripts/sync_conversations.py"]
          env:
            - name: TENANT_ID
              value: "{tenant_id}"
            - name: SYNC_INTERVAL_SECONDS
              value: "15"
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: mongo-credentials
          volumeMounts:
            - name: data
              mountPath: /opt/data
              readOnly: true
            - name: sync-script
              mountPath: /scripts
          resources:
            requests: {{cpu: "50m", memory: "128Mi"}}
            limits: {{cpu: "200m", memory: "256Mi"}}
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: {prefix}-data
        - name: sync-script
          configMap:
            name: mongo-sync-script
        - name: whatsapp-cloud-patch
          configMap:
            name: whatsapp-cloud-patch
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
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {prefix}-panel
  namespace: {NAMESPACE}
  labels: {{app: {prefix}-panel, tenant: {tenant_id}}}
spec:
  replicas: 1
  selector:
    matchLabels: {{app: {prefix}-panel}}
  template:
    metadata:
      labels: {{app: {prefix}-panel, tenant: {tenant_id}}}
    spec:
      containers:
        - name: panel
          image: python:3.12-slim
          command: ["sh", "-c", "pip install --quiet --no-cache-dir -r /app/requirements.txt && python -m uvicorn app:app --host 0.0.0.0 --port 8000 --app-dir /app"]
          env:
            - name: TENANT_ID
              value: "{tenant_id}"
            - name: PYTHONUNBUFFERED
              value: "1"
          envFrom:
            - secretRef:
                name: mongo-credentials
            - secretRef:
                name: {prefix}-env
            - secretRef:
                name: object-storage-credentials
                optional: true
          volumeMounts:
            - name: panel-code
              mountPath: /app
              readOnly: true
          resources:
            requests: {{cpu: "50m", memory: "128Mi"}}
            limits: {{cpu: "200m", memory: "256Mi"}}
          readinessProbe:
            httpGet: {{path: /health, port: 8000}}
            initialDelaySeconds: 5
            periodSeconds: 3
            failureThreshold: 30
      volumes:
        - name: panel-code
          hostPath:
            path: /root/atendagente-tools/tenant-panel
            type: Directory
---
apiVersion: v1
kind: Service
metadata:
  name: {prefix}-panel
  namespace: {NAMESPACE}
spec:
  selector: {{app: {prefix}-panel}}
  ports:
    - name: painel
      port: 8000
      targetPort: 8000
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
          - path: /painel
            pathType: Prefix
            backend:
              service:
                name: {prefix}-panel
                port:
                  number: 8000
          - path: /vitrine
            pathType: Prefix
            backend:
              service:
                name: {prefix}-panel
                port:
                  number: 8000
          - path: /configuracoes
            pathType: Prefix
            backend:
              service:
                name: {prefix}-panel
                port:
                  number: 8000
          - path: /agenda
            pathType: Prefix
            backend:
              service:
                name: {prefix}-panel
                port:
                  number: 8000
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


def normalize_br_phone(raw: str) -> str:
    """Telefone digitado tipo '(11) 90000-0000' vira '5511900000000' —
    formato bare E.164 (sem +) que o WHATSAPP_CLOUD_HOME_CHANNEL espera
    como chat_id. Assume DDD+número brasileiro se não vier com o 55 já."""
    digits = "".join(c for c in raw if c.isdigit())
    if digits.startswith("55") and len(digits) >= 12:
        return digits
    return f"55{digits}"


def build_secret_manifest(
    tenant_id: str, phone_number_id: str, waba_id: str, env: dict, escalacao: dict,
    calendar_mcp_token: str = "",
) -> tuple[str, dict]:
    prefix = f"{tenant_id}-hermes"
    verify_token = secrets.token_urlsafe(24)
    # PIN de verificação em duas etapas exigido pela Cloud API pra
    # registrar o número (`POST /{id}/register`, ver meta_client.
    # register_phone_number) — gerado aqui e guardado no Secret pro caso
    # de precisar re-registrar o número depois (troca de app, etc.).
    two_step_pin = f"{secrets.randbelow(1_000_000):06d}"
    home_channel = normalize_br_phone(escalacao["telefone"])
    home_channel_name = escalacao["nome"]
    # O cliente cadastra o próprio usuário/senha do painel na primeira
    # visita (ver tenant-panel/app.py) — a gente só gera um token de
    # configuração de uso único, nunca uma senha pronta.
    panel_setup_token = secrets.token_urlsafe(24)
    panel_session_secret = secrets.token_urlsafe(32)
    # Idealmente já vem em config["calendar_mcp_token"] (gerado no
    # submit_form do onboarding-service e salvo em signups.config —
    # calendar-mcp busca o tenant por esse valor no Mongo). Gerar aqui
    # só cobre provisionamento manual via CLI direto (YAML sem esse
    # campo) — nesse caso o calendar-mcp não vai achar o tenant até
    # alguém também gravar o mesmo valor em signups.config à mão.
    calendar_mcp_token = calendar_mcp_token or secrets.token_urlsafe(24)
    manifest = f"""\
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
  WHATSAPP_CLOUD_HOME_CHANNEL: "{home_channel}"
  WHATSAPP_CLOUD_HOME_CHANNEL_NAME: "{home_channel_name}"
  PANEL_SETUP_TOKEN: "{panel_setup_token}"
  PANEL_SESSION_SECRET: "{panel_session_secret}"
  CALENDAR_MCP_TOKEN: "{calendar_mcp_token}"
  WHATSAPP_CLOUD_TWO_STEP_PIN: "{two_step_pin}"
"""
    credentials = {
        "verify_token": verify_token,
        "panel_setup_token": panel_setup_token,
        "calendar_mcp_token": calendar_mcp_token,
        "two_step_pin": two_step_pin,
    }
    return manifest, credentials


def enable_calendar_mcp(tenant_id: str, calendar_mcp_token: str) -> None:
    """Registra a ferramenta de agendamento (calendar-mcp, ver
    tools/calendar-mcp/) no Hermes do tenant — escrito direto em
    config.yaml/.env em vez de `hermes mcp add` (interativo, espera
    respostas por prompt, frágil sem TTY numa automação). Formato final
    idêntico ao que o comando interativo produziria. A ferramenta só
    "funciona de verdade" depois que o tenant configurar
    google_calendar_email em Configurações → Agenda; registrar aqui
    evita mais um passo de provisionamento manual depois. Não reinicia
    o pod — quem chama decide quando (normalmente já tem um restart
    programado no mesmo passo)."""
    prefix = f"{tenant_id}-hermes"
    mcp_block = (
        "\nmcp_servers:\n"
        "  agenda:\n"
        "    url: http://calendar-mcp.atendagente.svc.cluster.local:8000/mcp\n"
        "    headers:\n"
        "      Authorization: Bearer ${MCP_AGENDA_API_KEY}\n"
        "    enabled: true\n"
    )
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat >> /opt/data/config.yaml"], mcp_block)
    env_line = f'\nMCP_AGENDA_API_KEY="{calendar_mcp_token}"\n'
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat >> /opt/data/.env"], env_line)


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


def provision(tenant_config_path: Path, env: dict, dry_run: bool = False) -> dict | None:
    config = yaml.safe_load(tenant_config_path.read_text(encoding="utf-8"))

    tenant_id = config["tenant_id"]
    host = config["dominio"]
    phone_number_id = config["whatsapp"]["phone_number_id"]
    waba_id = config["whatsapp"]["waba_id"]
    prefix = f"{tenant_id}-hermes"

    print(f"== Provisionando tenant '{tenant_id}' ({host}) {'[DRY RUN]' if dry_run else ''} ==")

    secret_yaml, credentials = build_secret_manifest(
        tenant_id, phone_number_id, waba_id, env, config["escalacao"],
        calendar_mcp_token=config.get("calendar_mcp_token", ""),
    )
    infra_yaml = build_infra_manifest(tenant_id, host)
    soul_text = render_soul(config)

    if dry_run:
        print("--- Secret (valores sensíveis mascarados) ---")
        for line in secret_yaml.splitlines():
            if any(k in line for k in ("ACCESS_TOKEN", "APP_SECRET", "OPENROUTER_API_KEY", "VERIFY_TOKEN", "PANEL_SETUP_TOKEN", "PANEL_SESSION_SECRET", "CALENDAR_MCP_TOKEN", "TWO_STEP_PIN")):
                key = line.split(":", 1)[0]
                print(f"{key}: <mascarado>")
            else:
                print(line)
        print("--- Manifests de infra (PVC/Deployment/Service/Ingress + painel) ---")
        print(infra_yaml)
        print("--- SOUL.md gerado ---")
        print(soul_text)
        print(f"(dry-run: nenhuma chamada real a kubectl/Cloudflare foi feita; "
              f"registro DNS seria {host} -> {env.get('SERVER_IP') or '<SERVER_IP não setado>'})")
        return None

    print("1/5 Registro DNS...")
    create_dns_record(host, env["SERVER_IP"], env["CLOUDFLARE_API_TOKEN"], env["CLOUDFLARE_ZONE_ID"])

    print("2/5 Secret (credenciais)...")
    kubectl_apply(secret_yaml)

    print("3/5 PVC + Deployment + Service + Ingress + painel...")
    kubectl_apply(infra_yaml)

    print("4/5 Aguardando pod ficar pronto...")
    if not wait_for_health(prefix):
        raise ProvisionError(
            f"Pod não ficou pronto a tempo — inspecionar com "
            f"'kubectl -n {NAMESPACE} logs -l app={prefix}' e os arquivos em "
            f"/opt/data/logs/ dentro do pod (kubectl logs pode atrasar, ver "
            f"memória infra_atendagente_k3s)."
        )
    wait_for_health(f"{prefix}-panel")

    print("5/5 Gerando e publicando SOUL.md...")
    kubectl_exec_stdin(prefix, ["sh", "-c", "cat > /opt/data/SOUL.md"], soul_text)
    apply_display_defaults(prefix)
    enable_calendar_mcp(tenant_id, credentials["calendar_mcp_token"])
    subprocess.run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deploy/{prefix}"], check=True)
    wait_for_health(prefix)

    print(f"\n✓ Tenant '{tenant_id}' no ar em https://{host}/whatsapp/webhook")
    print(f"  Verify token pro cadastro do webhook na Meta: {credentials['verify_token']}")
    print(f"  Link de configuração do painel (uso único): https://{host}/painel/setup?token={credentials['panel_setup_token']}")
    print(
        "  (o token não fica salvo em lugar nenhum além do Secret do "
        "cluster — anote agora.)"
    )
    return credentials


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
