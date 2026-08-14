# Identidade visual — AtendPraGente

Direção: jovem, popular, informal. Referência de peso visual: apps de
uso diário no Brasil (delivery, transporte, pagamento), não SaaS
corporativo.

## Paleta

| Token | Hex | Uso |
|---|---|---|
| `--leaf` | `#1FAE5C` | Cor primária — CTAs secundários, links, marca |
| `--leaf-deep` | `#0E7A3D` | Texto sobre fundo claro, faixa de features |
| `--leaf-pale` | `#E3F7EA` | Fundos suaves, contraste do número dos passos |
| `--spark` | `#FFC93C` | Cor de destaque — botão de CTA principal, ícone |
| `--paper` | `#FFFCF5` | Fundo base (creme quente, não branco puro) |
| `--ink` | `#17231D` | Texto principal (preto com viés verde, não `#000`) |

Verde puxa pra associação com WhatsApp sem copiar o verde oficial
(`#25D366`); amarelo é o contraponto de energia/ação. Tema escuro tem
seu próprio conjunto de tokens (ver `<style>` do `index.html`) — verde
e amarelo clareiam pra manter contraste em fundo escuro.

## Tipografia

- **Baloo 2** (peso 800) — títulos, números, logotipo. Rounded/bold,
  carrega o tom informal.
- **Nunito** (variável, 400–800) — corpo de texto, botões, legendas.
  Termina em curvas suaves, conversa com a Baloo 2 sem competir com ela.

Ambas embutidas como `@font-face` em base64 no HTML (nada de CDN
externo — a página funciona offline/sem dependência de terceiros).

## Logotipo

`Atend` + `Pra` (em `--leaf-deep`) + `Gente` — a cor isola a sílaba que
torna o nome informal, reforçando o "pra" na leitura em vez de deixar
o nome inteiro homogêneo.

## Ícone / marca

`poc/atendpragente-brand/icon.svg` — quadrado arredondado verde,
balão de conversa branco com um raio amarelo dentro (resposta
instantânea). Funciona como favicon/ícone de app a partir de 16px.

## Tom de voz

Segunda pessoa informal ("cê", "bora", "tá"), frases curtas, sem
jargão técnico (nunca "webhook", "IA generativa", "multi-tenant" —
fala do que o dono do negócio sente: "seu zap responde sozinho").

## Onde usar

- Landing: `poc/landing-page-atendpragente/index.html`
- CTA aponta para `https://onboarding.atendpragente.com.br/signup`
  (serviço real, ver `tools/onboarding-service/`)
