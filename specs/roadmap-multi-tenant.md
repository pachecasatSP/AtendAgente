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

Levantamento do repo (`bot-hermes`) confirma que hoje **não existe
nenhum código de aplicação** — só manifests K3s (`hermes-k3s/`) e SOULs em
markdown. Todo o comportamento do bot vem da imagem vendor
`nousresearch/hermes-agent`. A peça mais valiosa já descoberta
(`infra_hermes_profiles`): essa imagem suporta **múltiplos "profiles"** —
processos `hermes -p <nome> gateway run` isolados, cada um com seu próprio
`config.yaml`/`SOUL.md`/`.env`, supervisionados no mesmo pod. Isso já foi
usado para dar ao `/agent-api` um modelo de visão sem tocar no WhatsApp
(profile `agent-api-vision`). **A mesma mecânica, aplicada ao
`whatsapp_cloud`, é o caminho de menor esforço para multi-tenant**: um
profile por tenant, cada um com seu próprio `phone_number_id` /
`access_token` / SOUL — sem precisar reescrever o Hermes.

O trabalho real (não coberto pelo vendor) é: automatizar a criação desses
profiles a partir de um onboarding self-service, e construir o painel de
conversas/intervenção do zero (greenfield confirmado — não existe nenhum
esqueleto de painel/admin/CRM no repo).

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

### Fase 1 — Provar o padrão "1 profile = 1 tenant" com WhatsApp real
Repetir a receita do `agent-api-vision`, mas habilitando
`platforms.whatsapp_cloud` num profile novo (não no `default`) com um
número de teste real (Meta permite números de teste grátis para isso).
Confirmar: dois `whatsapp_cloud` simultâneos no mesmo pod funcionam sem
brigar por webhook/porta, e a CPX22 (2vCPU/4GB) aguenta o processo
adicional. **Isso valida a arquitetura inteira antes de construir
qualquer automação em cima dela.** Sem isso, as fases seguintes são
apostas.
- Repetir os footguns já documentados em `infra_hermes_profiles`: dono do
  arquivo de log (`chown hermes:hermes`), `gateway start` precisa rodar
  uma vez para persistir `desired_state`.
- Descobrir e documentar (novo item de memória) se o `webhook path` da
  Meta por número precisa de Ingress próprio por profile, ou se um único
  webhook + roteamento por `phone_number_id` no payload resolve — isso
  não está confirmado ainda e muda o design do Ingress.

### Fase 2 — SOUL como template, não arquivo à mão
Criar um template de SOUL (baseado na estrutura já validada: Quem eu sou
/ Como eu falo / serviços / regras / O que eu NÃO faço / Quando
encaminhar / Memória / Exemplos) com variáveis. Escrever um pequeno
gerador (script, não precisa de UI ainda) que recebe um JSON/YAML com as
respostas do formulário de onboarding e produz o `SOUL.md` final. Isso
desacopla "configurar um tenant" de "editar markdown".

### Fase 3 — Automação do provisionamento (CLI interno, sem UI ainda)
Um script que, dado `{tenant_id, phone_number_id, access_token, respostas
do SOUL}`, faz via SSH no servidor: cria o profile, grava SOUL gerado
(Fase 2), grava `.env` do profile, `gateway start`, valida `/health`.
Colapsa em um comando o que hoje é um processo manual de várias etapas
(como foi feita a troca Yogart→AC Soluções). Ainda operado pela Ac
Soluções, não pelo cliente — é o degrau antes do self-service real.

### Fase 4 — Onboarding self-service (Embedded Signup + formulário)
Primeira peça de software de verdade: um serviço web novo (namespace
próprio no K3s, seguindo o padrão "um namespace por projeto" já em uso)
que:
- Implementa o fluxo Embedded Signup (JS SDK da Meta) para o cliente
  conectar seu número.
- Coleta as respostas do formulário de SOUL (Fase 2).
- Chama a automação da Fase 3 (via API interna, não mais SSH manual).
- Fica no ar como cadastro pausado/pendente até confirmação de pagamento
  (depende da Fase 6, mas pode nascer com um "trial" sem cobrança).

### Fase 5 — Painel de conversas e intervenção manual
**Tem uma incógnita técnica a resolver antes de desenhar isso em
detalhe:** onde e em que formato o Hermes guarda o histórico de conversa
por profile dentro de `/opt/data/profiles/<tenant>/` (SQLite? JSON?) —
isso não foi confirmado ainda, precisa de uma sessão de investigação no
servidor (`kubectl exec` + inspecionar o diretório de um profile) antes
de decidir se o painel lê direto esse storage ou se precisa de uma API
intermediária. Uma vez resolvido isso, o painel é: lista de conversas por
tenant, thread de mensagens, botão "assumir conversa" (seta
`atendente_humano`/equivalente e permite enviar mensagem livre via Graph
API diretamente).

### Fase 6 — Cobrança
Assinatura por tenant (Stripe ou similar). V1 simples: checagem periódica
de status de pagamento que, se inadimplente, seta
`platforms.whatsapp_cloud.enabled: false` no profile (sem apagar dados)
em vez de destruir o tenant.

---

## Riscos / decisões a revisitar cedo

- **Capacidade da CPX22**: cada profile ativo é mais um processo no
  mesmo pod de 4GB. Definir, já na Fase 1, quantos tenants cabem antes de
  precisar de upgrade Hetzner ou de distribuir profiles entre mais de um
  pod/nó — isso muda o modelo de deploy (hoje `replicas:1` +
  `strategy:Recreate`, que assume single-instance).
- **Roteamento de webhook por tenant**: se cada `phone_number_id` exigir
  webhook/Ingress próprio, a Fase 3/4 de automação precisa também
  automatizar manifests K8s (Ingress/Secret) por tenant, não só o profile
  do Hermes — isso é mais trabalho do que só rodar `hermes profile
  create`.
- **Formato do histórico de conversa** (bloqueador da Fase 5) — não
  investigado ainda.

## Primeiro passo recomendado

Executar a **Fase 1** isolada: criar um profile de teste com
`whatsapp_cloud` habilitado, usando um número de teste da Meta (grátis),
confirmar que roda em paralelo ao `default` sem conflito, e documentar o
resultado (webhook por profile ou compartilhado) como uma nova entrada de
memória (`infra_hermes_profiles` ou uma nova `roadmap_multitenant_fase1`)
antes de prosseguir para qualquer automação.

## Verificação

- Fase 1: mensagem real recebida/respondida no número de teste enquanto
  o profile `default` (WhatsApp da AC Soluções) continua respondendo
  normalmente — prova de isolamento.
- Fase 2: gerar 2 SOULs diferentes a partir do mesmo template com inputs
  diferentes, revisar manualmente se ficam coerentes com a estrutura hoje
  usada nos SOUL-*.md existentes.
- Fase 3: rodar o script de ponta a ponta para um tenant fictício e medir
  quanto tempo leva vs. o processo manual documentado em `infra_k3s`.
- Fase 4/5/6: cada uma testável isoladamente antes de integrar (signup
  sem painel, painel com dados mockados antes de plugar no storage real,
  etc.) — não faz sentido testar a jornada completa antes das peças
  isoladas funcionarem.
