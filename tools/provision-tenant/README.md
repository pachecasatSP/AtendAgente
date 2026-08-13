# Provisionamento de tenant (Fase 3 do roadmap multi-tenant)

Colapsa num comando só o que foi feito manualmente na Fase 1: registro
DNS (Cloudflare), Secret + PVC + Deployment + Service + Ingress
(mesmo padrão validado), geração do `SOUL.md` (reaproveita
`tools/soul-generator/`), e validação de `/health`.

Roda **no servidor** (`root@62.238.103.17`), onde `kubectl` já aponta
pro cluster/namespace certo — não foi pensado pra rodar com kubeconfig
remoto.

## Uso

```bash
export WHATSAPP_ACCESS_TOKEN=...
export WHATSAPP_APP_SECRET=...
export OPENROUTER_API_KEY=...
export CLOUDFLARE_API_TOKEN=...
export CLOUDFLARE_ZONE_ID=...      # zone do domínio usado no tenant
export SERVER_IP=62.238.103.17

python3 provision_tenant.py tenants/<tenant>.yaml
```

Credenciais **só por variável de ambiente** — nunca no YAML (que pode
ir pro git) nem em argumento de linha de comando (aparece em `ps aux` e
no histórico do shell).

### Testar sem tocar em nada de verdade

```bash
python3 provision_tenant.py tenants/exemplo-tenant.yaml --dry-run
```

Gera e mostra o SOUL.md e os manifests (Secret com valores sensíveis
mascarados), sem chamar `kubectl` nem a API da Cloudflare.

**Já validado com execução real (não só dry-run)** em 2026-08-13,
reaproveitando o tenant de teste da Fase 1 — ver
`reprovision-teste-atendagente.sh` e a memória `roadmap_multitenant`.

## Schema do YAML do tenant

Combina dois blocos:

- **Infra**: `tenant_id` (slug, vira prefixo dos recursos K8s),
  `dominio` (host completo do subdomínio), `whatsapp.phone_number_id`,
  `whatsapp.waba_id`.
- **SOUL**: todo o schema de `tools/soul-generator/README.md`
  (`nome_negocio`, `descricao_negocio`, `servicos`, `escalacao`, etc.)
  — o mesmo YAML alimenta os dois.

Veja `tenants/exemplo-tenant.yaml`.

## As duas pegadinhas da Fase 1, já resolvidas aqui

O Secret gerado sempre inclui `GATEWAY_ALLOW_ALL_USERS=true` e a chave
do provedor de LLM — as duas coisas que faltaram na primeira tentativa
manual da Fase 1 (ver memória `infra_atendagente_k3s`).

## Pré-requisito: MongoDB compartilhado (Fase 5)

Desde a Fase 5, todo pod de tenant gerado por `build_infra_manifest` traz
um segundo container (`mongo-sync`) que espelha `sessions`/`messages` do
`state.db` do hermes-agent pro MongoDB compartilhado do namespace
`atendagente`. Isso significa que **`setup_mongo.py` precisa rodar pelo
menos uma vez antes de provisionar qualquer tenant** — ele cria o Secret
`mongo-credentials`, o Deployment/Service `mongo`, e o ConfigMap
`mongo-sync-script` que o sidecar consome. Ver `mongo_sync/` pro código
do sidecar e a explicação completa no roadmap (`specs/roadmap-multi-tenant.md`).

```bash
python3 setup_mongo.py            # uma vez, no servidor
python3 setup_mongo.py --dry-run  # só mostra o que seria aplicado
```

Rodar de novo é seguro (idempotente) — não recria credenciais se o
Secret já existir.

## Próximo passo (Fase 4)

Este script vira o backend de um formulário de onboarding self-service
(Embedded Signup + formulário de SOUL) — a Fase 4 chama a função
`provision()` daqui via API interna em vez de linha de comando.
