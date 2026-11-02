# Backoffice comercial

CRM de leads + apuração de comissão pra vendedores/parceiros externos
do AtendPraGente. Regras de negócio completas em
`specs/comissao-vendedores.md` — este README só cobre o que é
específico de rodar o código.

## Onde roda

Cluster AWS do AtendPraGente, namespace `atendagente-admin`
(`backoffice.atendpragente.com.br`), imagem
`ghcr.io/pachecasatsp/atendpragente-backoffice:<commit>` com **Mongo
próprio** (`mongo-backoffice`, não o dos tenants). O manifesto vigente
é `infra-aws/k8s/atendpragente/backoffice.yaml`; ver
`specs/infraestrutura-aws-atendpragente.md`.

Tudo que é de tenant (listar, atribuir vendedor, ciclo de vida) passa
pela API HTTP do onboarding-service (`app/onboarding_client.py`,
`ONBOARDING_BASE_URL`, padrão: host público). O backoffice nunca fala
direto com o Mongo dos tenants.

> `setup_backoffice.py` é **legado** (deploy na Hetzner, namespace
> `hermes`, código por `hostPath`). Não use no cluster novo.

## Variáveis de ambiente

| Variável | Obrigatória | Para quê |
|---|---|---|
| `MONGO_URI` | sim | Mongo próprio (com nome do banco) |
| `BACKOFFICE_SESSION_SECRET` | sim | assina o cookie de sessão |
| `ONBOARDING_ADMIN_KEY` | sim | chave server-a-server do onboarding-service |
| `ADMIN_PIN` | p/ ações sensíveis | PIN de pausar tenant, desativar vendedor, busca manual |
| `GOOGLE_PLACES_API_KEY` | p/ prospecção | busca de candidatos |
| `ONBOARDING_BASE_URL` | não | padrão `https://onboarding.atendpragente.com.br` |
| `SIGNUP_BASE_URL` | não | base do link pessoal de convite do vendedor |
| `SESSION_HTTPS_ONLY` | não | padrão `1` (cookie só por HTTPS); `0` só pra rodar em `http://localhost` |

## Primeiro admin

Não há cadastro público. Depois de subir o serviço pela primeira vez
(ou se o Mongo for recriado), crie o admin dentro do pod; a senha vem
da entrada padrão, nunca como argumento:

```
kubectl -n atendagente-admin exec -i deploy/backoffice -- \
  python /app/criar_admin.py --nome "Fulano" --email fulano@x.com --username fulano <<'EOF'
a-senha-aqui
EOF
```

Daí em diante `/admin/vendedores` cria os vendedores (sempre com papel
`vendedor`; admin só nasce por este script).

## Comissão e churn

- A taxa é travada na atribuição, pela faixa de clientes ativos do
  vendedor naquele momento (`store.FAIXAS_COMISSAO`). Um tenant só pode
  ser atribuído uma vez (primeiro toque).
- `app/churn.py` mantém as atribuições alinhadas ao ciclo de vida do
  tenant, lendo `/api/admin/lixeira` e `/api/admin/tenants` do
  onboarding-service: tenant **cancelado** ou **excluído** deixa de gerar
  comissão; registra `dentro_clawback` (cancelou antes de 60 dias).
  Tenant **restaurado** na janela da lixeira volta a gerar comissão. A
  sincronização roda ao abrir o dashboard e as telas de admin (no máximo
  1x por minuto) e só age com sinal explícito — resposta vazia do
  onboarding nunca encerra comissão.
- O **valor** do estorno do clawback não é calculado aqui: o
  backoffice não registra pagamentos de comissão. O flag
  `dentro_clawback` diz ao fechamento mensal quais atribuições entram.
- A regra de saída por baixa performance (`registrar_mes_performance`)
  existe no `store`, mas **não é automática**: depende de decisão
  jurídica (spec, pendência 10) e de um fechamento mensal que ainda
  não existe.

## Prospecção

`/prospeccao` lista candidatos do Google Places (cidade × categoria,
`app/prospeccao.py`). O CronJob `backoffice-prospeccao` busca 50 por dia
(nasce suspenso); o botão "buscar mais" (admin + PIN) roda um lote em
segundo plano, um por vez.

## Local (dev)

```
pip install -r requirements.txt
export MONGO_URI="mongodb://localhost:27017/backoffice"
export BACKOFFICE_SESSION_SECRET="dev-secret-troque-em-producao"
export ONBOARDING_ADMIN_KEY="mesma-chave-do-onboarding-service-env"
export ADMIN_PIN="0000"
export SESSION_HTTPS_ONLY=0
uvicorn app.main:app --reload --port 8000
```

## Testes

```
pip install -r tests/requirements-test.txt
pytest tests
```

Usam `mongomock` e um onboarding-service de mentira: não precisam de
Mongo, rede nem credenciais.
