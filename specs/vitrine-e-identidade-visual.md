# Vitrine pública e identidade visual da plataforma

> Estado (2026-11-04): redesenhadas, na ordem: **vitrine** (PR #6), **login**, **criação de usuário**, **painel** (conversas), **agenda, pedidos e configurações** e **onboarding** (seções abaixo). Cada seção diz o que foi verificado no navegador e o que não foi. O deploy é por PR + `implantar` (aprovado pelo dono); o que está em produção depende do último `implantar` aprovado. Complementa `catalogo-produtos.md` (modelo de dados, import, fotos) e `infraestrutura-aws-atendpragente.md`.

## Contexto

A vitrine (`GET /vitrine`, pública) é a única tela que o **cliente final da loja** vê, quase sempre aberta pelo WhatsApp, no celular. Um levantamento da vitrine real da `linda-ana-calcados` (2026-11-02, desktop 1280 px) achou:

1. **Identidade errada na tela da loja:** verde "folha", fundo creme e "by atendpragente" no lugar da marca de uma loja de calçados.
2. **Preço em dois formatos na mesma página:** lista `R$ 990.00` (Jinja `"%.2f"|format`), carrinho `R$ 990,00`.
3. **Caixa de rolagem dentro de cada card** (`.item-descricao-scroll`, 7,5 rem fixos).
4. **Descrição igual ao nome** (o import copiava `nome` para `descricao`); problema de dado, escondido agora na tela.
5. **Coluna estreita** (máx. 760 px, à esquerda, 2 cards por linha).
6. **Fontes da marca ausentes:** `login`, `index` e `signup` embutem Baloo 2 e Nunito em base64 (~75 KB cada); a vitrine não carregava nenhuma e caía numa sans genérica que muda por aparelho.
7. **Emojis como interface** (📦 🛒 📅 🕒), `alert()` para erro, contorno de texto no `h1`, botão branco sobre `#1FAE5C` (contraste 2,9:1 por cálculo).

Quase não havia gradiente, vidro ou brilho (medido por `grep`): o problema era layout, hierarquia, dados e copy.

## Decisões do dono (2026-11-02)

- A identidade segue a **da plataforma**. Em breve será construído um **motor de customização da loja** para vitrine, painel, login, criação de usuário e onboarding.
- Por isso o visual sai em **tokens de duas camadas**; o motor troca só a camada semântica.
- **Layout da Linda Ana: `lista`.** O outro layout, `grade`, existe para lojas em que a foto vende.
- Começar pela vitrine. Os chips de categoria no topo (do ensaio "editorial") ficam.

## Tokens

| Camada | Tokens | Observação |
|---|---|---|
| Paleta da plataforma (inalterada) | `--paper`, `--paper-card`, `--paper-soft`, `--leaf-pale`, `--ink`, `--ink-soft`, `--ink-faint`, `--line`, `--leaf`, `--leaf-deep`, `--spark` | `--ink-faint` clareado para `#6B776F` no claro (contraste ~4,7:1) e `#93A398` no escuro |
| Semântica (o motor mexe aqui) | `--fundo`, `--superficie`, `--texto`, `--texto-suave`, `--borda`, `--acao`, `--acao-texto`, `--acao-suave`, `--foco`, `--erro`, `--fonte-titulo`, `--fonte-corpo`, `--raio-cartao`, `--raio-controle` | `--acao` = `--leaf-deep` (texto branco: 5,9:1); no escuro `--acao` = `#35C878` com texto escuro |

Modo escuro mantido (`prefers-color-scheme` e `data-theme`). Verde só na ação principal e nos estados; título, categorias e preço usam a tinta.

## Layout

`data-layout="lista" | "grade"` no `<main id="vitrine">`. Ordem de resolução no servidor: `?layout=` (para testar) > `signups.config.vitrine_layout` (onde o motor de customização vai gravar) > `lista`. Valor inválido cai em `lista`.

- **Lista:** miniatura (84 px; 104 px a partir de 760 px), nome em até 3 linhas, preço, botão "+" de 44 px. Desktop em duas colunas de cards de altura igual por linha. Sem foto nenhuma na loja (ex.: restaurante): a coluna da miniatura some.
- **Grade:** 2 colunas no celular, `auto-fill minmax(220px, 1fr)` a partir de 720 px, foto quadrada com `object-fit: contain` sobre branco.
- Item sem foto numa loja que tem fotos: iniciais (`iniciais`) sobre `--acao-suave`.
- **Chips de categoria** fixos no topo (um `<nav>` por tipo, só quando o tipo tem mais de uma categoria), com o chip ativo acompanhando a rolagem (`IntersectionObserver`); o título da categoria fica fixo logo abaixo (`--topo-cat`).
- **Abas por tipo** (produto, evento, serviço) só quando há mais de um tipo, como antes.
- Descrição: omitida se igual ao nome; até 90 caracteres inline; acima disso, `<details>` ("Ver detalhes").

## Contrato preservado (não mudou)

Rota e contexto (`secoes`, `tipos_compraveis`, `nome_negocio`, `tenant_id`); item sempre com `id` string; carrinho de **1 unidade por item** (alterna adicionar/remover); `localStorage` com a chave `atendpragente_carrinho_<tenant_id>` (preservada para não perder carrinho de quem já comprava); `POST /vitrine/api/pedido` com `{"item_ids": [...]}` e redirecionamento para `wa_link`; serviço não é comprável (sem botão); evento mostra a data (`data_br`), serviço mostra o horário.

## O que mudou no servidor

- Filtros Jinja `brl` (`R$ 1.234,50`, igual ao JS) e `iniciais`.
- Contexto novo: `layout`, `contato_wa` (só calculado com a loja vazia, para o botão "Falar com a loja"), `total_itens`.
- **Rota `GET /vitrine/fonte/{nome}`:** serve Baloo 2 e Nunito de `tools/tenant-panel/static/fontes/` com `Cache-Control: public, max-age=31536000, immutable`. **Lista fixa de dois nomes** (nunca monta caminho a partir da entrada; `../`, `%2e%2e`, `\` dão 404). Funciona sem mexer em ingress: o ingress de todo tenant já manda `/vitrine` (Prefix) ao painel. No modo `admin` (Duda) é bloqueada pelo prefixo `/vitrine`.
- **Rolagem infinita em lotes de 12 removida.** Os itens escondidos quebravam os chips (saltar para uma categoria cujos itens ainda não existiam na tela). Todos os itens renderizam; as imagens seguem `loading="lazy"`. **Risco aceito:** catálogos com centenas de itens geram HTML grande; reavaliar se aparecer tenant assim.
- `alert()` virou mensagem inline na folha do pedido ("Seus itens continuam aqui; tente de novo.").

## Estados cobertos

Normal, loja vazia (com CTA de WhatsApp só se houver número), item sem foto, loja sem nenhuma foto, tipos mistos, pedido em envio ("Preparando…"), erro de pedido (itens mantidos), carrinho vazio (barra some), modo escuro. Acessibilidade: alvos de 44 px, `:focus-visible`, folha como `role="dialog"` com `Escape`, foco devolvido ao fechar, nome do item no `aria-label` de cada botão, imagens decorativas com `alt=""` (o nome está no texto ao lado).

## Verificação

- Testes: `tools/tenant-panel/tests/test_vitrine.py` (13 casos: preço BR, sem emoji, layout padrão/config/query, chips, abas, serviço sem botão, descrição, sem foto, vazia, XSS, contrato do pedido, rota de fontes, formatadores). Suíte do painel: **28 passam** (Python 3.12, união dos requirements do CI).
- Ensaios (HTML estático com os dados reais da Linda Ana, celular de 390 px em `iframe` e desktop de 1280 px): lista e grade, claro e escuro, sem foto, sem nenhuma foto, vazia, pedido aberto, sucesso e erro; o fluxo de adicionar e enviar funcionou.
- **Template real no navegador (2026-11-03):** painel de verdade (`app.py` do branch) com Mongo falso e os 10 produtos da Linda Ana mais 1 evento e 1 serviço, a 1280 px. Conferido: lista e grade; abas Produtos 10, Eventos 1, Serviços 1; chips de categoria; evento com `Data: 12/12/2026` e botão; serviço com `Atendimento: seg a sex, 9h às 18h` e sem botão; item sem foto com iniciais; fonte Baloo 2 carregada por `/vitrine/fonte/baloo2-800.woff2` (200, `font/woff2`); adicionar vira "✓", barra `2 itens · R$ 1.215,00`, folha com "×", total e botões; **`POST /vitrine/api/pedido` real** criou o pedido `#1-BA457CD2` e redirecionou para `api.whatsapp.com/send` com o texto `Quero fechar o pedido #… (… - R$ 1215,00)` (número de teste; nada foi enviado).
- **Não verificado:** celular (largura de 390 px só nos ensaios estáticos, não no template real), modo escuro no template real, teclado e leitor de tela (só por leitura de código), aparelho físico, catálogo grande (50+ itens), nome de loja muito longo, o caminho de erro do pedido no template real.

## Login do painel (`/painel/login`)

Levantado em 2026-11-03 com o painel real (desktop 1280 px). Problemas: 77 KB de fonte em base64 em cada carregamento (página de 83 KB); marca com o **slug** do tenant (`linda-ana-calcados`) em verde com contorno de texto (`-webkit-text-stroke`); botão amarelo em pílula com brilho e elevação no hover; campo quase da cor do fundo (borda de 1,3:1); fonte dos campos de 15,4 px (o iPhone dá zoom ao focar); nenhum `autocomplete`, `autocapitalize` ou `autofocus`, e corretor ortográfico ligado no campo de usuário; usuário digitado se perdia no erro; erro sem `role="alert"`; e a rota aceitava `?erro=` e escrevia o texto dentro da caixa de erro (qualquer link podia exibir mensagem à escolha; nenhuma tela do projeto usava o parâmetro).

Decisões e mudanças (mesmos tokens de duas camadas da vitrine; identidade da plataforma mantida):

- **Marca:** o ícone da plataforma + **nome da loja** (`signups.config.nome_negocio`, que a vitrine pública já exibe; cai no `tenant_id` se não houver) em Baloo 2 na cor da tinta, sem contorno, e "Painel · AtendPraGente" embaixo. No painel da Duda (`PANEL_MODE=admin`) vale a mesma regra; o que aparece lá depende do cadastro dela e não foi conferido.
- **Cartão** reto (borda de 1 px, raio 12, sem sombra); botão primário `--acao` (verde escuro, texto branco 5,9:1; no escuro `#35C878` com texto escuro), 48 px de altura. O amarelo fica só como anel de foco e na barra do aviso.
- **Campos** de 48 px, `font-size: 1rem`, borda `--borda-campo` (3,9:1 no claro), rótulos ligados por `for`/`id`; `autocomplete="username"` e `"current-password"` (gerenciador de senhas), `autocapitalize="none"`, `spellcheck="false"`.
- **Mostrar/Ocultar senha** (botão `aria-pressed`); aparece só com JS (`hidden` no HTML). "Entrando…" desabilita o botão ao enviar e volta no `pageshow` (botão "voltar" do navegador).
- **Erro:** `role="alert"`, devolve o usuário digitado (escapado) e põe o cursor na senha. Aviso de "painel sem usuário" com `role="status"`.
- **Fontes por arquivo:** nova rota pública `GET /painel/fonte/{nome}` (mesma lista fixa de dois nomes da vitrine), com `preload`. Precisa existir porque o login é compartilhado com o painel da Duda, onde `/vitrine` é bloqueado. Página de 83 KB para ~9 KB (+ as duas fontes, cacheadas por um ano).
- **`?erro=` removido** de `GET /painel/login`.
- Contrato preservado: `POST /painel/login` com `username` e `password`, 303 para `/painel`, 400 com a página no erro; sessão e hash inalterados; link de "esqueci a senha" pelo WhatsApp igual (número fixo no template, como antes).

Testes: `tools/tenant-panel/tests/test_login.py` (13 casos: sem base64, rota de fontes e traversal, fonte no modo admin, nome da loja escapado e fallback, sem contorno/brilho, atributos dos campos e 16 px, `?erro=` ignorado, alerta com usuário devolvido e escapado, login correto, aviso sem usuário, botão de mostrar só com JS). 12 deles falham contra o login antigo. Suíte do painel: 41 passam.

Verificado no navegador com o app real e Mongo falso: claro e escuro a 1280 px, 390 px em `iframe`, erro com usuário devolvido e cursor na senha, mostrar/ocultar senha, fontes carregadas por arquivo. **Não verificado:** aparelho físico, teclado virtual do iOS (zoom), gerenciador de senhas real, leitor de tela, nome de loja muito longo, o login no painel da Duda em produção.

## Criação de usuário (`/painel/setup`) e base comum das telas de acesso

Feita em 2026-11-03, depois do login. `setup.html` tinha o mesmo CSS inline do login (e os mesmos problemas): 77 KB de fonte em base64, marca "AtendPraGente" sem a loja, botão amarelo com brilho, campos de 15 px sem `autocomplete`, placeholder como única instrução ("mínimo 8 caracteres" sumia ao digitar), nenhuma dica nem checagem da confirmação antes do envio, usuário apagado no erro e `?erro=` na URL (exigia token válido, então o risco era pequeno, mas não era usado).

- **Base comum em arquivo:** `static/plataforma.css` (fontes, tokens de duas camadas, reset, foco; **toda** tela da plataforma usa), `static/acesso.css` (página centrada, cartão, campos, botão, erro/aviso; só login e criação de usuário) e `static/plataforma.js` (mostrar/ocultar senha, checagem da confirmação, "Entrando…"/"Criando…" ao enviar), mais as partes `_cabeca.html` e `_marca.html`. `login.html` e `setup.html` só têm o que é próprio de cada tela. O motor de customização troca a camada semântica **num lugar só**.
- **Rota pública `GET /painel/estilo/{nome}`** (lista fixa de dois nomes, `Cache-Control: public, max-age=31536000, immutable`, 404 para qualquer outro). O endereço leva `?v=<sha256 do arquivo, 10 caracteres>` (`url_estilo()`, global do Jinja), então uma mudança no arquivo troca o endereço e ninguém fica com CSS velho em cache. Fica sob `/painel` (não `/vitrine`) porque o painel da Duda usa as mesmas telas.
- **Criação de usuário:** `autocomplete="new-password"` nos dois campos de senha (o gerenciador de senhas sugere uma senha forte), dica visível "Pelo menos 8 caracteres." ligada por `aria-describedby`, mostrar/ocultar nos dois campos, e a confirmação avisa "As senhas não são iguais." antes do envio (`setCustomValidity`). A validação do servidor continua igual e é a que vale.
- **Erro do servidor:** `role="alert"`, usuário devolvido (escapado), cursor na senha, token preservado no formulário.
- `<meta name="referrer" content="no-referrer">` e `noindex` nas duas telas: o link de `/painel/setup?token=...` carrega o token na URL.
- `?erro=` removido de `GET /painel/setup`.
- Contrato preservado: `GET /painel/setup?token=` (403 com token errado, redireciona ao login se já existe usuário), `POST /painel/setup` (`token`, `username`, `password`, `password_confirm`), 303 para `/painel`, 400 com a página no erro.

Testes: `test_setup.py` (13 casos: sem base64 e marca da loja, atributos dos campos, referrer/noindex, 403 e redirecionamento, `?erro=` ignorado, os três erros de validação com usuário devolvido, escape, criação e entrada, rota de estilos e traversal, hash no endereço, modo admin) e `test_login.py` ajustado para ler o CSS do arquivo. Suíte do painel: **54 passam** (67 com o painel, abaixo).

Verificado no navegador com o app real e Mongo falso: criação completa (cai no painel), confirmação diferente barrada com a mensagem, erro do servidor com cursor na senha, escuro a 1280 px, 390 px em `iframe`, CSS/JS/fontes carregados por arquivo. **Não verificado:** aparelho físico, gerenciador de senhas real (a sugestão de senha forte), leitor de tela, o painel da Duda em produção.

## Painel (`/painel`, caixa de conversas)

Feito em 2026-11-03, depois do login e da criação de usuário. Levantado no app real com Mongo falso e conversas semeadas (uma que pede humano, uma em atendimento manual, nome longo, URL sem quebra, mensagem não entregue, uso do plano a 82%). Problemas do `index.html` antigo: 77 KB de fonte em base64 + 35 KB de CSS e JS inline (112 KB por carregamento); marca com o slug do tenant em verde com contorno de texto e "by atendpragente" a 10 px; emojis como ícones (🚨 📎 ⚠️ ✕); `alert()` para erro e `confirm()` para excluir; conversas como `<div>` clicável (sem teclado, sem foco, sem `aria-current`); lista **apagada e refeita a cada 5 s**, o que tirava o foco do teclado; a conversa **voltava ao fim a cada 5 s**, mesmo para quem tinha rolado para reler; data completa `2026-11-03 18:03` na lista e na bolha; **bug de bolha:** o recuo do HTML dentro de `white-space: pre-wrap` virava linhas em branco e espaços antes do texto (a distância enorme entre a mensagem e a hora); selo de não lidas branco sobre `#1FAE5C` (2,9:1); hora da bolha a 9 px; campo de mensagem a 14,4 px (zoom no iPhone); ícones de 22 px como alvo de toque; marca d'água com o logo repetido no fundo da conversa; botão amarelo de 13 palavras ("Atendimento manual — devolver pro bot").

Decisões e mudanças (mesmos tokens; identidade da plataforma mantida):

- **Arquivos:** `static/painel.css` e `static/painel.js` (mesma rota `/painel/estilo/{nome}?v=<hash>`); `index.html` caiu de 112 KB para ~9 KB. O JS mantém **o mesmo contrato**: as rotas `/painel/api/*` e a chave `painel-seen-<session_id>` do `localStorage`.
- **Marca:** ícone + nome da loja (`nome_negocio`, cai no `tenant_id`) + "Painel · AtendPraGente"; título da aba "Conversas — {loja}".
- **Navegação** (vitrine, agenda, pedidos, configurações, sair) é um `<nav aria-label>` com alvos de 44 px e nome acessível em cada ícone; as mesmas regras de exibição (modo admin da Duda, agenda e pedidos conforme a loja).
- **Lista:** cada conversa é um `<button>` (Tab, Enter, `aria-current`), com avatar de iniciais (ícone de pessoa quando o nome é só número), nome em uma linha, hora curta (hoje `18:03`, `Ontem`, `02/11`), estado ("Precisa de você" em vermelho com ícone, "Atendimento manual") e selo de não lidas (branco sobre verde-escuro, 5,9:1, com "não lidas" para leitor de tela). Número de WhatsApp aparece formatado (`+55 11 98765-4321`). **O DOM só é refeito quando algo muda** (comparação por assinatura), então foco e rolagem sobrevivem ao polling de 5 s.
- **Conversa:** cabeçalho com avatar, nome e estado ("Bot respondendo" / "Atendimento manual"); botão "Assumir conversa" (cheio) ou "Devolver ao bot" (contorno); aviso do motivo do handoff com ícone e "Marcar como resolvido"; separador de dia ("Hoje", "Ontem", `02/11/2026`); balões **do cliente** (superfície com borda), **do bot** (verde claro, rótulo "Bot") e **seus** (verde escuro, rótulo "Você"), alinhados à esquerda, hora a 11,5 px **logo abaixo do texto** (distância 0, pedido do dono), URL longa quebra dentro do balão; falha de entrega com ícone e motivo. A conversa só vai ao fim na primeira abertura ou se a pessoa já estava no fim.
- **Antes de assumir:** em vez de sumir com o campo de texto, aparece "O bot está respondendo. Toque em “Assumir conversa” para escrever você mesmo."
- **Erros e exclusão:** `alert()` virou aviso `role="alert"` que some em 6 s; `confirm()` virou `<dialog>` (foco em "Cancelar", Esc fecha, texto: sai só da lista, o bot continua lembrando e a conversa volta se o contato escrever).
- **Celular (≤768 px):** lista ou conversa em tela cheia, botão "Voltar" de 44 px e o **botão voltar do aparelho** (`history`) volta para a lista em vez de sair do painel; aviso do handoff e botão de assumir em linhas próprias.
- **Fundo da conversa** liso (`--conversa-fundo`, claro e escuro); marca d'água removida. `prefers-reduced-motion` respeitado (em `plataforma.css`).

Testes: `test_painel.py` (13 casos: sem sessão vai ao login, HTML enxuto sem fonte/CSS/JS embutidos, marca escapada e sem contorno, ícones com nome acessível, navegação por loja e pelo modo admin, sem emoji/`alert`/`confirm`, partes acessíveis, contrato da API e do `localStorage`, lista refeita só quando muda, bolha sem `pre-wrap` no contêiner e com a hora colada, arquivos servidos com cache longo, sem marca d'água). 7 deles falham contra o painel antigo. Suíte do painel: **67 passam**.

Verificado no navegador com o app real e Mongo falso: lista e conversa a 1280 px, claro e escuro, 390 px em `iframe` (lista, conversa, escuro), URL longa, falha de entrega, aviso "bot respondendo", foco da lista preservado após o polling, diálogo de excluir (Esc não exclui), distância texto→hora medida em 0 px nos seis balões. **Não verificado:** aparelho físico, leitor de tela, envio real de mensagem e imagem (as rotas de envio não foram exercitadas, só o contrato no código), 200 conversas, o painel da Duda em produção, o aviso de erro (`role="alert"`) disparado por falha real de rede.

Fora desta etapa (as três telas internas foram feitas depois, na seção seguinte): um filtro "Precisam de você" na lista (com 80+ conversas o alerta pode ficar fora da primeira tela) e uma prévia da última mensagem exigiriam campo novo na API.

## Telas internas: agenda, pedidos e configurações

Feito em 2026-11-03, depois do painel. As três tinham ~170 linhas de CSS inline cada, **sem `@font-face`** (usavam `var(--font-display)` e caíam na fonte do sistema, então Baloo 2 e Nunito nunca apareciam depois do login), botão amarelo em pílula com brilho e elevação, campos de 15 px, 15 usos de `alert()`/`confirm()` (agenda 1 `alert`; pedidos 5; configurações 6 `alert` e 3 `confirm`), emojis como ícones (🔄 ⚠️ 🔔 📍 👤 🛍️ ✓ ✨ ⬇ 📦 🛠️ 📅 🕒) e a marca "AtendPraGente" no topo em vez da loja. Problemas específicos:

- **Agenda:** `hoje` calculado com `toISOString()` (UTC: depois das 21h em Brasília marcava o dia seguinte); **evento agrupado pelo dia do texto UTC** (um compromisso às 22h em Brasília chega como 01h do dia seguinte e aparecia na coluna errada); botão "Confirmar" **dentro** de um `<a>` (controle interativo dentro de controle interativo); grade fixa de 910 px com rolagem horizontal no celular.
- **Pedidos:** valor `R$ 1215.00` sem milhar (a vitrine já mostrava `R$ 1.215,00`); modal feito à mão (sem foco preso, sem `role`); botão "Salvo ✓" que ficava assim para sempre; pills a 11 px em maiúsculas com cores fixas (`#fff3d6`…).
- **Configurações:** `<input type=file>` com `display: none` (o teclado não alcançava "Foto", "Escolher CSV" e "Escolher .zip"); linhas dos números de WhatsApp montadas com `style.cssText` e `innerHTML` **sem escape** do nome vindo da Meta; uma página única de ~1.100 linhas com seis cartões e nenhuma navegação.

Decisões e mudanças (mesmos tokens; identidade da plataforma mantida):

- **Base comum:** `plataforma.css` passou a ter também o que todas as telas usam (`.btn`, `.btn.contorno`/`.perigo`, `.dialogo`, `.toast`, `.icone`, `[hidden]`, `--alvo`, `--aviso-fundo`/`--aviso-texto`); `interno.css` (topo com a loja, cartão, avisos, pills, campos de 16 px, botão padrão) só nas três; `agenda`, `pedidos` e `configuracoes` ganharam `.css` próprio, e agenda e pedidos também `.js` (a configurações mantém o script inline porque usa Jinja). Tudo pela rota `/painel/estilo/{nome}?v=<hash>`. O `<button>` sem classe vale como ação principal com **especificidade 0** (`:where(body.interno) button`), então qualquer classe da tela o sobrepõe.
- **`interno.js`:** `avisar(texto)` (faixa `role="alert"` que some em 6 s) e `await confirmar({titulo, texto, ok, perigo})` num `<dialog>` com foco em **Cancelar** e Esc cancelando. Substituem todos os `alert()`/`confirm()`.
- **Topo:** ícone + nome da loja + "Painel · AtendPraGente" e o link "Conversas"; as três rotas passaram a mandar `nome_negocio`.
- **Agenda:** celular = um dia por linha (rótulo à esquerda, compromissos à direita), a partir de 900 px = sete colunas sem rolagem lateral (página de 1.180 px); "hoje" e o agrupamento por dia usam **o dia em São Paulo**; evento é um `<article>` com hora, título, estado (pill: "Aguardando confirmação", "Confirmado", "Pediu remarcação") e, como irmãos, o botão "Confirmar com o cliente" e o link "Abrir na Google Agenda"; rótulo da semana com o ano (`02/11 a 08/11/2026`); estado de carregamento e erro com `role`.
- **Pedidos:** ícones SVG no lugar dos emojis, valor com `Intl.NumberFormat('pt-BR', BRL)`, status como pills (`aviso`/`ok`/`erro`), botões de 40 px (ação principal cheia, as outras em contorno, "Cancelar pedido" em vermelho), endereço com `<label>` de verdade, itens num `<dialog>`, aviso de fraude em caixa neutra com a borda vermelha (a faixa de rastreio continua âmbar, para as duas não se confundirem). **Lógica e rotas iguais:** rolagem infinita, poll de 15 s só da primeira página, `upsertPedido`, `recarregar()` depois de pago/cancelar.
- **Configurações:** navegação por seções no topo (WhatsApp, Atendente, Catálogo, Contatos, Agenda, Assinatura, com `id` em cada cartão), `✨ Ajudar a escrever` virou "Ajudar a escrever", miniatura do item sem foto mostra as **iniciais** (como a vitrine) em vez de emoji, preço em `R$ 1.215,50`, "Data:"/"Atendimento:" no lugar de 📅/🕒, "Testado ✓" virou "Testado", seletores de arquivo visualmente escondidos mas **focáveis** (anel de foco no botão), números do WhatsApp escapados e sem estilo inline, o `tenant-tag` com o slug saiu. O script foi mantido, com `avisar()` e `confirmar()` no lugar de `alert()`/`confirm()`: o `confirm()` nativo bloqueava a página e não dava para estilizar nem descrever a ação.
- Texto e rotas preservados: nenhum `/painel/api/*` mudou.

Testes: `test_telas_internas.py` (19 casos: login obrigatório nas três, base comum e sem `<style>`/base64, sem emoji/`alert`/`confirm` em HTML, JS e CSS, fontes da marca carregadas, helper de aviso/confirmação, arquivos servidos e lista fixa, agenda por dia de São Paulo, contrato e formato dos pedidos, seções/iniciais/preço na configurações, uso do helper e escape, seletor de arquivo acessível, campos de 16 px, Duda). 9 falham contra as três telas antigas. Suíte do painel: **86 passam**.

Verificado no navegador com o app real e Mongo falso: as três telas a 1280 px e a 390 px em `iframe` (claro e escuro), sem rolagem horizontal; o compromisso das 22h aparece no dia certo; marcar pedido como pago (status e ações mudam), diálogo de itens; aviso de erro; na configurações, abrir a ajuda de texto, salvar o atendente (status vira "aguardando publicação"), **adicionar e excluir item de verdade** com o diálogo, e o diálogo de cancelar assinatura (aberto e cancelado). **Não verificado:** aparelho físico, teclado e leitor de tela, troca de número de WhatsApp (exige a Meta), importação de CSV e de .zip de fotos, upload de foto, "Salvar e testar conexão" da agenda, confirmação real de cancelamento de assinatura, "Confirmar com o cliente" (envia WhatsApp), o poll de 15 s e a rolagem infinita com mais de uma página de pedidos, e as três telas no painel da Duda em produção.

Sugestões (não feitas, mudam comportamento): pedir confirmação antes de "Cancelar pedido" (hoje cancela direto, como antes) e antes de "Marcar como pago".

## Onboarding (`onboarding-service`: cadastro, formulário em passos, aguardando e no ar)

Feito em 2026-11-04, a última fila de telas da plataforma. É o funil de venda: a página inicial é a primeira coisa que um cliente novo vê, antes de qualquer loja existir (por isso aqui a marca é "AtendPraGente", não a de uma loja). Problemas encontrados (leitura do código e depois o app real com o `store` trocado por dados falsos):

- **Fonte em base64 em cada página** (83 a 120 KB por carregamento, só a fonte ~75 KB), CSS e JS inline (o `form.html` tinha 887 linhas).
- **CTA amarelo em pílula com brilho e elevação**; texto pequeno com `--ink-faint` (3:1) em `.reassure`, passos e FAQ (a 12 a 13 px); campos a 15,7 px (zoom no iPhone); alvos de toque pequenos.
- **Emojis na interface:** ⚠️ ✅ ✨ ✓ ✗ (inclusive nos textos de "disponível"/"já em uso" do endereço e do CEP, que dependiam do glifo e da cor).
- **Aviso de convite usado e de desistência `position: fixed` no topo**, em pílula, que cobria a marca e o conteúdo e sumia da leitura por teclado/leitor de tela.
- **`?erro=` na URL do formulário** (`GET /signup/{id}/form`) escrevia texto arbitrário na caixa de erro; ninguém usava o parâmetro (os `erro=` do código são gravações no banco).
- `pattern="[a-z][a-z0-9-]{1,30}[a-z0-9]"` no endereço: o `-` solto numa classe é erro de sintaxe no modo `v` que os navegadores novos usam no `pattern`, então o atributo era **ignorado**; a validação de verdade já existia no JS e no servidor.
- **Bugs de comportamento no formulário:** (1) as sugestões do passo "Me dá uns exemplos?" só eram preenchidas ao chegar no **último** passo (`current === total`), depois de a pessoa já ter visto o campo vazio no passo 5; (2) ao entrar no passo dos planos o foco automático ia para o 1º plano ("Entrada") com anel de foco, parecendo selecionado, enquanto o marcado é "Começando"; (3) a linha de dois campos (CPF/telefone, CEP/número, endereço/bairro) ficava colada no campo de cima; (4) o foco automático na carga da página abria o teclado no celular.
- **Botão da Meta clicado antes do SDK carregar** dava erro de JavaScript (`FB` indefinido) e a pessoa ficava sem retorno.

Decisões e mudanças (mesmos tokens e mesma base; conteúdo e copy preservados):

- **Mesma base do painel, sem copiar arquivo:** o onboarding serve `GET /estilo/{nome}` e `GET /fonte/{nome}` (lista fixa, `Cache-Control: public, max-age=31536000, immutable`, `?v=<hash>`), lendo `plataforma.css` e as fontes **de `tools/tenant-panel/static/`**; a imagem `tools` copia `tools/` inteiro para `/app`, então o arquivo está ao lado, e o pod só monta `/tmp` além disso. Para isso `plataforma.css` passou a apontar as fontes por **caminho relativo** (`url(../fonte/baloo2-800.woff2)`): `/painel/estilo/x.css` carrega `/painel/fonte/...` no painel e `/estilo/x.css` carrega `/fonte/...` no onboarding. O ingress do onboarding já manda `/` inteiro ao serviço (nada a mudar em infraestrutura).
- **Arquivos próprios:** `onboarding-service/static/onboarding.css`, `signup.js` (Embedded Signup e convite de teste), `form.js` (passos, endereço, CEP, ajuda de IA, desistir) e `aguardando.js`. O JS que usava Jinja agora lê `data-*` (`data-app-id` e `data-config-id` no `<body>`, `data-signup-id` na página do formulário); o `signup.js` roda **antes** do SDK da Meta, que define `window.fbAsyncInit`. Partes `_cabeca.html`, `_marca.html` e `_icones.html`; as páginas que têm dado de um cadastro (formulário, aguardando, no ar) levam `noindex` e `referrer: no-referrer` (o link de configuração do painel, com o token, aparece na tela final). A página inicial continua indexável.
- **Página inicial:** mesma estrutura (chamada, mini conversa, três passos da Meta, aviso do Portfólio Empresarial, FAQ, benefícios, CTA), com marcas de verificação em SVG, botão principal verde de 52 px, aviso do Portfólio em caixa âmbar com ícone, avisos de convite/desistência **dentro da página** (`role="status"`), texto mínimo de 14 a 15 px, contraste dos tokens. O botão desliga enquanto conecta e volta se a Meta cancelar; sem o SDK carregado mostra "O login da Meta ainda está carregando. Tenta de novo em alguns segundos." O painel escondido do convite de teste (digitar "duda") continua, agora com Esc e alvo de 44 px. O balão de exemplo do mini chat mantém o emoji "😊": é a amostra do que o assistente escreve, não interface.
- **Formulário:** mesmos 7 passos (6 no convite gratuito), mesmos nomes de campo (há teste comparando com a assinatura de `submit_form`) e mesma validação por passo; campos de 16 px com `autocomplete` (`organization`, `email`, `tel`, `postal-code`, `address-line1`), `inputmode` numérico/telefone, `<fieldset>`/`<legend>` nos planos com radios de 22 px e selo "mais escolhido" como elemento; "Ajudar a escrever" sem estrela e num macro (era o mesmo bloco copiado 5 vezes); endereço e CEP com status em texto ("está disponível", "Esse endereço já está em uso", "CEP não encontrado", "Endereço preenchido"), `aria-live`; "Desistir" com foco em "Continuar meu cadastro" e Esc fecha. Corrigidos os quatro comportamentos listados acima.
- **Aguardando e no ar:** mesma lógica (recarrega a cada 4 s; falha mostra o detalhe técnico escapado e o link de suporte; "no ar" mostra o domínio). A tela final agora tem o botão **"Cadastrar meu acesso"** (o link de uso único continua visível abaixo, para copiar) em vez de só o endereço como texto. Os textos ("Deu ruim no provisionamento" etc.) não foram alterados.
- `GET /signup/{id}/form` deixou de aceitar `?erro=`; o erro de validação do `POST` continua aparecendo (400) com `role="alert"`.

Testes: `onboarding-service/tests/test_paginas.py` (32 casos: lista fixa e traversal de `/fonte` e `/estilo`, é o mesmo `plataforma.css` do painel e as fontes são relativas, hash no endereço, sem base64/`<style>`/script inline, ids da Meta por `data-*` e ordem dos scripts, botão sem SDK, avisos inline, painel do convite, campos do HTML comparados com `submit_form`, planos e convite gratuito, escape do número conectado, `?erro=` ignorado, erro do servidor com alerta escapado, 16 px/`autocomplete`/`pattern` válido, sem `alert`/`confirm`/emoji/glifos, os quatro comportamentos corrigidos, aguardando em três estados, falha, redirecionamento para o fim, no ar com e sem link, 404). 12 falham contra as páginas antigas. Suítes: onboarding **32**, painel **86**.

Verificado no navegador com o app real do onboarding e o `store` falso: página inicial (convite usado e desistência) a 1280 px e 390 px; clique no botão da Meta **sem** o SDK (simulado); formulário de ponta a ponta até o último passo (endereço ocupado barra o avanço, depois libera; sugestão de exemplos no passo 5; foco no plano marcado; espaço entre linhas de campos), convite gratuito com 6 passos, Desistir (abrir, foco, Esc), abertura da ajuda de IA; aguardando (provisionando), falha e no ar a 390 px, claro e escuro, sem rolagem lateral. **Não verificado:** o login e o Embedded Signup da Meta de verdade (o clique no botão com o SDK real não foi feito: abre o login do Facebook), o envio do formulário (`POST` real aciona Asaas/provisionamento), a geração de sugestão da ajuda de IA, a busca de CEP (ViaCEP) e o aguardando a recarregar sozinho, o link de convite de teste ("duda"), teclado e leitor de tela, aparelho físico, e as páginas no cluster depois do deploy.

## Próximas telas e dívidas

- `tapume.html` (painel pausado) ainda é CSS inline sem as fontes da marca.
- A tela da configurações é uma página de ~1.100 linhas com script inline (usa Jinja para dois valores): dá para extrair para `configuracoes.js` passando `soul_apply_minutes` e `catalogo_apply_minutes` por `data-*`.
- O `backoffice` (`tools/comercial-backoffice`, `_base.html`, interno da equipe comercial) tem a própria base de CSS e não foi tocado.
- O motor de customização da loja (ver acima) precisa de: onde gravar o layout/tokens por loja (`signups.config`), validação de cores (contraste) e fallback para a identidade da plataforma.
- Sugestões que mudam comportamento (não feitas): confirmar antes de "Cancelar pedido" e "Marcar como pago"; filtro "Precisam de você" e prévia da última mensagem na lista de conversas (campo novo na API).
