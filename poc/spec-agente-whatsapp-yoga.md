# Spec — Agente de WhatsApp para Estúdio de Yoga

**Versão:** 0.1 (rascunho)
**Canal:** WhatsApp Cloud API (Meta) — oficial
**Objetivo do documento:** definir a arquitetura, as regras de plataforma e o comportamento de um agente de WhatsApp que recebe contatos e trata leads de forma humanizada para um estúdio de yoga, em nível de detalhe suficiente para um desenvolvedor ou agência implementar.

---

## 1. Visão geral

O sistema é um agente conversacional em WhatsApp que atende leads do estúdio de forma humanizada, qualifica o interesse, captura dados, agenda aulas experimentais e faz follow-up de quem não converteu. O canal é a **Cloud API oficial da Meta**, o que elimina o risco de banimento de número e habilita recursos oficiais (recibos de entrega/leitura e templates para reengajamento).

A orquestração fica sob controle do estúdio: a Meta entrega cada mensagem num **webhook** próprio, e o backend decide como responder, chamando um LLM com contexto e as integrações de CRM e agenda.

### 1.1 Objetivos

- Responder contatos novos em segundos, com tom acolhedor e natural.
- Qualificar o lead (objetivo da prática, nível, restrições, preferência de horário).
- Capturar e registrar os dados do lead num CRM ou planilha.
- Agendar aulas experimentais.
- Fazer follow-up de leads frios via template aprovado.
- Passar para um atendente humano quando a conversa sair do escopo.

### 1.2 Não-objetivos (fora de escopo nesta versão)

- Cobrança e pagamento dentro do WhatsApp.
- Emissão de nota fiscal ou contrato.
- Automação de marketing em massa (disparos frios não solicitados).
- Suporte multilíngue (assume-se português do Brasil).

### 1.3 Premissas

- Público e conversas em português (pt-BR).
- Existe um destino de dados para os leads (CRM ou, no mínimo, uma planilha).
- A "conversão" alvo desta versão é o agendamento de uma aula experimental.
- O estúdio tem uma pessoa disponível para receber handoffs em horário comercial.

---

## 2. Arquitetura

Fluxo de uma mensagem, do lead até a resposta:

```
Lead (WhatsApp)
   → Cloud API (Meta)
      → Webhook  [valida assinatura · responde 200 rápido · enfileira]
         → Agente [LLM + contexto + base de conhecimento + memória]
            → integra CRM / agenda
            → decide a saída pela janela de 24h:
                 • dentro de 24h  → resposta livre
                 • fora de 24h    → template aprovado
         → Graph API (POST /{phone-number-id}/messages)
      → Cloud API (Meta)
   → Lead recebe a resposta
```

### 2.1 Componentes

| Componente | Responsabilidade |
|---|---|
| **Canal — Cloud API (Meta)** | Recebe e entrega mensagens do WhatsApp; fonte oficial de eventos. |
| **Webhook receiver** | Valida a assinatura, responde `200` imediatamente, coloca o evento numa fila. |
| **Fila / worker assíncrono** | Processa a mensagem fora do ciclo de request da Meta, garantindo idempotência. |
| **Agente (orquestração + LLM)** | Interpreta a mensagem, consulta contexto e base de conhecimento, decide a ação e gera a resposta. |
| **Memória / estado da conversa** | Guarda histórico e perfil por contato para não repetir perguntas. |
| **Integração CRM / agenda** | Registra o lead, atualiza o estágio e agenda a aula experimental. |
| **Camada de envio (Graph API)** | Envia a resposta (texto livre ou template) de volta ao lead. |

### 2.2 Opções de implementação da orquestração

A Cloud API é agnóstica quanto ao que fica atrás do webhook. Três caminhos válidos:

1. **n8n (low-code)** — nós prontos para webhook e envio; fluxo montado visualmente. Bom para começar rápido com pouca escrita de código.
2. **Backend próprio (Node/Express ou Python/FastAPI)** — controle total; é essencialmente um endpoint que valida, enfileira, chama o LLM e faz o POST de volta.
3. **hermes-agent como cérebro** — o runtime da Nous Research plugado neste webhook oficial em vez do bridge Baileys, aproveitando memória, cron e skills dele com a segurança do canal oficial.

Esta spec descreve o comportamento independentemente da escolha; qualquer uma das três deve satisfazer os mesmos contratos e regras.

---

## 3. Regras da plataforma Meta

Estas regras não são opcionais — moldam o design do sistema.

### 3.1 Janela de atendimento de 24 horas

Dentro de **24 horas** a partir da última mensagem enviada pelo lead, o agente pode responder com **texto livre**. Fora dessa janela, só é possível iniciar contato usando um **template pré-aprovado**.

Consequência de design: todo follow-up de lead que ficou inativo por mais de 24h precisa usar template. O sistema deve rastrear o timestamp da última mensagem do lead por conversa.

### 3.2 Templates de mensagem

Templates precisam ser cadastrados e aprovados pela Meta antes do uso. Categorias:

- **utility** — atualizações e follow-ups transacionais (ex.: confirmar aula experimental).
- **marketing** — reengajamento e promoções.
- **authentication** — códigos de verificação (não usado aqui).

### 3.3 Volume e qualidade

O número tem *messaging tiers* (faixas de volume) que aumentam conforme o histórico e a **qualidade** do número. Excesso de bloqueios/denúncias derruba a qualidade e pode limitar o envio. Design defensivo: só mandar template para quem demonstrou interesse; nunca comprar listas.

### 3.4 Preço

A cobrança é por conversa/mensagem e **o modelo mudou recentemente**. Confirmar os valores atuais na documentação oficial da Meta antes de estimar custo operacional. *(Não fixar números nesta spec.)*

---

## 4. Onboarding / setup (uma vez)

1. **Conta Meta Business + verificação do negócio.** Base de tudo; libera tiers maiores.
2. **App no Meta for Developers** com o produto WhatsApp adicionado.
3. **Número dedicado** registrado na WhatsApp Business Account (WABA). Guardar o `Phone Number ID` e o `WABA ID`.
4. **Token permanente** via System User (não o token temporário de teste, que expira em 24h).
5. **Webhook**: URL pública de callback + `verify token` próprio; assinar o campo `messages` (e `message_status` para recibos).
6. **Aprovação do nome de exibição** do número.

---

## 5. Contratos de integração

### 5.1 Verificação do webhook (GET)

A Meta chama a URL com `hub.mode`, `hub.verify_token` e `hub.challenge`. O endpoint deve:

- Comparar `hub.verify_token` com o valor configurado.
- Responder o `hub.challenge` em texto puro com `200` se bater; `403` caso contrário.

### 5.2 Recebimento de mensagens (POST)

- **Responder `200` imediatamente** (antes de qualquer processamento pesado). A Meta reenvia se demorar → processamento vai para a fila.
- **Validar a assinatura** `X-Hub-Signature-256` (HMAC-SHA256 do corpo bruto com o App Secret). Rejeitar se não bater — sem isso, qualquer um que descobrir a URL injeta mensagens falsas.
- **Idempotência**: deduplicar por `message.id`, pois reenvios podem duplicar eventos.

Estrutura relevante do payload (resumo): `entry[].changes[].value.messages[]` (mensagens recebidas) e `entry[].changes[].value.statuses[]` (recibos de entrega/leitura).

### 5.3 Envio de mensagens (Graph API)

- `POST /{phone-number-id}/messages`
- Texto livre (dentro da janela de 24h): `type: "text"`.
- Template (fora da janela): `type: "template"` com nome e parâmetros do template aprovado.
- Autenticação via token permanente no header `Authorization: Bearer`.

---

## 6. Modelo de dados (esboço)

**Lead**
- `id`, `telefone`, `nome`
- `estagio` (ver máquina de estados)
- `objetivo` (ex.: estresse, flexibilidade, condicionamento)
- `nivel` (iniciante / intermediário / avançado)
- `restricoes` (texto livre; ex.: lesão, gravidez)
- `preferencia_horario`
- `origem` (ex.: Instagram, indicação)
- `criado_em`, `atualizado_em`

**Conversa**
- `id`, `lead_id`
- `ultima_msg_lead_em` (timestamp que controla a janela de 24h)
- `janela_aberta` (derivado)
- `atendente_humano` (booleano — em handoff?)

**Mensagem**
- `id` (usar o `message.id` da Meta para dedupe), `conversa_id`
- `direcao` (in / out)
- `tipo` (text / template / media)
- `conteudo`, `status` (sent / delivered / read / failed), `timestamp`

**Agendamento**
- `id`, `lead_id`, `data_hora`, `modalidade`, `status` (marcado / confirmado / realizado / faltou)

---

## 7. Tratamento de leads — máquina de estados

```
novo → em_qualificacao → qualificado → experimental_agendada → convertido
                              ↓                    ↓
                          perdido            nao_compareceu → (re-follow-up)
```

- **novo** — primeiro contato recebido.
- **em_qualificacao** — agente descobrindo objetivo, nível, restrições e preferência de horário.
- **qualificado** — dados suficientes capturados; interesse confirmado.
- **experimental_agendada** — aula experimental marcada na agenda.
- **convertido** — virou aluno/matrícula.
- **perdido** — sem interesse ou sem resposta após tentativas de follow-up.
- **nao_compareceu** — faltou à experimental; entra em nova rotina de follow-up.

### 7.1 Roteiro de qualificação (diretriz, não script rígido)

1. Acolher e identificar o objetivo da pessoa.
2. Descobrir nível e eventuais restrições físicas.
3. Apresentar a modalidade que encaixa.
4. Oferecer a aula experimental e capturar dia/horário de preferência.
5. Confirmar o agendamento e registrar no CRM/agenda.

### 7.2 Follow-up

- Leads inativos > 24h só recebem contato via **template** (ver 3.1/3.2).
- Limitar tentativas para preservar a qualidade do número (ex.: no máx. 2–3 toques espaçados antes de marcar como `perdido`).

---

## 8. Humanização

Requisitos de comportamento que fazem o agente parecer humano e confiável:

- **System prompt e base de conhecimento** com os dados reais do estúdio (horários, modalidades, preços, professores, endereço). O agente **nunca inventa** horário ou preço; quando não sabe, oferece transferir para uma pessoa.
- **Quebra de mensagens**: respostas longas em mensagens curtas, em vez de um textão único.
- **Agrupamento de rajada (message batching)**: se o lead manda várias mensagens seguidas, aguardar um curto período de silêncio (ex.: ~5s) e tratá-las como uma só, evitando respostas picotadas.
- **Delay de digitação** simulado, quando o canal/stack permitir.
- **Continuidade**: usar o nome e o histórico; não recomeçar do zero a cada mensagem.
- **Handoff humano**: ao detectar reclamação, dúvida sensível, pedido fora do escopo ou pedido explícito, avisar e transferir. O estado `atendente_humano` silencia o agente naquela conversa.
- **Modo copiloto / rascunho**: em produção inicial, o agente sugere a resposta e um humano aprova antes de enviar, para calibrar o tom antes do modo automático.

---

## 9. Segurança e conformidade

- **Validação de assinatura** em todo POST do webhook (seção 5.2).
- **Proteção de credenciais**: token permanente e App Secret em cofre de segredos, nunca em código versionado.
- **Guardrails do LLM**: instruções explícitas contra inventar informações e contra sair do tom do estúdio; sanitização de entradas para reduzir prompt injection via conteúdo do usuário.
- **LGPD**: os dados dos leads são pessoais. Definir base legal (interesse do titular ao iniciar contato), permitir opt-out ("não quero mais receber mensagens") e prever retenção/eliminação. Registrar consentimento quando aplicável.

---

## 10. Requisitos não-funcionais

- **Latência**: primeira resposta em poucos segundos após a mensagem do lead.
- **ACK do webhook**: `200` em milissegundos; processamento sempre assíncrono.
- **Idempotência**: dedupe por `message.id` (reenvios da Meta).
- **Disponibilidade**: o webhook precisa estar sempre no ar; usar retry/fila para picos.
- **Observabilidade**: logs de mensagens, status de entrega e falhas; painel do funil de leads por estágio.
- **Escala**: respeitar e acompanhar o messaging tier do número.

---

## 11. Templates a cadastrar (exemplos)

Textos a submeter para aprovação (parâmetros entre chaves):

- **Follow-up de lead frio (utility)**: "Oi {{1}}! Aqui é do {{2}}. Sua aula experimental de yoga ainda está de pé? Posso te encaixar num horário essa semana. 🙏"
- **Confirmação de experimental (utility)**: "Oi {{1}}, confirmando sua aula experimental {{2}} às {{3}}. Chega uns 10 min antes, roupa confortável. Qualquer coisa, é só responder aqui."
- **Lembrete véspera (utility)**: "Oi {{1}}, sua aula experimental é amanhã às {{2}}. Te espero! Precisa reagendar?"

*(Textos finais dependem da aprovação da Meta e da voz da marca.)*

---

## 12. Decisões em aberto

- Qual CRM/ferramenta de agenda será a fonte de verdade?
- Rodar a orquestração em n8n, backend próprio ou hermes-agent?
- Modelo de LLM (frontier via API vs. open-weight self-hosted) — trade-off custo/privacidade/qualidade.
- Horário de cobertura do handoff humano e comportamento fora desse horário.
- Política exata de follow-up (nº de toques e intervalos).

---

## Glossário

- **WABA** — WhatsApp Business Account, a conta que agrupa o número na plataforma.
- **Cloud API** — API oficial da Meta para o WhatsApp, hospedada pela própria Meta.
- **Janela de 24h** — período após a última mensagem do lead em que se pode responder texto livre.
- **Template (HSM)** — mensagem pré-aprovada, exigida para iniciar contato fora da janela de 24h.
- **Phone Number ID / WABA ID** — identificadores usados nas chamadas à Graph API.
- **Messaging tier** — faixa de volume de envio do número, ligada à qualidade.
- **Handoff** — transferência da conversa do agente para um atendente humano.
