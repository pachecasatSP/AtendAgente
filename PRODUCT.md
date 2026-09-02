# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primário: donos/gestores de pequenos negócios brasileiros (ex.: estúdios, lojas, prestadores de serviço) que contratam a AtendPraGente para atender clientes no WhatsApp. Situação: sem equipe dedicada a atendimento, precisam responder pedidos, dúvidas de catálogo e agendamentos rapidamente sem contratar mais gente. Job: configurar e operar um agente de atendimento automatizado sem precisar programar.

Secundário: o cliente final do tenant, que interage exclusivamente por conversa de WhatsApp com o bot (não usa telas do produto) para tirar dúvidas, ver catálogo, pedir, agendar e pagar.

## Product Purpose

A AtendPraGente é uma plataforma multi-tenant que provê um agente de atendimento com IA operando dentro do WhatsApp de cada negócio cliente. Ela substitui atendimento manual por um bot que conversa com a persona/voz do negócio (definida em um "SOUL"), apresenta catálogo, recebe pedidos, agenda horários (Google Calendar) e processa pagamento via Pix, com um painel de operação para o dono do negócio acompanhar e configurar tudo isso. Sucesso = o dono do negócio consegue operar o atendimento inteiro pelo WhatsApp sem intervenção manual constante, e o cliente final consegue resolver sua necessidade (comprar, agendar, tirar dúvida) só conversando.

## Positioning

Agente de atendimento no WhatsApp com IA para pequenos negócios brasileiros: catálogo, pedidos, agendamento e pagamento (Pix) acontecem dentro da própria conversa, sem app ou site separado para o cliente final usar. Diferente de um chatbot genérico de FAQ, o bot age (cria pedido, agenda, cobra) e é operado por um Tech Provider aprovado pela Meta, com onboarding self-service (WhatsApp Embedded Signup) para novos tenants.

## Operating Context

- O bot roda sobre o framework "Hermes" (hermes-agent), conectado à WhatsApp Cloud API (não Baileys), hospedado em Kubernetes (K3s) na Hetzner.
- Cada tenant tem sua própria persona (arquivo SOUL.md), catálogo, agenda e número de WhatsApp business (WABA), provisionados via fluxo de onboarding.
- Superfícies visuais do repositório, todas web:
  - **Landing page institucional** (`poc/landing-page`) — página de marketing/vendas da AtendPraGente, modo Persuade.
  - **Painel do tenant** (`tools/tenant-panel`) — onde o dono do negócio configura catálogo, agenda, SOUL, acompanha pedidos e conversas, modo Operate.
  - **Onboarding self-service** (`tools/onboarding-service`) — fluxo de cadastro de novo tenant, incluindo Embedded Signup do WhatsApp e pagamento, modo Operate/Persuade híbrido.
  - Outras ferramentas internas (`admin-mcp`, `ai-assist`, `calendar-mcp`, `provision-tenant`, `soul-generator`) são de operação/infra, não superfícies voltadas ao usuário final do produto.
- Cliente final nunca acessa uma tela do produto — toda a experiência dele é dentro do app WhatsApp.

## Capabilities and Constraints

- Catálogo de produtos, agendamento via Google Calendar, checkout/cobrança via Pix (Asaas), status de entrega, escalação para humano (handoff) já implementados e em produção para tenants reais.
- Multi-tenant: cada negócio tem SOUL, catálogo e WABA próprios; provisionamento é um processo semi-automatizado (`tools/provision-tenant`).
- Modelo de LLM é configurável por tenant (pinado explicitamente na config — já houve incidente de modelo default caro rodando sem intenção).
- Nome comercial ao usuário final é "AtendPraGente"; "Hermes" é o codinome interno do framework/infra e não deve vazar para o usuário.
- Idioma do produto e de todas as superfícies é português do Brasil.

## Brand Commitments

- Nome comercial: **AtendPraGente** (domínio atendpragente.com.br), distinto do codinome interno "Hermes".
- Tokens de marca (fontes, cores, logo SVG) já extraídos do site ao vivo — reutilizar em vez de re-derivar (ver memória de projeto / DESIGN.md quando existir).
- Tom de voz do bot de atendimento é definido por tenant via SOUL.md (ex.: "carioca informal-competente" era o tom do primeiro tenant/Ac Soluções); o tom das superfícies do produto (landing, painel) em si ainda não tem DESIGN.md registrado.

## Evidence on Hand

- Site institucional ao vivo em www.atendpragente.com.br (Netlify, deploy manual).
- Testes reais de carga em tenants sandbox/produção confirmaram uso de recursos (~377-421m CPU / ~260Mi pico) — não é apenas protótipo, há uso real.
- Não fabricar cases, depoimentos ou métricas de clientes além dos tenants reais já conhecidos (ex.: linda-ana-calcados, novo-negocio) sem confirmação explícita.

## Product Principles

1. A conversa de WhatsApp é o produto para o cliente final — nenhuma funcionalidade essencial deve exigir sair do WhatsApp.
2. O painel e o onboarding existem para que o dono do negócio (não-técnico) configure e opere o bot sozinho, sem suporte manual constante.
3. Cada tenant tem identidade própria (SOUL, catálogo, número) — nada de misturar dados/config entre tenants.
4. "Hermes" é implementação interna; "AtendPraGente" é a marca que o usuário vê.
5. Mudanças em superfícies voltadas ao cliente final devem ser confirmadas em produção (não só em teste local) antes de serem consideradas concluídas.

## Accessibility & Inclusion

Nenhum requisito específico de acessibilidade foi confirmado ainda além de boas práticas padrão da web.
