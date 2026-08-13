# Onboarding self-service (Fase 4)

Serviço web novo (primeira dependência de framework — FastAPI — no
repo) que implementa o fluxo completo: cliente conecta o próprio
WhatsApp via **Embedded Signup** da Meta, preenche o formulário de SOUL
(schema de `tools/soul-generator/`), e sai com o tenant provisionado de
verdade (via `tools/provision-tenant/provision_tenant.py`, importado
diretamente) — sem intervenção manual da Ac Soluções.

Roda em namespace próprio (`atendagente-onboarding`), com um kubeconfig
**namespace-scoped** (restrito só ao namespace `atendagente`, nunca o
kubeconfig admin do cluster — ver `setup_onboarding_service.py`) e sem
imagem Docker/registry: `python:3.12-slim` + `pip install` no startup,
código montado via `hostPath` de `/root/atendagente-tools/` (mesmo
padrão dos sidecars `mongo-sync` e do painel por tenant).

## Passo extra descoberto na Fase 4

A console da Meta não tem UI pra rotear webhook por WABA em contas Tech
Provider. Depois de provisionar o tenant, o serviço chama
`subscribe_app_to_waba()` (em `provision_tenant.py`) — `POST
/{waba_id}/subscribed_apps` — registrando o webhook daquele cliente
específico. Sem isso, o tráfego do cliente cai no webhook padrão do App
em vez de chegar no pod certo.

## Setup (uma vez, no servidor)

1. Criar o Secret de credenciais compartilhadas — via SSH interativo
   direto no servidor, **nunca colado no chat do agente**:

   ```bash
   kubectl create namespace atendagente-onboarding 2>/dev/null

   umask 077 && cat > /root/.onboarding-service-env <<'EOF'
   WHATSAPP_CLOUD_APP_ID=...
   WHATSAPP_CLOUD_APP_SECRET=...
   META_CONFIG_ID=...
   OPENROUTER_API_KEY=...
   CLOUDFLARE_API_TOKEN=...
   CLOUDFLARE_ZONE_ID=5435cf54669fa51f002f1e2a8b59ae61
   SERVER_IP=62.238.103.17
   EOF

   kubectl -n atendagente-onboarding create secret generic onboarding-service-env \
     --from-env-file=/root/.onboarding-service-env
   shred -u /root/.onboarding-service-env
   ```

2. Rodar o bootstrap (namespace, RBAC, kubeconfig namespace-scoped,
   cópia do Mongo, DNS, Deployment/Service/Ingress). O token da
   Cloudflare é lido de `/root/.cloudflare_api_token` (mesmo arquivo já
   usado por `reprovision-teste-atendagente.sh`) — zone ID e SERVER_IP
   já têm default pro cluster atual:

   ```bash
   python3 setup_onboarding_service.py
   python3 setup_onboarding_service.py --dry-run   # só mostra os manifests
   ```

Idempotente — pode rodar de novo sem duplicar recursos nem rotacionar
o token do ServiceAccount à toa.

## Estado do signup

Coleção `signups` no MongoDB compartilhado (mesmo `mongo-credentials`
da Fase 5, copiado pro namespace novo) — `code_exchanged` →
`provisioning` → `live`/`failed`. Não é um datastore de conversas, só
controle do processo de cadastro.

## Fluxo

Ver `specs/roadmap-multi-tenant.md` (Fase 4) pro fluxo completo
Embedded Signup → formulário → provisionamento → webhook override.
