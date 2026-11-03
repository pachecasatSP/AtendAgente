# Vitrine pública e identidade visual da plataforma

> Estado (2026-11-02): **vitrine redesenhada no branch `feat/vitrine-redesenho`, ainda não publicada** (depende de PR, merge e do `implantar`). As demais telas (painel, login, criação de usuário, onboarding) só foram **mapeadas**. Complementa `catalogo-produtos.md` (modelo de dados, import, fotos) e `infraestrutura-aws-atendpragente.md`.

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

## Próximas telas e dívidas

- **Fontes em base64** nas outras telas (~75 KB em cada `login`, `index`, `signup`, `form`...): trocar pela rota de arquivo cacheável. Fora deste PR, porque mexe em telas autenticadas.
- Painel, login, criação de usuário e onboarding: repetir o método (levantar com o navegador, tokens, ensaios, depois o template real).
- O motor de customização precisa de: onde gravar o layout/tokens por loja (`signups.config`), validação de cores (contraste), e fallback para a identidade da plataforma.
