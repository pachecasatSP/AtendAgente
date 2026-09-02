# Onboarding conversacional — repensando os passos 1-5 do wizard

**Página analisada:** `onboarding.atendpragente.com.br/signup/{id}/form`, passos 1-5.

## Contexto

Hoje, depois do Embedded Signup, o cliente cai num wizard de 7 passos
(`tools/onboarding-service/templates/form.html`) que monta o SOUL do
assistente: negócio, tom de voz, oferta, escalação, exemplos, plano,
cobrança. Cada campo "difícil" (texto livre) já tem um botão **"✨ Ajudar a
escrever"** que chama uma LLM pra sugerir o conteúdo a partir do que já foi
preenchido — mas ainda é um campo por vez, dentro de um formulário
(`specs/assistente-ia-configuracoes.md` §2, decisão já confirmada: "botão
Ajudar por campo — não um chat único que preenche tudo de uma vez").

Esta spec propõe revisitar essa decisão especificamente para o wizard de
onboarding (não pro painel de Configurações, que mantém os botões por
campo): trocar os **passos 1-5** (conteúdo do SOUL) por uma conversa guiada
dentro da própria página, em vez de formulário com preenchimento assistido.
**Passos 6 (plano) e 7 (cobrança) ficam como estão** — são decisões formais
que fazem mais sentido como tela normal, não como conversa.

### Por que faz sentido tentar

- A promessa central da marca (`DESIGN.md`, landing page) é "a conversa é o
  produto" — pedir pro cliente preencher um formulário de 5 telas pra
  configurar um *assistente de conversa* é uma dissonância que ele sente na
  pele antes mesmo de receber a primeira mensagem de um cliente dele.
- O cliente-alvo (`PRODUCT.md` — dono de pequeno negócio, sem equipe
  técnica) já entende "responder perguntas sobre o meu negócio" muito
  melhor do que "preencher o campo `como_trabalhamos` no formato
  `Rótulo: valor`". A conversa esconde a estrutura de dados; o formulário
  expõe ela.
- Boa parte do trabalho de extração e redação já existe — só em outro
  formato (`tools/ai-assist/ai_assist.py`, campo por campo). O salto aqui é
  de "gerar 1 campo a partir de 1 dica" para "extrair N campos a partir de
  uma resposta livre", não construir do zero.

### Risco que isso não resolve sozinho

Uma conversa faz sentido pros campos de texto livre (descrição, tom,
serviços, exemplos). Ela **não** melhora campos estruturais/técnicos —
`tenant_id` (o slug `minha-loja.atendpragente.com.br`, com validação de
unicidade em tempo real) e `artigo_negocio` continuam precisando de um
input direto, não de linguagem natural. Forçar esses dois pra dentro de
bolhas de chat ("digite o endereço do seu atendimento") seria pior que o
campo atual — é só um formulário fantasiado de chat. Ver decisão de design
abaixo.

---

## Desenho do fluxo conversacional

### Princípio: agrupar por assunto, não por campo

Em vez de uma pergunta por campo do banco (que viraria ~15 idas-e-voltas —
pior que os 5 passos atuais), a conversa é organizada nos **mesmos 5 temas**
que já estruturam o wizard, mas cada tema é **uma pergunta aberta**, e a IA
extrai múltiplos campos da resposta de uma vez, do mesmo jeito que uma
recepcionista faria uma pergunta ampla e anotaria os detalhes internamente:

| Tema (era passo) | Pergunta aberta que o bot faz | Campos extraídos daquela resposta |
|---|---|---|
| 1. Negócio | "Me conta sobre o seu negócio — o que vocês fazem, e como você quer que eu chame o seu assistente?" | `nome_negocio`, `descricao_negocio`, `nome_atendente` (opcional) |
| 2. Tom de voz | "Como você costuma atender? Mais formal, descontraído, técnico... e usa emoji?" | `descricao_publico`, `tom_descricao`, `tom_emoji` |
| 3. Oferta | "O que vocês oferecem? Pode listar os principais serviços/produtos e, se quiser, como vocês trabalham (horário, prazo, etc.)." | `servicos`, `como_trabalhamos`, `lacunas_conhecidas` |
| 4. Escalação | "Quando o assunto pedir uma pessoa de verdade, pra quem eu encaminho? Nome e telefone." | `escalacao_nome`, `escalacao_telefone`, `escalacao_pronome` |
| 5. Exemplos (opcional) | "Tem alguma pergunta que seus clientes fazem sempre? Me dá um ou dois exemplos de pergunta e resposta." | `exemplos` |

`tenant_id` e `artigo_negocio` **não entram na conversa** — ver "Decisão de
design: o slug fica fora do chat" abaixo.

### O que aparece na tela a cada rodada

1. O bot faz a pergunta do tema atual (bolha de chat, no mesmo tom
   informal já usado no resto do produto).
2. O cliente responde em texto livre, num campo único (sem sub-campos).
3. A IA extrai os campos daquele tema e devolve **um cartão de confirmação
   inline**, mostrando o que foi entendido campo a campo — não um resumo em
   prosa, um cartão editável (mesma lógica visual de qualquer "confirma os
   dados" antes de avançar). Cada campo do cartão é clicável/editável
   direto ali, sem reabrir formulário.
4. Cliente confirma (ou edita e confirma) → bot passa pro próximo tema.
5. Se a resposta não deu pra extrair um campo obrigatório daquele tema
   (ex.: descreveu o negócio mas não dá pra saber o nome dele), o bot
   **pergunta de novo só o que faltou**, não a pergunta inteira — evita o
   "you told me that already" que mais irrita em fluxo conversacional mal
   feito.

Esse cartão de confirmação é o ponto mais importante do desenho: sem ele, a
extração automática vira uma caixa-preta, e um cliente não-técnico não tem
como saber se a IA "entendeu direito" o que ele quis dizer — exatamente o
tipo de ansiedade que a spec de melhorias no onboarding já identificou como
o principal risco pro perfil de cliente leigo.

### Progresso visível

Troca "Passo 1 de 7" por uma lista curta dos 5 temas com estado (○ não
começou / ✓ confirmado / ✎ em edição), sempre visível — mantém a mesma
função do indicador de progresso atual (reduzir ansiedade de "quanto falta
ainda"), mas sem impor ordem rígida: o cliente pode clicar num tema já
confirmado e reabrir a conversa daquele ponto pra ajustar algo, sem perder o
que já respondeu nos outros.

### Decisão de design: o slug fica fora do chat

`tenant_id` tem uma validação de unicidade em tempo real
(`tenant_id-status`, `.slug-status` — checa se `minha-loja` já existe
enquanto o cliente digita) e um formato restrito (`[a-z][a-z0-9-]{1,30}[a-z0-9]`).
Isso é fundamentalmente diferente de "descreva seu negócio": é um dado
técnico com feedback síncrono, não uma frase pra extrair.

Proposta: o campo de endereço (`tenant_id` + `artigo_negocio`) aparece como
um **input inline fixo** no topo da conversa (ou logo após a resposta do
Tema 1, já sugerido a partir do `nome_negocio` que acabou de ser
extraído — "sugerimos `minha-loja`, pode trocar"), com a mesma checagem de
unicidade ao vivo que existe hoje. Ele não é "perguntado" pelo bot em
linguagem natural; é preenchido como qualquer campo técnico, só que
posicionado dentro do fluxo em vez de escondido num formulário separado.

### O que muda pro botão "✨ Ajudar a escrever"

Nos passos 1-5, o botão desaparece — a conversa inteira *é* a ajuda: o
cliente não precisa mais pedir sugestão, ele já responde em linguagem
natural e a IA estrutura. Nos passos 6-7 (que continuam formulário) e no
painel de Configurações (edição pós-onboarding), o padrão atual por campo
continua existindo sem mudança — são contextos diferentes (editar um campo
isolado depois vs. montar o SOUL inteiro pela primeira vez).

---

## Perguntas em aberto antes de virar spec técnica

1. **Confiança na extração multi-campo.** Extrair 1 campo de 1 dica (hoje)
   é mais previsível que extrair 2-3 campos de uma resposta livre mais
   longa. Precisa validar com respostas reais de teste se a extração erra
   pouco o suficiente pra o cartão de confirmação ser "revisão rápida" e
   não "conserto trabalhoso" — se errar muito, a conversa fica pior que o
   formulário.
2. **O que acontece se o cliente responder tudo de uma vez** (ex.: já
   escreve negócio + tom + serviços na primeira mensagem, sem esperar o
   bot perguntar tema por tema)? Vale a pena a extração já tentar pegar o
   que der de qualquer tema, ou trava a conversa em um tema por vez pra
   manter o desenho simples na v1?
3. **Reentrada.** Se o cliente fecha a aba no meio da conversa e volta
   depois (mesmo padrão de "Passo X de 7" hoje, que retoma de onde parou),
   a conversa reabre do mesmo jeito — mostra o histórico de mensagens
   anteriores, ou só o resumo/cartões já confirmados?
4. **Skip explícito.** O wizard atual marca campos como opcionais
   (`<span class="optional-tag">opcional</span>`). Numa conversa, como o
   cliente sinaliza "pular esse tema" sem precisar digitar "não sei" e
   torcer pra IA entender que aquilo é vazio? Precisa de um botão "pular"
   visível ao lado do campo de resposta, não só confiar em linguagem
   natural pra isso.
5. **Fallback pra formulário.** Alguém prefere simplesmente preencher os
   campos direto (usuário técnico, ou alguém que já testou e achou o chat
   mais lento) — existe um link "prefiro preencher manualmente" que leva
   pro wizard de campos de hoje, ou o conversacional vira o único caminho?

## Fora de escopo (nesta spec)

- Passos 6 (plano) e 7 (cobrança) — continuam formulário, sem mudança.
- Painel de Configurações (edição pós-onboarding) — continua com botão
  "Ajudar" por campo, sem mudança.
- Qualquer coisa acontecendo dentro do WhatsApp do cliente (Opção descartada
  na decisão de escopo: a conversa é uma UI de chat *na página* de
  onboarding, não mensagens reais trocadas pelo WhatsApp recém-conectado).
- Detalhe técnico de implementação (endpoints, prompt de extração, schema
  Mongo, como o cartão de confirmação vira `signup` no banco) — fica pra
  uma spec técnica separada depois que este desenho for validado, seguindo
  o mesmo padrão de `specs/melhorias-onboarding-whatsapp-spec.md`.
