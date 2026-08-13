# Roadmap Multi-Tenant — AtendPraGente

## Contexto

Hoje o Hermes atende **um único número/persona por vez** (troca de SOUL =
sobrescrever `/opt/data/SOUL.md` + restart, documentado em `infra_k3s`).
Isso já deixou o Yogart sem WhatsApp desde a troca para AC Soluções
(29/07). Decisão de negócio: **Yogart não é mais prioridade** — o produto
agora é o **AtendPraGente**, vendido pela Ac Soluções (Tech Provider já
aprovado pela Meta — Business/Access/App Review ✓, ver
`business_meta_tech_provider`) para clientes B2B externos.

Modelo escolhido: **SaaS self-service** — o cliente conecta seu próprio
número via **Embedded Signup**, configura o SOUL, e paga assinatura —
**com um painel** onde a Ac Soluções (e/ou o próprio cliente) vê as
conversas e pode intervir manualmente quando necessário.

Levantamento do repo original (`bot-hermes`, agora `poc/` neste
repositório) confirma que até aqui **não existia nenhum código de
aplicação** — só manifests K3s e SOULs em markdown. Todo o comportamento
do bot vem da imagem vendor `nousresearch/hermes-agent`.

**Decisão de arquitetura (revista em 2026-08-12): pod-por-tenant, não
profile-por-tenant.** A primeira ideia era reaproveitar o mecanismo de
"profiles" do `hermes-agent` (múltiplos processos `hermes -p <nome>
gateway run` num único pod, usado hoje pelo profile `agent-api-vision` —
ver `infra_hermes_profiles`). Isso foi descartado como base do
multi-tenant: colocar N clientes no mesmo pod significa que um profile
com problema (o footgun já documentado de arquivo de log com dono errado
travando silenciosamente) pode arrastar todos os outros tenants junto, e
o webhook/roteamento por profile dentro do mesmo Ingress vira um problema
extra pra resolver. Com uma segunda máquina Hetzner disponível
(**62.238.103.17**, `ubuntu-16gb-hel1-1`, 8 vCPU/16GB, K3s já instalado,
~8.2GB livres — já roda em produção o namespace `consultor`/re-colocar-me,
isolado e intocado), a escolha é **um Deployment/Service/Ingress próprio
por tenant**, usando os primitivos nativos do K8s para isolamento em vez
de depender da multiplexação interna do hermes-agent. Namespace dedicado
criado: **`atendagente`** (separado de `consultor`).

**Domínio: `atendpragente.com.br`** (registrado, DNS na Cloudflare, ainda
propagando em 2026-08-12). Cada tenant recebe um subdomínio
(`<tenant>.atendpragente.com.br`). Certificado **por tenant via HTTP-01**
(não wildcard/DNS-01) — mais simples de automatizar, mesmo padrão já
usado em `bot.colocar-me.com.br`: basta o registro A do subdomínio
apontar pro `62.238.103.17` antes do Ingress ser aplicado.

O trabalho real (não coberto pelo vendor) é: gerar os manifests K8s por
tenant a partir de um template, automatizar esse provisionamento a partir
de um onboarding self-service, e construir o painel de conversas/
intervenção do zero (greenfield confirmado — não existe nenhum esqueleto
de painel/admin/CRM no repo). A migração/renomeação do namespace
`consultor` → `re-colocar-me` foi discutida e **adiada deliberadamente**
— não é trivial (tem Postgres/RabbitMQ/Elasticsearch com dados reais,
exigiria uma migração cuidadosa com backup) e não bloqueia o AtendAgente,
que fica isolado no namespace `atendagente`.

---

## Jornada do cliente (tenant)

1. **Descoberta** — cliente vê o site institucional (Netlify,
   `solucoes-re.colocar-me.com.br`) ou é indicado pela Ac Soluções.
2. **Cadastro self-service** — fluxo de **Embedded Signup** da Meta:
   cliente autentica com a própria conta Business/WhatsApp e concede à Ac
   Soluções (Tech Provider) permissão para gerenciar seu número. Sistema
   recebe `phone_number_id` + `waba_id` do cliente via callback do SDK.
3. **Configuração do SOUL** — formulário guiado (não markdown à mão):
   nome do negócio, tom de voz, serviços/produtos, contato de escalonamento,
   o que o bot NÃO deve inventar. Gera um SOUL.md a partir de um template.
4. **Provisionamento automático** — sistema roda o equivalente a
   `hermes profile create --clone-from <template>`, grava o SOUL gerado,
   grava as credenciais do tenant no `.env` do profile, `gateway start`.
   Número do cliente fica ativo em minutos, sem intervenção manual da Ac
   Soluções.
5. **Operação** — bot atende no número do próprio cliente. Handoff humano
   (`atendente_humano`, já previsto na spec) silencia o bot quando
   necessário.
6. **Painel** — cliente (ou operador da Ac Soluções) loga num painel web,
   vê a lista de conversas do seu tenant, lê o histórico, e pode assumir
   manualmente uma conversa (mandar mensagem direto, silenciar o bot
   naquele contato).
7. **Cobrança** — assinatura mensal por tenant; inadimplência desativa o
   profile (`whatsapp_cloud.enabled: false`) sem apagar dados.

---

## Roadmap técnico (fases, em ordem de dependência)

### Fase 1 — Provar o padrão "1 pod = 1 tenant" no cluster novo
No namespace `atendagente` (62.238.103.17), criar o primeiro Deployment
de tenant de teste:
- **Deployment**: imagem `nousresearch/hermes-agent:latest`, `args:
  ["gateway","run"]` (sem `-p`, cada pod já é single-tenant por si só —
  não precisa da mecânica de profiles aqui), `replicas: 1`, `strategy:
  Recreate`, `envFrom` um Secret próprio do tenant.
- **PVC** dedicado (ex. 1-2Gi, `local-path`) montado em `/opt/data`,
  guardando o `SOUL.md` e o estado/memória daquele tenant.
- **Secret**: `WHATSAPP_CLOUD_PHONE_NUMBER_ID/ACCESS_TOKEN/APP_SECRET/
  WABA_ID/VERIFY_TOKEN` do número de teste (Meta dá número de teste
  grátis).
- **Service** (ClusterIP, porta 8090) + **Ingress** (host
  `<tenant-de-teste>.atendpragente.com.br`, TLS via `letsencrypt-prod`
  já configurado no cluster, HTTP-01) — precisa do registro A na
  Cloudflare apontando pro `62.238.103.17` **antes** de aplicar o
  Ingress (mesma regra já aprendida em `infra_k3s`).
- Nomear todos os recursos com prefixo do tenant (`<tenant>-hermes`,
  etc.) para não colidir quando houver um segundo tenant.

**Isso valida a arquitetura inteira antes de construir qualquer
automação em cima dela** — inclusive serve de molde (YAML) que a Fase 3
vai aprender a gerar automaticamente.

### Fase 2 — CONCLUÍDA (2026-08-12): SOUL como template, não arquivo à mão
Template de SOUL (`tools/soul-generator/SOUL.template.md`) baseado na
estrutura já validada (Quem eu sou / Como eu falo / serviços / Como a
gente trabalha / O que eu ainda não sei / O que eu NÃO faço / Quando
encaminhar / Memória / Exemplos), com um gerador
(`tools/soul-generator/generate_soul.py`, só stdlib + PyYAML, sem
dependência de motor de template) que recebe um YAML com as respostas do
onboarding e produz o `SOUL.md` final. Validado com dois YAMLs de
exemplo: uma reconstrução do SOUL real da AC Soluções (prova que
reproduz a estrutura existente) e um negócio bem diferente — clínica
odontológica fictícia (prova que generaliza). Desacopla "configurar um
tenant" de "editar markdown". Detalhes/schema em
`tools/soul-generator/README.md` — inclui uma limitação conhecida de
fraseado quando o nome de escalação já vem com artigo definido embutido.

### Fase 3 — CONCLUÍDA (2026-08-13): execução real ponta a ponta validada
`tools/provision-tenant/provision_tenant.py`: dado um YAML de tenant
(infra + schema de SOUL da Fase 2), cria o registro DNS via API da
Cloudflare, aplica o Secret (já com `GATEWAY_ALLOW_ALL_USERS=true` e a
chave de LLM embutidos — as duas pegadinhas da Fase 1), aplica
PVC/Deployment/Service/Ingress no padrão validado, espera o pod ficar
pronto, gera e publica o `SOUL.md` (reaproveitando o gerador da Fase 2),
e reinicia. Credenciais só por variável de ambiente, nunca no YAML nem
em argv. Roda no servidor, onde `kubectl` já aponta pro cluster certo.

Rodado de verdade (sem `--dry-run`) reaproveitando o tenant de teste da
Fase 1 (`teste-atendagente-hermes`): DNS (idempotente, registro já
existia), Secret, PVC/Deployment/Service/Ingress reaplicados, SOUL de
teste gerado e publicado, pod reiniciado, e o handshake do webhook
(`hub.challenge`) confirmado funcionando pelo pod atualizado. Dois bugs
reais encontrados e corrigidos nessa primeira execução real (não
apareciam em dry-run):
1. A checagem de "registro DNS já existe" só cobria o código de erro
   `81057` da Cloudflare — a API retornou `81058`
   ("An identical record already exists") pra um registro A idêntico,
   que não estava coberto.
2. Mais grave: como a Cloudflare responde HTTP 400 pra esse caso, o
   `urllib.request.urlopen` lança `HTTPError` **antes** do código
   chegar a olhar o corpo JSON pra checar o código do erro — a lógica de
   "não é fatal" nunca era executada pra respostas não-2xx. Corrigido
   fazendo o parse do corpo de erro dentro do próprio bloco `except
   HTTPError`.

Colapsa em um comando o que antes era um processo manual de várias
etapas. Ainda operado pela Ac Soluções, não pelo cliente — é o degrau
antes do self-service real (Fase 4).

**Nota de segurança do processo:** o token da API da Cloudflare nunca é
passado por variável de ambiente/argv em comando remoto nem colado no
chat — fica em `/root/.cloudflare_api_token` (chmod 600), criado
manualmente via SSH interativo direto pelo operador. Um wrapper
(`tools/provision-tenant/reprovision-teste-atendagente.sh`) lê esse
arquivo e as credenciais do WhatsApp/OpenRouter direto do Secret k8s já
existente, sem nunca expor nenhum valor sensível em texto. Ver
`security_cloudflare_token_leak` na memória: um token colado por engano
no chat durante essa validação foi identificado e o usuário revogou/
recriou antes do reprovisionamento funcionar.

### Fase 4 — CONCLUÍDA (implementação, 2026-08-13): Onboarding self-service
Primeira peça de software de verdade do produto:
`tools/onboarding-service/` (FastAPI, namespace próprio
`atendagente-onboarding`, kubeconfig namespace-scoped restrito a
`atendagente` — nunca o kubeconfig admin do cluster, que também
alcançaria `consultor`). Sem Docker/registry: mesmo padrão sem-build já
validado no sidecar `mongo-sync` (`python:3.12-slim` + `pip install` no
startup, código via `hostPath`).

- Implementa o fluxo Embedded Signup (JS SDK da Meta): `code` de
  autorização trocado por access token server-side
  (`app/meta_client.py`), `waba_id`/`phone_number_id` capturados do
  evento `WA_EMBEDDED_SIGNUP`.
- Formulário de SOUL server-rendered (Jinja2, `POST` tradicional, sem
  SPA) mapeando pro schema da Fase 2.
- Chama `provision_tenant.provision()` **por import direto** (não
  subprocess/SSH) — `provision()` agora retorna um dict de credenciais
  em vez de só imprimir.
- **Descoberta que mudou o escopo**: a console da Meta não tem UI pra
  rotear webhook por WABA em contas Tech Provider — só via API. Nova
  função `subscribe_app_to_waba()` (`POST /{waba_id}/subscribed_apps`)
  chamada logo depois de `provision()`, fora dela (mantém `provision()`
  utilizável standalone pelos fluxos manuais como
  `reprovision-teste-atendagente.sh`).
- Estado do cadastro (`code_exchanged`→`provisioning`→`live`/`failed`)
  numa coleção `signups` no MongoDB compartilhado da Fase 5 — sem
  datastore novo.
- Ainda sem cobrança de verdade — todo tenant novo nasce
  `plano: trial` (Fase 6 decide o que fazer com isso).

**Infra do onboarding-service validada contra o cluster real
(2026-08-13/14)**: `onboarding-service-env` criado via SSH interativo
(`--from-env-file`), `setup_onboarding_service.py` rodado com sucesso —
namespace, RBAC, kubeconfig namespace-scoped, cópia do Mongo, DNS,
cert TLS, Deployment/Service/Ingress todos no ar. Isolamento RBAC
testado na prática (`kubectl -n consultor get pods` de dentro do pod
→ `Forbidden`; `-n atendagente` → permitido). `/signup` responde 200
com App ID/Config ID reais da Meta embutidos.

Dois bugs corrigidos nessa primeira execução real:
1. Aspas duplas aninhadas no comando de instalação do `kubectl` dentro
   de um YAML já delimitado por aspas — quebrava o parser.
2. `ONBOARDING_HOST` apontava pra `atendpragente.com.br` (ainda sem
   propagação) — trocado pra `onboarding.colocar-me.com.br`, mesmo
   domínio usado pelo resto do projeto.

**Ainda falta**: o passeio real pelo fluxo Embedded Signup → formulário
→ provisionamento → `subscribe_app_to_waba` num navegador, contra o
WABA/número de teste — esse é o teste crítico que prova que o webhook
override realmente funciona ponta a ponta.

### Fase 5 — Espelho de conversas em MongoDB (dados) + painel por tenant (CONCLUÍDA, 2026-08-13)

**Mudança de escopo decidida nesta sessão**: o painel deixou de ser um
painel central multi-tenant e passou a ser **por tenant**, provisionado
junto com o resto (`build_infra_manifest`) em vez de construído depois
como peça separada — ver `tools/tenant-panel/`. Cada tenant tem seu
próprio painel em `https://<tenant>.../painel` (mesma Ingress do
webhook, path separado), protegido por HTTP Basic Auth
(`PANEL_USER`/`PANEL_PASSWORD` gerados no provisionamento, junto do
`verify_token`).

**Incógnita resolvida (2026-08-13):** inspeção ao vivo do pod de teste
(`kubectl exec` + `sqlite3` em `/opt/data/state.db`) confirmou que o
hermes-agent guarda sessões e mensagens em SQLite, tabelas `sessions` e
`messages` (WAL mode), sem nenhum hook de config pra outro backend —
`response_store.db` é só cache de respostas, não o log real.

**Decisão:** em vez de o painel ler direto o SQLite de cada pod (exigiria
`kubectl exec` por tenant a cada consulta, ou uma API intermediária por
tenant), sobe um **MongoDB compartilhado** no namespace `atendagente`
(`tools/provision-tenant/setup_mongo.py`, recurso único do cluster, não
por tenant), com as conversas de todos os tenants nele, particionadas por
`tenant_id`. Um **sidecar** (`mongo-sync`, adicionado em
`build_infra_manifest` de `provision_tenant.py`) roda no mesmo pod de
cada tenant, monta o mesmo PVC como `readOnly: true`, e sincroniza
`sessions`/`messages` pro Mongo a cada ~15s (polling curto — "quase em
tempo real", sem exigir tail de WAL). O cursor de sincronização
(`sync_state`) fica no próprio Mongo, não no PVC do tenant, então o
sidecar é stateless/restart-safe. Credenciais do Mongo são um Secret
único compartilhado (`mongo-credentials`), não duplicado por tenant.

**UI do painel**: lista de conversas + thread (`tools/tenant-panel/app.py`,
FastAPI + Jinja2) implementada e gerada automaticamente por
`build_infra_manifest`. Ainda não tem botão "assumir conversa" (enviar
mensagem livre via Graph API silenciando o bot) — fica pra depois,
mas a base de leitura já está no ar. Validação end-to-end contra o
cluster real ainda pendente (junto com a da Fase 4, mesmo lote de
testes).

### Fase 6 — Cobrança
Assinatura por tenant (Stripe ou similar). V1 simples: checagem periódica
de status de pagamento que, se inadimplente, seta
`platforms.whatsapp_cloud.enabled: false` no profile (sem apagar dados)
em vez de destruir o tenant.

---

## Riscos / decisões a revisitar cedo

- **Capacidade do cluster novo**: 8.2GB livres / 8 vCPU hoje, mas o
  namespace `consultor` já compartilha a máquina. Definir, já na Fase 1,
  o footprint real de um pod `hermes-agent` (idle e sob carga) pra
  projetar quantos tenants cabem antes de precisar de outro nó — o
  `consultor` não deve sofrer contenção de recursos por causa do
  AtendAgente (considerar `ResourceQuota` no namespace `atendagente` cedo).
- **Automação de DNS**: cada tenant novo precisa de um registro A na
  Cloudflare antes do Ingress — se isso ficar manual demais, vira gargalo
  do self-service (Fase 4). A Fase 3 já assume automação via API da
  Cloudflare; validar credenciais/token de API cedo.
- **Migração `consultor` → `re-colocar-me`**: adiada, não bloqueia este
  roadmap, mas fica pendente como dívida separada (não documentar aqui os
  detalhes — é um projeto à parte, envolve dados reais em produção).

## Fase 1 — CONCLUÍDA (2026-08-12)

Deployment de teste (`teste-atendagente`) criado no namespace
`atendagente`, DNS (`teste-atendagente.colocar-me.com.br`, via API da
Cloudflare — `atendpragente.com.br` ainda propagando) e certificado
(staging → prod) confirmados, handshake do webhook (`hub.challenge`)
respondido corretamente. Mensagem real enviada ao número de teste foi
recebida e respondida pelo bot (`response ready: time=20.5s
response=89 chars`) — round-trip completo validado, isolado do namespace
`consultor` no mesmo cluster.

**Duas pegadinhas descobertas que a automação da Fase 3 precisa cobrir**
(um `hermes-agent` só com credenciais de WhatsApp não funciona sozinho):
1. `GATEWAY_ALLOW_ALL_USERS=true` é obrigatório no Secret de cada
   tenant — sem isso o gateway nega remetentes desconhecidos
   silenciosamente (sem erro visível pro remetente).
2. Cada tenant precisa da própria chave de provedor de LLM
   (`OPENROUTER_API_KEY` ou equivalente) no Secret — não herda nada do
   cluster de produção da AC Soluções (são clusters diferentes).

Detalhes completos (incluindo a pegadinha de depuração com `kubectl
logs` vs. os arquivos de log em disco) na memória `infra_atendagente_k3s`.

## Próximo passo

Fases 1, 2, 3 e o pipeline de dados da Fase 5 (Mongo + `mongo-sync`)
estão provadas de ponta a ponta contra o cluster real. O painel por
tenant (Fase 5 UI) e o onboarding self-service (Fase 4) foram
**implementados** nesta sessão, mas ainda **não validados contra o
cluster** — falta:
1. Rodar `tools/provision-tenant/setup_mongo.py` de novo se ainda não
   estiver com a versão mais recente, e reaplicar o tenant de teste
   (`reprovision-teste-atendagente.sh`) pra ele ganhar o painel também.
2. Criar manualmente o Secret `onboarding-service-env` no servidor (App
   Secret/ID da Meta, Config ID, credenciais compartilhadas — ver
   `tools/onboarding-service/README.md`) e rodar
   `setup_onboarding_service.py`.
3. Rodar o fluxo completo (Embedded Signup real com o WABA/número de
   teste já usado nas fases anteriores → formulário → tenant novo no
   ar) e confirmar o `subscribe_app_to_waba()` realmente roteou o
   webhook — esse é o teste crítico desta fase.

Depois disso, falta só a **Fase 6** (cobrança) pra fechar o roadmap
técnico — hoje todo tenant novo nasce `plano: trial` sem cobrança.

## Verificação

- Fase 1 ✓: mensagem real recebida/respondida no número de teste, pod
  isolado no namespace `atendagente`, sem qualquer efeito nos pods do
  namespace `consultor` no mesmo cluster — prova de isolamento por
  namespace + Deployment dedicado.
- Fase 2: gerar 2 SOULs diferentes a partir do mesmo template com inputs
  diferentes, revisar manualmente se ficam coerentes com a estrutura hoje
  usada nos SOUL-*.md existentes.
- Fase 3: rodar o script de ponta a ponta para um tenant fictício e medir
  quanto tempo leva vs. o processo manual documentado em `infra_k3s`.
- Fase 5 (pipeline de dados): mandar mensagem real pro número de teste,
  confirmar em até ~30s que ela aparece em `db.messages` no Mongo com o
  mesmo conteúdo/`session_id` que aparece no `state.db` via `sqlite3`
  dentro do pod; reiniciar o pod do tenant e confirmar que a sincronização
  retoma do cursor salvo sem duplicar nem perder mensagens.
- Fase 5 (painel): aplicar no tenant de teste, confirmar Basic Auth e
  lista de conversas batendo com o Mongo, confirmar que o webhook
  continua respondendo na mesma Ingress (path novo não quebrou o path
  existente).
- Fase 4: preencher o form isolado (comparar YAML gerado com
  `exemplo-tenant.yaml`), testar troca `code`→token e
  `subscribed_apps` isoladamente antes do fluxo completo, depois rodar
  ponta a ponta criando um tenant novo de fato com o WABA de teste —
  `kubectl get pods` `Running`, webhook responde, mensagem real
  chega/é respondida. Testar negativo do RBAC (`kubectl --kubeconfig
  <gerado> -n consultor get pods` deve dar `Forbidden`).
- Fase 6: ainda não implementada.
