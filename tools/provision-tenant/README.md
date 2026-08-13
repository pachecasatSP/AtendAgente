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
mascarados), sem chamar `kubectl` nem a API da Cloudflare — é assim que
foi validado até aqui. **Uma execução real ponta a ponta contra o
cluster ainda não foi feita** (exigiria um segundo número de teste da
Meta, ou reaproveitar o tenant de teste da Fase 1 — decisão de negócio,
não técnica, então ficou pra quando fizer sentido).

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

## Próximo passo (Fase 4)

Este script vira o backend de um formulário de onboarding self-service
(Embedded Signup + formulário de SOUL) — a Fase 4 chama a função
`provision()` daqui via API interna em vez de linha de comando.
