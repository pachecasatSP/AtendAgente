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

**Nome do atendente virtual + EULA + consentimento (2026-08-15):**
- Campo opcional `nome_atendente` no passo 1 do wizard — se
  preenchido, `SOUL.template.md`/`generate_soul.py` fazem o assistente
  se apresentar com nome (mesmo padrão da Duda: "Meu nome é **X**...
  sou X, o atendimento automático d[negócio]"); sem nome, comportamento
  igual a antes (fala em nome do negócio, "a gente").
- `poc/landing-page/eula.html` — Termos de Uso (EULA), mesmo
  layout/branding de `privacidade.html`, 10 seções (serviço, cadastro,
  planos/cobrança/trial, cancelamento/suspensão, responsabilidades,
  limitação de responsabilidade, dados, contato, alterações). Rascunho
  funcional pra v1, ainda sem revisão jurídica formal.
- Checkbox obrigatório de aceite do EULA no último passo do wizard
  (`required` client-side + validado no servidor,
  `eula_aceito_em` gravado no signup) e checkbox opcional de
  consentimento de comunicações (`comunicacoes_aceito`, bool).

### Fase 5 — Espelho de conversas em MongoDB (dados) + painel por tenant (CONCLUÍDA, 2026-08-13)

**Mudança de escopo decidida nesta sessão**: o painel deixou de ser um
painel central multi-tenant e passou a ser **por tenant**, provisionado
junto com o resto (`build_infra_manifest`) em vez de construído depois
como peça separada — ver `tools/tenant-panel/`. Cada tenant tem seu
próprio painel em `https://<tenant>.../painel` (mesma Ingress do
webhook, path separado). Autenticação (revisada em 2026-08-15): não é
mais HTTP Basic com senha pré-gerada — o cliente cadastra o próprio
usuário/senha na primeira visita via link de configuração de uso único
(`PANEL_SETUP_TOKEN` no Secret), depois é login por formulário com
sessão em cookie assinado (`PANEL_SESSION_SECRET`).

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
`build_infra_manifest`.

**Handoff manual — CONCLUÍDA (2026-08-15), Fase A+B:** operador
consegue assumir uma conversa pelo painel (mandar mensagem direto via
Graph API, silenciando o bot naquele contato), como previsto na seção
"Jornada do cliente" acima.

- *Fase A (painel):* campo `handoff` (bool) na collection `sessions`;
  `POST /painel/api/sessions/{id}/handoff` liga/desliga; `POST
  /painel/api/messages/{id}/send` manda mensagem via Cloud API
  (`WHATSAPP_CLOUD_ACCESS_TOKEN`/`WHATSAPP_CLOUD_PHONE_NUMBER_ID`, já
  disponíveis no container do painel via o mesmo Secret `{tenant}-env`
  do Hermes) e liga `handoff` automaticamente ao enviar.
- *Fase B (bot realmente silencia):* como o container do Hermes não
  fala com o Mongo (só o sidecar `mongo-sync` fala), e
  `gateway/platforms/whatsapp_cloud.py` (onde `send()` faz a chamada
  real à Graph API) vem embutido na imagem vendorizada
  `nousresearch/hermes-agent` (não é hostPath-mountável como o resto),
  a solução foi: (1) `mongo_sync/sync_conversations.py` ganhou um
  servidor HTTP local (`127.0.0.1:8091/handoff?chat_id=...`, mesmo pod,
  cache em memória atualizado a cada ciclo de sync) e (2) um patch em
  `whatsapp_cloud.py` (`_is_handoff_active`, chamado no início de
  `send()`) consulta esse endpoint e suprime o envio se `handoff:
  true`. O patch é aplicado via **ConfigMap overlay** (`kubectl create
  configmap whatsapp-cloud-patch` a partir de
  `tools/provision-tenant/whatsapp_cloud_patched.py`, montado por cima
  do arquivo original via `subPath` — nunca editando a imagem em si),
  gerado/reaplicado por `setup_mongo.py` e montado automaticamente pra
  todo tenant novo em `build_infra_manifest`. Falha aberta: se o
  endpoint de handoff não responder, o bot responde normal (nunca
  trava o atendimento por causa disso).
- Tenant pausado (`WHATSAPP_CLOUD_ENABLED=false`, ver Fase 7) mostra um
  "tapume" (`tools/tenant-panel/templates/tapume.html`) no lugar do
  painel normal, em vez de tela em branco/quebrada.

### Fase 6 — Cobrança
Assinatura por tenant (Asaas — ver Fase 4). V1 simples: checagem
periódica de status de pagamento que, se inadimplente, desativa o
tenant (ver `set_tenant_enabled` na Fase 7) em vez de destruir.

**Tokens de gratuidade — CONCLUÍDA (2026-08-15):** collection
`free_tokens` no Mongo (`store.create_free_token`/`get_free_token`/
`mark_free_token_used`, uso único). Fluxo: `/signup?invite=<token>` →
`signup.html` repassa `invite` no POST de `/api/signup/callback` →
`signup_callback` valida e grava `invite_token` no doc do signup →
`submit_form` detecta `invite_token` e pula o checkout da Asaas
inteiro, provisionando na hora (`_run_provisioning` chamado
sincronamente, sem esperar webhook de pagamento). Cliente ainda passa
pelo formulário de billing (CPF/CNPJ, endereço etc.) mesmo em cadastro
gratuito — simplificação aceita por ora, nunca é cobrado.

**Preço "de/por" (2026-08-15):** valores revisados pra terminar em
`,90` e mostrar desconto simulado (~20% sobre o preço anterior, que
virou o "de" riscado). `Começando`: de R$ 197,00 por **R$ 157,90**.
`Crescendo`: de R$ 897,00 por **R$ 717,90**. `PLANOS` em
`onboarding-service/app/main.py` ganhou a chave `valor_de` (só
exibição — `valor` continua sendo o preço real cobrado na Asaas).
Aplicado também no wizard (`form.html`), na landing page (cards +
JSON-LD) e no SOUL da Duda. O link "precisa de mais volume" no wizard
e na landing deixou de ser `mailto:` e virou o WhatsApp da Duda
(`wa.me/5511920081743`).

**Plano Entrada (2026-08-15):** quarto plano, abaixo do Começando —
500 conversas/mês, de ~~R$ 105,90~~ por **R$ 84,90**. Motivo de existir:
uma proposta inicial de "500 conversas por R$70" foi barrada em análise
de viabilidade — não por custo (WhatsApp é grátis pra resposta dentro
da janela de 24h; LLM via OpenRouter custa centavos por conversa), mas
por quebrar a lógica da escada: R$70/500 = R$0,14/conversa, mais barato
por unidade que o próprio Começando (R$0,158/conversa), o que
canibalizaria o plano do meio. R$84,90/500 = R$0,17/conversa preserva
a ordem correta (quem compromete menos paga mais por conversa):
Entrada (0,170) > Começando (0,158) > Crescendo (0,144). Começando e
Crescendo não mudaram de valor.

### Fase 7 — Ferramentas administrativas via MCP (CONCLUÍDA, 2026-08-15)

A Duda (bot da AC Soluções, `hermes-duda`, cluster `2.28.15.6`,
namespace `hermes` — cluster diferente do `atendagente`) ganhou 4
ferramentas de administração: `convite` (gera token de gratuidade),
`listar` (tenants ativos), `uso` (sessões/mensagens de um tenant),
`ativar` (liga/desliga o WhatsApp de um tenant — ver
`set_tenant_enabled` em `provision_tenant.py`).

**Arquitetura:** `tools/admin-mcp/server.py` — servidor MCP standalone
(`mcp` SDK 2.0, `MCPServer` + `streamable_http_app`), deployado em
`atendagente` (`admin-mcp.atendpragente.com.br`), que a Duda consome
via `hermes mcp add` (transporte HTTP, mesmo mecanismo suportado
nativamente pelo framework — não precisa de patch vendorizado). Ele não
fala com kubectl/Mongo diretamente: delega tudo pro onboarding-service
(`/api/admin/*`), que já tem RBAC/kubeconfig restrito ao namespace
`atendagente`.

**Segurança em duas camadas, nenhuma decidida pela IA:**
1. `MCP_AUTH_TOKEN` — Bearer estático, checado por middleware Starlette
   antes de qualquer chamada chegar nas tools (protege o endpoint MCP
   em si; a Duda está em `GATEWAY_ALLOW_ALL_USERS=true`, pública).
2. `ADMIN_PIN` — cada tool exige esse PIN como argumento, comparado no
   servidor (`secrets.compare_digest`). A Duda nunca "decide" quem é
   confiável — o SOUL dela (seção "Ferramentas administrativas") proíbe
   explicitamente presumir identidade, revelar o PIN, ou tentar de
   novo sem um PIN novo depois de um erro.
3. `ONBOARDING_ADMIN_KEY` — autenticação servidor-a-servidor entre o
   admin-mcp e o onboarding-service (`x-admin-api-key`), também nunca
   visto pela IA.

**Pegadinha descoberta (custou duas iterações):** o modelo padrão da
Duda (`meta-llama/llama-3.3-70b-instruct`, o mesmo usado no WhatsApp de
todo tenant) não conseguia invocar as ferramentas vindas de MCP de
forma confiável — alucinava uma tag pseudo-function-call auto-fechada
(`<function name="tool_call" parameters="..." />`) que o próprio
mecanismo de limpeza do Hermes (`agent_runtime_helpers.py`,
`_NAMED_FUNCTION_BLOCK_PATTERN`) não reconhece (só cobre a variante com
abre/fecha `<function name="...">...</function>`, não a auto-fechada),
então vazava como texto cru pro WhatsApp. Ferramentas nativas do Hermes
(ex: `memory`) não tinham esse problema. Encurtar os nomes das tools
(`atendpragente-admin`/`listar_tenants` → `admin`/`listar`) não
resolveu. Fix real: trocar o modelo **só do profile da Duda** (pod
dedicado, não afeta outros tenants) pra `openai/gpt-5.6-luna` em
`/opt/data/config.yaml` — mesmo modelo já validado no profile de visão
do `/agent-api` (ver `infra_hermes_profiles`). É modelo pago (não é SKU
`:free`), gera custo real por mensagem — trade-off aceito em troca de
tool-calling confiável.

### Fase 8 — Controle de volume/custo por tenant (CONCLUÍDA, 2026-08-15)

Item que ficava só implícito no marketing dos planos ("até 1.000/5.000
conversas") sem nenhum controle técnico. **Definição de "conversa"**
(decisão explícita): contatos únicos (`chat_id` distinto) no mês
corrente — não sessões, mais fácil de justificar pro cliente ("quantas
pessoas diferentes te procuraram") e não infla o número com múltiplas
sessões do mesmo contato.

**Arquitetura:** `tools/provision-tenant/usage_watch.py`, `CronJob`
diário (03:17 UTC, namespace `atendagente`, mesmo padrão leve dos
sidecars — `python:3.12-slim` + `pip install pymongo`, sem imagem
própria; bootstrap idempotente em `setup_usage_watch.py`). Pra cada
tenant `status=live`, conta `sessions.distinct("chat_id", {started_at
>= início do mês})` e compara com `LIMITES` (duplicado de `PLANOS` do
onboarding-service de propósito — sem import cross-serviço por um dict
de 2 linhas). Grava `usage_current_month`/`usage_limite`/`usage_pct`/
`usage_status` (`ok`/`warning`≥80%/`over`≥100%) no doc do signup.

**Sem ação automática — só mede e sinaliza.** Nenhum tenant é pausado
ou cobrado sozinho quando estoura; a decisão (negociar upgrade, cobrar
excedente pela taxa de R$0,14/conversa do pitch Sem Limite, ou pausar
com `set_tenant_enabled`) é sempre manual. Dois pontos de visibilidade:

1. `GET /api/admin/usage-alerts` (onboarding-service) + ferramenta nova
   `alertas` no admin-mcp (PIN-gated, mesmo padrão das outras 4) — a
   Duda lista quem está em warning/over.
2. `GET /painel/api/usage` (tenant-panel) — barra de progresso no topo
   do painel do próprio cliente ("X de Y conversas esse mês", cor muda
   verde→amarelo→vermelho conforme o status). Painéis sem doc de
   signup (ex: o da Duda, que não passa pelo onboarding) simplesmente
   escondem a barra, sem erro.

Cobrança automática do excedente (criar cobrança avulsa na Asaas
quando `over`) fica pra uma Fase 8.2 futura, mais arriscada por mexer
com dinheiro sem revisão — só depois desse v1 (visibilidade) validado
com uso real.

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
