---
name: AtendPraGente — Landing Page
description: Landing institucional que vende o agente de atendimento no WhatsApp com IA, com o mockup de chat como peça central.
colors:
  mata-atlantica: "#1FAE5C"
  mata-atlantica-profunda: "#0E7A3D"
  mata-atlantica-clara: "#E3F7EA"
  sol-da-tarde: "#FFC93C"
  sol-da-tarde-intenso: "#E8A400"
  marfim: "#FFFCF5"
  marfim-aquecido: "#FFF6E2"
  branco-cartao: "#FFFFFF"
  tinta: "#17231D"
  tinta-suave: "#4A5A50"
  tinta-fraca: "#54635A"
  linha: "#E4E0D0"
typography:
  display:
    fontFamily: "'Baloo 2', ui-rounded, 'Segoe UI Rounded', sans-serif"
    fontWeight: 800
    letterSpacing: "-0.01em"
  body:
    fontFamily: "'Nunito', ui-rounded, 'Segoe UI', sans-serif"
    fontWeight: 500
    lineHeight: 1.6
  label:
    fontFamily: "'Nunito', ui-rounded, 'Segoe UI', sans-serif"
    fontWeight: 800
    fontSize: "0.8rem"
    letterSpacing: "0.08em"
rounded:
  md: "20px"
  lg: "28px"
  pill: "999px"
spacing:
  section: "4.5rem"
  card-padding: "1.6rem"
  grid-gap: "1.4rem"
components:
  button-primary:
    backgroundColor: "{colors.sol-da-tarde}"
    textColor: "#211500"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "1rem 1.7rem"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.tinta}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "0.95rem 1.6rem"
  nav-cta:
    backgroundColor: "{colors.mata-atlantica}"
    textColor: "#FFFFFF"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "0.65rem 1.3rem"
  card:
    backgroundColor: "{colors.branco-cartao}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.md}"
    padding: "1.8rem"
  price-card-featured:
    backgroundColor: "{colors.branco-cartao}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.lg}"
    padding: "2rem 1.8rem"
---

# Design System: AtendPraGente — Landing Page

## Overview

**Creative North Star: "A Conversa que te Ajuda"**

Tudo na página existe para fazer o mockup de chat do WhatsApp parecer real e confiável — a conversa é o herói, o resto é moldura. O tom é caloroso, direto e brasileiro: títulos em Baloo 2 (arredondada, peso 800) soam como alguém falando animado, não como um deck corporativo; a copy é coloquial ("Cê dorme. Seu WhatsApp atende, agenda e vende.") em vez de jargão de SaaS. A paleta combina verde-mata-atlântica (confiança, "oficial", vivo) com um amarelo sol-da-tarde (ação, calor) sobre um fundo marfim quente — nunca o branco/cinza frio de dashboard de software. Formas são macias e convidativas: pílulas completas em botões e badges, cantos bem arredondados em cartões (20–28px), nada de esquinas vivas ou linhas duras.

**Anti-referência confirmada:** SaaS corporativo frio — paletas azul/cinza genéricas, cartões retos, tom "enterprise" impessoal. A escolha de verde+marfim+amarelo e tipografia arredondada é deliberadamente o oposto disso.

**Key Characteristics:**
- O balão de chat do WhatsApp é a peça central; toda seção existe para sustentar essa demonstração.
- Tipografia display arredondada e informal (Baloo 2), nunca séria/corporativa.
- Formas em pílula (999px) dominam botões, badges e chips — praticamente nenhum canto reto no sistema.
- Elevação é reservada a poucos elementos de destaque, nunca decoração padrão.
- Um único bloco de seção (features-band) inverte para fundo verde sólido, deliberadamente fixo em ambos os temas — não segue o dark mode.

## Colors

Paleta quente centrada em verde-mata-atlântica e marfim, com amarelo-sol-da-tarde como único acento de ação. Todos os tokens têm um par claro/escuro definido via `prefers-color-scheme` e um atributo `data-theme` manual; os valores abaixo são o tema claro (canônico).

### Primary
- **Mata Atlântica** (#1FAE5C): cor de marca e confiança — CTA do menu, bordas ativas, ícones de status, o "verde oficial" do WhatsApp reinterpretado. Escuro: #35C878.
- **Mata Atlântica Profunda** (#0E7A3D): texto de destaque sobre fundo claro (eyebrows, preço), e é também o fundo sólido e fixo da seção `features-band` nos dois temas.
- **Mata Atlântica CTA** (token `--leaf-btn`; claro #0E7A3D, escuro #207949): fundo de qualquer elemento que carrega texto branco em cima (nav CTA, avatar do chat, botão de aceitar cookies). *Adicionado em 2026-09-02:* não é um alias de Mata Atlântica Profunda — no tema escuro, Profunda vira um verde-menta claro (papel de texto), então usá-la como fundo de botão quebraria o contraste; o CTA precisa do próprio valor escuro por tema.
- **Mata Atlântica Clara** (#E3F7EA): preenchimento de marcadores de lista dentro do círculo de check dos planos.

### Secondary
- **Sol da Tarde** (#FFC93C): único acento de ação — botão primário (CTA principal), selo "mais escolhido", faixa de trial. Escuro: #FFD666.
- **Sol da Tarde Intenso** (#E8A400): variante mais escura do acento, hover states e texto sobre `--spark`.

### Neutral
- **Marfim** (#FFFCF5): fundo padrão da página — nunca branco puro, sempre com esse leve calor. Escuro: #0F1B15.
- **Marfim Aquecido** (#FFF6E2): fundo de blocos internos (CTA final, tag de mockup). Escuro: #16261D.
- **Branco Cartão** (#FFFFFF): fundo de cartões sobre o marfim (steps, price cards, mockups). Escuro: #182920.
- **Tinta** (#17231D): texto principal. Escuro: #F3F6F1.
- **Tinta Suave** (#4A5A50): texto secundário/lead. Escuro: #C9D6CC.
- **Tinta Fraca** (#54635A): texto terciário/legendas. Escuro: #98A49C. *(Corrigido em 2026-09-02: o valor original #8A9690/#7C8B81 ficava abaixo de 4.5:1 em quase todo uso real — labels, preço riscado, rodapé — e foi escurecido/clareado por tema até passar em WCAG AA.)*
- **Linha** (#E4E0D0): toda borda de 1px do sistema. Escuro: #24352B.

### Named Rules
**The One Accent Rule.** Sol da Tarde (amarelo) é usado só em CTAs primários e no que precisa de atenção imediata — nunca como cor decorativa de fundo em áreas grandes. Sua raridade é o que faz o botão de ação saltar aos olhos.

**The Fixed Band Rule.** A seção `features-band` (fundo #0E7A3D sólido) é a única área que ignora o tema claro/escuro do usuário — ela carrega seu próprio par fundo/texto branco para garantir contraste, porque é pensada como um "cartaz" dentro da página, não como uma superfície que respira com o tema.

## Typography

**Display Font:** Baloo 2 (com fallback `ui-rounded, 'Segoe UI Rounded', sans-serif`)
**Body Font:** Nunito (com fallback `ui-rounded, 'Segoe UI', sans-serif`)

**Character:** Baloo 2 é arredondada e cheia de personalidade — usada em todo título (h1/h2/h3) sempre em peso 800, dando um tom animado e acessível. Nunito no corpo mantém peso médio-alto (500 como base, 800 em CTAs e labels), então mesmo o texto corrido tem presença, sem parecer fino ou frágil.

### Hierarchy
- **Display/Hero** (800, `clamp(2.4rem, 5vw, 3.6rem)`, line-height 1.05): título principal do hero, com o trecho de destaque em Mata Atlântica Profunda.
- **Headline** (800, `clamp(1.9rem, 3.2vw, 2.5rem)`): título de cada seção.
- **Eyebrow/Label** (800, 0.8rem, letter-spacing 0.08em, uppercase): rótulo curto acima de cada título de seção, sempre em Mata Atlântica Profunda.
- **Body** (500, ~1rem–1.15rem, line-height 1.6, max ~46–60ch): parágrafo de lead e texto de seção.
- **CTA/Button label** (800, ~0.95–1.05rem): texto de botão, sempre em Nunito peso 800, nunca em Baloo 2.

### Named Rules
**The Bold-or-Nothing Rule.** Baloo 2 só aparece em peso 800 — não existe uma variante leve/regular dela no sistema. Se o texto precisa de menos peso visual, ele vira Nunito, não Baloo 2 mais fina.

## Layout

Container central (`.wrap`) com `max-width: 1120px` e padding lateral de `1.5rem`. Seções têm respiro generoso (`padding: 4.5rem 0`), com o cabeçalho de seção (`.section-head`) limitado a `60ch` para manter a leitura curta antes do conteúdo em grade.

Grades usam `repeat(N, 1fr)` com colapso responsivo em breakpoints próprios por seção (900px para o hero de 2 colunas, 800–900px para steps/mockups em 3–4 colunas, 560–1100px para os planos de 4 colunas) — cada bloco define seu próprio ponto de quebra em vez de um breakpoint global único, e todos colapsam para coluna única em telas pequenas. Gaps variam por densidade do conteúdo, de `1.4rem` (grades de cartão) a `3.5rem` (hero de duas colunas).

## Elevation & Depth

Sistema majoritariamente flat: a maioria dos cartões (steps, feature cards, trust pills, price cards padrão) usa apenas uma borda de 1px em `--line`, sem sombra. Sombra (`--shadow: 0 20px 50px -20px rgba(23,35,29,0.25)`, mais escura no tema escuro) é reservada a um punhado de elementos que precisam parecer "levantados": o mockup do celular/chat, o card de plano em destaque (`.featured`) e as imagens dentro dos mockup-cards. Botões usam sombras coloridas e curtas (`box-shadow` tingido na própria cor do botão) só no CTA primário e no CTA do menu, como reforço de "isso é clicável e importante", não como padrão de todo botão.

### Shadow Vocabulary
- **`--shadow`** (`0 20px 50px -20px rgba(23,35,29,0.25)`; escuro: `rgba(0,0,0,0.6)`): elevação de destaque — mockup do celular, plano em destaque, imagens de mockup.
- **CTA glow** (`box-shadow` tingido via `color-mix` na cor do próprio botão, ex. `0 14px 30px -12px color-mix(in srgb, var(--spark) 75%, transparent)`): reforço de ação nos botões primário e CTA do menu.

### Named Rules
**The Earned Shadow Rule.** Sombra é sinal de importância, não decoração padrão — se um elemento tem sombra, é porque ele precisa competir visualmente com o resto da página (mockup do produto, plano recomendado, CTA principal).

## Shapes

Formas macias e convidativas em todo o sistema. Botões, badges, chips e pills usam raio total (`999px` — pílula completa). Cartões usam dois raios grandes: `20px` (steps, feature cards, mockup cards) e `28px` (phone mockup, price cards, bloco de CTA final) — quanto mais "protagonista" o elemento, maior o raio. Bordas são sempre finas (1px, cor `--line`), nunca decorativas ou grossas. Bolhas de chat usam `border-radius: 18px` com o canto voltado para a "origem" da mensagem achatado a 4px (efeito de balão real).

## Components

### Buttons
- **Shape:** pílula completa (`border-radius: 999px`).
- **Primary:** fundo Sol da Tarde, texto quase-preto (#211500), peso 800, padding `1rem 1.7rem`, sombra tingida na própria cor.
- **Secondary:** transparente, borda dupla de 2px em `--line`, texto Tinta; hover troca a borda para Mata Atlântica.
- **Nav CTA:** variante compacta do primary mas em Mata Atlântica CTA/`--leaf-btn` (não Sol da Tarde, não a Mata Atlântica clara `--leaf`) — reserva o amarelo para a decisão de conversão principal e usa o verde escuro-o-suficiente pra manter texto branco em 4.5:1+, não para navegação.
- **Hover / Focus:** `translateY(-1px|-2px)` + leve scale no primary; foco visível com outline de 3px em Sol da Tarde e offset de 3px em todo elemento interativo.

### Cards / Containers
- **Corner Style:** 20px (padrão) ou 28px (destaque) — ver Shapes.
- **Background:** Branco Cartão sobre fundo Marfim; Feature cards dentro da `features-band` usam branco translúcido (`rgba(255,255,255,0.08)`) em vez do token de cartão, porque vivem sobre o fundo verde fixo, não sobre o fundo do tema.
- **Shadow Strategy:** flat por padrão; ver Elevation & Depth para as exceções de destaque.
- **Border:** 1px em `--line` (ou `rgba(255,255,255,0.16)` dentro da features-band).
- **Internal Padding:** `1.6rem`–`2rem`, com `1.8rem` em price cards.

### Chips / Pills
- **Trust pill:** fundo Branco Cartão, borda 1px, um ponto colorido (`--leaf`) à esquerda, texto Tinta Suave peso 700.
- **Mockup tag:** pill pequena em maiúsculas, fundo Marfim Aquecido, texto Tinta Fraca — rótulo discreto no rodapé de cada mockup.
- **Trial tag:** dentro da faixa de trial, fundo quase-preto (#211500) com texto Sol da Tarde — inversão de contraste para se destacar dentro do próprio banner amarelo.

### WhatsApp Chat Mockup (componente-assinatura)
O coração visual da página. Um "telefone" (`.phone`, 28px de raio, sombra de destaque, largura máx. 380px) contém um cabeçalho com avatar circular verde e nome verificado, seguido de um thread de balões que aparecem em sequência animada (`animation-delay` escalonado, com indicador de "digitando..." antes da resposta). Balões recebidos são brancos com borda; balões enviados usam um verde-claro próprio (`--bubble-out: #DCFCE3`, escuro: `#17421F`) que não é o mesmo tom de nenhum outro token — é a única cor do sistema dedicada exclusivamente a essa metáfora de "mensagem minha". Respeita `prefers-reduced-motion` removendo toda animação.

### Navigation
- Barra fixa no topo (`position: sticky`), fundo semitransparente com `backdrop-filter: blur(10px)` sobre o Marfim, borda inferior de 1px.
- Links de texto peso 800, cor Tinta Suave, hover para Mata Atlântica Profunda; CTA à direita sempre em formato pílula, fundo Mata Atlântica CTA (`--leaf-btn`).

### FAQ Accordion
Usa `<details>`/`<summary>` nativo (acessível via teclado sem JS). Cartão flat com borda 1px e raio 20px, igual ao Step Card; `summary` em peso 800 com um indicador `+`/`–` em Mata Atlântica Profunda à direita, sem o marcador padrão do navegador. Corpo da resposta em Tinta Suave, 0.95rem.

## Do's and Don'ts

### Do:
- **Do** manter o mockup de chat do WhatsApp como o elemento visual mais elaborado da página — nenhum outro componente deve competir com ele em complexidade ou centralidade.
- **Do** usar pílula (999px) para todo elemento clicável/badge; reservar 20px/28px só para superfícies de conteúdo (cartões, contêineres).
- **Do** reservar Sol da Tarde para o único CTA de conversão mais importante de cada seção — nunca espalhar o amarelo em múltiplos elementos da mesma tela.
- **Do** manter `--bubble-out` como cor exclusiva do balão de mensagem enviada — não reutilizar esse verde-claro em outro componente.

### Don't:
- **Don't** usar `--leaf` (Mata Atlântica clara) como fundo de texto branco — use `--leaf-btn` (Mata Atlântica CTA), que tem um valor escuro-o-suficiente por tema para passar 4.5:1. `--leaf` é só pra bordas, pontos decorativos e traços finos.
- **Don't** introduzir azul, cinza frio ou qualquer paleta "SaaS enterprise" — é a anti-referência explícita deste sistema.
- **Don't** adicionar sombra decorativa em cartões comuns (steps, feature cards, trust pills); sombra é reservada aos elementos de destaque listados em Elevation & Depth.
- **Don't** usar Baloo 2 em peso diferente de 800, nem em blocos longos de texto corrido — ela é só para títulos curtos.
- **Don't** deixar a `features-band` reagir ao tema claro/escuro — seu par fundo/texto é fixo por design.
- **Don't** usar branco translúcido abaixo de 90% de opacidade (`rgba(255,255,255,0.9+)`) pra texto de corpo dentro da `features-band` — abaixo disso o contraste contra o fundo `#0E7A3D` cai abaixo de 4.5:1. *(Corrigido em 2026-09-02: o texto secundário e dos feature cards estava em 0.78/0.85, dando ~3.96:1/4.39:1; subiu pra 0.92 em ambos, ~4.85:1.)*
- **Don't** deixar um elemento interativo novo fora do seletor `a:focus-visible, button:focus-visible, summary:focus-visible` — todo controle focável por teclado usa o mesmo anel de foco em Sol da Tarde; um elemento fora da lista fica com o anel padrão do navegador, quebrando a consistência.
