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

1. Criar o Secret de credenciais compartilhadas (App Secret/ID da Meta,
   Config ID do Embedded Signup, OpenRouter, Cloudflare, SERVER_IP) —
   digitado direto no servidor, nunca colado no chat:

   ```bash
   kubectl create namespace atendagente-onboarding
   kubectl -n atendagente-onboarding create secret generic onboarding-service-env \
     --from-literal=WHATSAPP_CLOUD_APP_ID=... \
     --from-literal=WHATSAPP_CLOUD_APP_SECRET=... \
     --from-literal=META_CONFIG_ID=... \
     --from-literal=OPENROUTER_API_KEY=... \
     --from-literal=CLOUDFLARE_API_TOKEN=... \
     --from-literal=CLOUDFLARE_ZONE_ID=... \
     --from-literal=SERVER_IP=62.238.103.17
   ```

2. Rodar o bootstrap (namespace, RBAC, kubeconfig namespace-scoped,
   cópia do Mongo, DNS, Deployment/Service/Ingress):

   ```bash
   export SERVER_IP=62.238.103.17
   export CLOUDFLARE_API_TOKEN=...
   export CLOUDFLARE_ZONE_ID=...
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
