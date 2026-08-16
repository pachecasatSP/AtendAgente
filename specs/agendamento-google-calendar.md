# Agendamento via bot, integrado com Google Agenda

## Contexto

Hoje o bot só sabe conversar — quando o cliente quer marcar um horário,
o SOUL.md manda escalar pra pessoa responsável (seção "Quando
encaminhar"). A ideia aqui é dar ao bot uma ferramenta de verdade pra
resolver isso sozinho: checar a agenda do tenant no Google Calendar,
criar o evento no horário pedido (se estiver livre) e responder na
própria conversa do WhatsApp com o link do evento/Google Meet — sem
precisar da Duda ou do dono do negócio no meio.

## Decisões de arquitetura (conversadas com o usuário, 2026-08-16)

- **Autenticação Google: conta de serviço + compartilhamento manual**,
  não OAuth2 por tenant. Uma única conta de serviço da AtendPraGente
  (Google Cloud) chama a Calendar API; cada tenant só precisa
  compartilhar a própria Google Agenda com o e-mail dessa conta de
  serviço (30 segundos, direto no Google Calendar → Configurações →
  Compartilhar com pessoas específicas). Evita construir um fluxo OAuth
  completo (tela de consentimento, possível revisão da Google pra
  escopos de agenda, renovação de refresh token por tenant) — trade-off
  aceito: os eventos aparecem como criados pela conta de serviço, não
  pelo dono do negócio, e tem um passo manual de setup por tenant.
- **Bot agenda direto, sem aprovação humana no meio.** Confere
  disponibilidade (freebusy) e já cria o evento se estiver livre. Se
  não estiver, informa e sugere perguntar outro horário — não fica
  esperando confirmação de ninguém.
- **"Invite" = link colado na própria conversa do WhatsApp, sem
  e-mail.** O evento é criado com Google Meet habilitado
  (`conferenceData.createRequest`); o bot manda o link do Meet (e/ou o
  link do evento no Google Agenda) direto no WhatsApp. Não pede e-mail
  do cliente nem manda convite formal por e-mail — resolve mesmo pra
  quem só tem WhatsApp.
- **v1 sem checagem de horário comercial.** Só confere conflito de
  horário na agenda (freebusy). Duração do agendamento é um campo
  configurável no painel (padrão 30min), mas não há janela de
  atendimento restrita — fica a critério de quem pede o horário.

## Infra necessária (a provisionar do zero, nada disso existe ainda)

1. **Projeto no Google Cloud** dedicado à AtendPraGente (ou reaproveitar
   um existente da AC Soluções, a decidir na hora — ainda não escolhido).
2. **Habilitar a Google Calendar API** nesse projeto.
3. **Criar uma conta de serviço** (ex:
   `atendpragente-agenda@<projeto>.iam.gserviceaccount.com`) e gerar
   uma **chave JSON** — é o segredo compartilhado que autentica todas
   as chamadas à Calendar API, pra todos os tenants.

Esses três passos são feitos no **Google Cloud Console** (login com
conta Google do usuário) — não dá pra automatizar via CLI sem
autenticação interativa do usuário, mesma natureza do passo manual que
tivemos que fazer pro bucket Hetzner (ver `infra_object_storage_cdn` na
memória do projeto). Depois de gerada a chave JSON, o fluxo de entrega
segue o mesmo padrão já validado nesse projeto: **a chave nunca passa
pelo chat** — o usuário cria o Secret do Kubernetes direto via SSH no
servidor, com um comando que eu forneço.

## Arquitetura: novo serviço `calendar-mcp`

Mesmo padrão do `tools/admin-mcp/` já existente (servidor MCP que o
Hermes consome via `hermes mcp add`), mas:
- **Multi-tenant** (admin-mcp é uso exclusivo da Duda; este aqui é
  consumido por *todos* os Hermes de tenant, cada um com seu próprio
  token).
- **Puramente interno ao cluster** — só os pods de tenant (mesmo
  namespace `atendagente`) precisam alcançá-lo, então é só um
  `ClusterIP` Service, sem Ingress/cert-manager/domínio público (mais
  simples que o admin-mcp, que precisa ser alcançável de fora do
  cluster porque a Duda está noutro servidor).

**Autenticação/roteamento por tenant**: cada tenant ganha um
`CALENDAR_MCP_TOKEN` gerado no provisionamento (mesmo padrão de
`PANEL_SETUP_TOKEN`/`WHATSAPP_CLOUD_VERIFY_TOKEN` em
`build_secret_manifest`), gravado no Secret do tenant e também no
`config` do signup no Mongo (`calendar_mcp_token`). O `calendar-mcp`
recebe o token no header `Authorization: Bearer <token>` (mesmo
mecanismo do admin-mcp), busca no Mongo (`signups`, por
`config.calendar_mcp_token`) qual `tenant_id`/`google_calendar_email`
usar — assim ele não precisa de nenhum outro parâmetro por chamada além
do que o próprio Hermes já manda automaticamente no header.

**Ferramenta exposta** (uma só, de propósito — superfície simples é
mais confiável pro modelo usar certo, ver `feedback_hermes_mcp_tool_calling`
na memória):

```
criar_agendamento(data_hora_inicio_iso: str, titulo: str) -> dict
```
- Lê `google_calendar_email` e `agendamento_duracao_minutos` (default
  30) do tenant (via o token).
- Chama `freebusy.query` na agenda do tenant pro intervalo
  `[data_hora_inicio, data_hora_inicio + duração]`.
- Se **ocupado**: devolve `{"ok": false, "motivo": "horario_ocupado"}`
  — o bot decide como responder (pedir outro horário).
- Se **livre**: cria o evento via `events.insert` com
  `conferenceData.createRequest` (Google Meet automático), devolve
  `{"ok": true, "link_evento": "<htmlLink>", "link_meet": "<hangoutLink>"}`.
- Timezone fixo `America/Sao_Paulo` (mesmo padrão já usado em
  `tools/tenant-panel/app.py`, `SAO_PAULO_TZ`).

## Modelo de dados (novos campos em `signup.config`, mesmo doc do Mongo já usado)

```
config.google_calendar_email: str | None   # e-mail que o tenant compartilhou com a conta de serviço
config.agendamento_ativo: bool             # True só depois de "testar conexão" passar
config.agendamento_duracao_minutos: int    # default 30
config.calendar_mcp_token: str             # gerado no provisionamento, nunca editável pelo tenant
```

## Painel — nova seção "Agenda" (dentro de Configurações, mesmo padrão do Catálogo)

- Campo: e-mail da Google Agenda (com instrução inline: "Compartilhe
  sua agenda com **`atendpragente-agenda@<projeto>.iam.gserviceaccount.com`**
  em Google Agenda → Configurações → Compartilhar com pessoas
  específicas, com permissão 'Fazer alterações nos eventos'").
- Campo: duração padrão do agendamento (minutos).
- Botão "Testar conexão" — chama uma rota nova
  (`POST /painel/api/agenda/testar`) que faz um `freebusy.query` de
  verificação contra o `calendar-mcp` (ou direto a Calendar API, a
  decidir na implementação); se der certo, marca
  `agendamento_ativo=True` e **também seta `soul_pending=True`** (mesmo
  padrão do catálogo: primeira ativação republica o SOUL.md com o
  parágrafo novo).
- **Diferente do catálogo**: não tem fila assíncrona/CSV pra publicar
  — o `calendar-mcp` lê `google_calendar_email` direto do Mongo a cada
  chamada da ferramenta, então salvar no painel já é "publicar". Só o
  SOUL.md (texto fixo explicando a ferramenta) segue o fluxo de fila
  existente.

## SOUL.md — novo bloco condicional `agenda_block`

Mesmo mecanismo do `catalog_block` (`generate_soul.py` +
`SOUL.template.md`), vazio se `config.get("agendamento_ativo")` for
falso, senão algo como:

> ## Agendamento
> Você pode marcar horário na nossa agenda usando a ferramenta
> `criar_agendamento`. Duração padrão: {duração} minutos. Se o horário
> pedido estiver ocupado, avise e peça outro horário — nunca insista
> nem invente disponibilidade. Depois de agendar, sempre mande o link
> do Google Meet na conversa.

## Provisionamento (`provision_tenant.py`)

1. **`calendar_mcp_token` é gerado no onboarding-service**
   (`submit_form` em `main.py`), não no `provision_tenant.py` — precisa
   já estar em `signups.config` **antes** do provisionamento, porque é
   por esse valor que o `calendar-mcp` acha o tenant depois (busca
   `config.calendar_mcp_token` no Mongo). `build_secret_manifest` só lê
   `config.get("calendar_mcp_token")` e grava como `CALENDAR_MCP_TOKEN`
   no Secret do tenant (gera um novo só como fallback pra
   provisionamento manual via CLI direto, fora do wizard).
2. **`hermes mcp add` é interativo** (múltiplos prompts por stdin,
   `getpass` até avisando "Can not control echo on the terminal") —
   frágil demais pra chamar via `subprocess.run(input=...)` numa
   automação sem TTY. Descobri testando manualmente que ele só grava
   duas coisas: um bloco `mcp_servers.<nome>` em `config.yaml` e a
   variável (`MCP_<NOME>_API_KEY`) em `.env`. `enable_calendar_mcp()`
   escreve esses dois arquivos direto (mesmo padrão já usado pra
   SOUL.md/catalogo.csv — `kubectl exec ... cat >> arquivo`), gerando
   exatamente o mesmo formato final, sem depender do fluxo interativo:
   ```yaml
   mcp_servers:
     agenda:
       url: http://calendar-mcp.atendagente.svc.cluster.local:8000/mcp
       headers:
         Authorization: Bearer ${MCP_AGENDA_API_KEY}
       enabled: true
   ```
   Chamado no mesmo passo 5/5 do `provision()`, junto de
   `apply_display_defaults` — registra a ferramenta desde a primeira
   subida do tenant (funciona de verdade só depois que
   `agendamento_ativo=True`, mas registrar cedo evita mais um passo de
   provisionamento manual depois). **Só faz `append`, não é idempotente**
   — não chamar duas vezes no mesmo tenant sem checar antes se o bloco
   já existe.

## Risco técnico do `contextvar` — verificado, funciona (2026-08-16)

`server.py` resolve o tenant no middleware (busca por Bearer token no
Mongo) e guarda num `contextvars.ContextVar` pra a ferramenta
`criar_agendamento` conseguir ler — ela não recebe o `Request`
diretamente, só os argumentos que o modelo passa. Testado de ponta a
ponta no tenant `novo-negocio` via `hermes -z "..."`: o Hermes chamou a
ferramenta de verdade, o `calendar-mcp` resolveu o tenant certo pelo
token e devolveu `{"ok": false, "motivo": "agenda_nao_configurada"}`
(esperado, `google_calendar_email` ainda não configurado nesse tenant).
Confirma que o contextvar chega certo no transporte `streamable_http`
da lib `mcp` — não precisa de plano B.

**Dois bugs reais encontrados só no deploy** (não pegos por
`py_compile`, só em runtime):
1. `TransportSecuritySettings(allowed_origins=None)` — precisa ser
   lista (`[]`), não `None`; `pydantic.ValidationError` travava o
   processo antes de subir.
2. `allowed_hosts` precisa incluir a porta (`calendar-mcp....:8000`,
   não só o hostname) — sem isso, todo request vinha `421 Misdirected
   Request` (a checagem de DNS-rebinding compara a string do Host
   header inteira, e como a porta não é 80/443 ela sempre vem
   explícita no header).

## Limites da v1 (comunicar, não resolver agora)

- Sem checagem de horário comercial — bot agenda em qualquer horário
  livre, mesmo 3h da manhã, se for isso que o cliente pedir.
- Sem cancelamento/reagendamento pelo bot — só cria. Cancelar um
  agendamento errado é manual, direto no Google Agenda.
- Sem convite por e-mail — só o link colado no WhatsApp.
- Um único agendamento por chamada de ferramenta — sem recorrência,
  sem múltiplos participantes.
- Conta de serviço aparece como organizadora do evento (não o dono do
  negócio) — decorrência direta da escolha de autenticação da v1.
- **Google Meet nem sempre é gerado** — confirmado em teste real: conta
  de serviço sem "domain-wide delegation" (não dá pra ter isso fora de
  Google Workspace administrado) não consegue criar conferência via
  API pra contas Gmail pessoais/pequena empresa. `check_and_book()`
  detecta esse erro específico e cria o evento sem Meet em vez de
  falhar o agendamento — `link_meet` vem `null` nesses casos, só
  `link_evento` é garantido.
  **Domain-wide delegation foi avaliado e descartado pra v1**
  (2026-08-16): exige dois passos — habilitar DWD na conta de serviço
  (Cloud Console, `client_id` = `102699687697249785712`,
  `project_id` = `atendpragente-com-br`) **e** autorizar esse
  `client_id` no Admin Console (`admin.google.com`) de um domínio
  Google Workspace administrado. O usuário não tem acesso de admin a
  nenhum domínio Workspace — e a maioria dos tenants reais da
  AtendAgente provavelmente usa Gmail pessoal mesmo, onde DWD nunca
  seria aplicável de qualquer forma (não existe Admin Console numa
  conta `@gmail.com`). Fallback sem Meet é a solução real pra v1, não
  um workaround temporário.

## Arquivos a tocar

- `tools/calendar-mcp/server.py` — novo, servidor MCP multi-tenant
- `tools/calendar-mcp/requirements.txt` — novo (`mcp`, `google-api-python-client`, `google-auth`, `pymongo`)
- `tools/calendar-mcp/setup_calendar_mcp.py` — novo, bootstrap do
  Deployment/Service (sem Ingress, ver acima)
- `tools/tenant-panel/app.py` — rota `/painel/api/agenda` (salvar +
  testar conexão)
- `tools/tenant-panel/templates/configuracoes.html` — seção "Agenda"
- `tools/soul-generator/generate_soul.py` + `SOUL.template.md` — `agenda_block`
- `tools/provision-tenant/provision_tenant.py` — `CALENDAR_MCP_TOKEN` +
  `hermes mcp add` na etapa de provisionamento

## Verificação

1. Criar o projeto GCP + conta de serviço (passo manual do usuário,
   fora do meu alcance).
2. Usuário compartilha uma agenda de teste com a conta de serviço.
3. Deploy do `calendar-mcp`, registrar via `hermes mcp add` num tenant
   de teste (`novo-negocio`, já que não subimos mais tenant fake).
4. Configurar e-mail da agenda em Configurações → Agenda, testar
   conexão, confirmar `agendamento_ativo=True` e SOUL.md republicado.
5. Pedir um horário pelo WhatsApp, confirmar que o evento aparece na
   Google Agenda de teste com Meet, e que o bot manda o link certo na
   conversa.
6. Pedir de novo o mesmo horário — confirmar que o bot detecta conflito
   e não duplica o evento.
