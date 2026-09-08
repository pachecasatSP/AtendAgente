# Comissão por Volume e Frente Comercial — AtendPraGente

## Contexto

Nenhum vendedor ou parceiro vende o AtendPraGente hoje — a base de
clientes inteira vem de venda direta. Esta é a primeira frente comercial
do produto: uma régua de comissão por volume pra vendedores externos, e
um piloto de expansão regional pra testar essa régua na prática. Estudo
completo, com todos os cálculos e ensaios que levaram às decisões abaixo,
publicado como artifact em
`https://claude.ai/code/artifact/d278773f-9a1e-4f92-a860-16f8fb3b80e3`
("Comissão por Volume") — este documento é o resumo executivo, a fonte
de verdade rápida pra consulta; o artifact é o histórico completo do
raciocínio, incluindo a régua alternativa descartada e os ensaios que
levaram a cada ajuste.

**Status: régua fechada e validada pelo dono do negócio em 2026-09-08.**
Pendências restantes (seção final) dependem de terceiros — advogado
trabalhista, CORE-RJ, contador — não de decisão de produto/negócio em
aberto.

Planos de referência (preços praticados em atendpragente.com.br,
2026-09-08):

| Plano | Preço/mês | Limite |
|---|---|---|
| Entrada | R$ 84,90 | 100 conversas/mês |
| Começando | R$ 157,90 | 1.000 conversas/mês |
| Crescendo | R$ 717,90 | 5.000 conversas/mês |
| Sem limite | sob consulta | 5.000+ conversas/mês |

## Régua de comissão

**Régua crescente, oficial**: 12% / 17% / 28% / 27% conforme o tier
(Início / Crescimento / Consolidado / Parceiro-chave). Note que
Consolidado (28%) é maior que Parceiro-chave (27%) — não é erro, é um
ajuste deliberado (ver "Piso de salário mínimo" abaixo).

Uma régua alternativa ("invertida", taxa decrescente por tier:
25,0/20,8/16,7/8,3%, calibrada pra render múltiplos redondos de
mensalidade — 3,0×/2,5×/2,0×/1,0×) foi estudada em profundidade e
**descartada como régua de pagamento**. Ela é estruturalmente incapaz de
garantir 1 salário mínimo via comissão pura, porque sua taxa cai
justamente no tier de maior MRR. Continua documentada no artifact como
referência de benchmark/comunicação (útil pro pitch de recrutamento,
seção 06 do artifact), mas não deve virar a régua real.

Comissão é recorrente por até 12 meses por cliente, recalculada
mensalmente pela carteira ativa do vendedor — subir (ou descer) de tier
vale só pros clientes novos daquele mês em diante, não retroage sobre
comissões já em curso.

## Mix-alvo por tier

Tier não é definido por contagem solta de clientes — é uma **meta de
mix** (quantos clientes de cada plano), com o plano Começando sempre
como âncora (maior fatia em todo tier):

| Tier | Mix (Entrada/Começando/Crescendo) | Total clientes | Comissão (crescente) |
|---|---|---|---|
| Início | 4/6/0 | 10 | 12% |
| Crescimento | 5/8/2 | 15 | 17% |
| Consolidado | 6/8/6 | 20 | 28% |
| Parceiro-chave | 15/25/10 | 50 | 27% |

O mix do Consolidado foi ajustado (de 6/10/4 pra 6/8/6, trocando 2
clientes Começando por 2 Crescendo) junto com a subida de taxa de 22%
pra 28% — é essa combinação de mix+taxa que faz o piso de salário
mínimo fechar nesse tier (ver abaixo). Reduzir Entrada em vez de
aumentar Crescendo foi testado e descartado: precisaria de 32% de taxa
em vez de 28%, e deixa ~R$770/mês a menos de lucro pra empresa no mesmo
resultado pro vendedor.

## Piso de salário mínimo

Decisão de negócio: é saudável a régua garantir 1 salário mínimo mensal
ao vendedor **via comissão pura, sem a empresa subsidiar por fora da
régua**. Isso foi um ensaio explícito — cogitou-se um "piso garantido"
pago do bolso da empresa quando a comissão não alcançasse o piso, mas
essa rota foi descartada: o resultado financeiro depois do complemento
é `MRR − custo − salário mínimo`, independente da régua escolhida, e no
tier Início esse resultado é **negativo** (a empresa perderia dinheiro
garantindo o piso ali). Confirmar que o piso é comissão de verdade
importa também pra classificação jurídica do vínculo (ver abaixo).

Salário mínimo 2026: R$ 1.621,00 (Decreto nº 12.797/2025).

Com o mix-alvo oficial:

| Tier | Comissão (crescente) | Bate 1 salário mínimo? |
|---|---|---|
| Início | R$ 154,44/mês | Não |
| Crescimento | R$ 531,00/mês | Não |
| Consolidado | R$ 1.702,40/mês | **Sim** |
| Parceiro-chave | R$ 3.348,00/mês | Sim |

**Consolidado é o primeiro tier onde a comissão pura garante o piso.**
Início e Crescimento nunca vão garantir — mesmo 100% de comissão não
cobriria o MRR do tier Início. Isso não deve ser prometido em material
de recrutamento como "salário mínimo desde o primeiro cliente".

## Condições gerais

1. **Cliente ativo e pagante conta.** Trial não gera comissão nem conta
   pra tier — nasce no primeiro pagamento confirmado.
2. **Clawback de 60 dias.** Cliente que cancela nos primeiros 60 dias
   estorna a comissão desses dois meses. Critério: 2 mensalidades já
   cobrem o custo de infraestrutura do cliente com folga em qualquer
   plano (~17-20% do valor recebido em 2 meses) — é o ponto onde a
   venda se prova saudável, não só onde a fraude óbvia é pega. Mesma
   janela libera o crédito de indicação (abaixo).
3. **Atribuição por primeiro toque.** O vendedor que gerou o lead
   original fica dono do cliente pelos 12 meses, mesmo que outro tenha
   ajudado a fechar. Vale também pra indicação: quem já atende o
   indicador fica com o indicado.
4. **Pagamento mensal, D+10.** Comissão apurada e paga até o 10º dia
   útil do mês seguinte, sobre o que o cliente efetivamente pagou.
5. **Exclusividade territorial só dentro de frente geográfica
   designada.** Fora de uma frente dedicada (como a Região dos Lagos),
   vale first-touch — qualquer vendedor prospecta em qualquer lugar.
   Dentro de uma frente com vendedor designado por cidade, a cidade é
   exclusiva do vendedor designado por 6 meses ou até ele sair do tier
   Crescimento (o que vier primeiro) — depois, abre pra qualquer
   vendedor da empresa. Prazo de 6 meses é ponto de partida, não
   testado — revisar depois do primeiro ciclo real.

## Vínculo do vendedor

**Decisão: representante comercial autônomo (Lei 4.886/65, alterada
pela Lei 8.420/92)** — não CLT, não PJ genérico.

Por quê: o piso garantido, a meta de mix obrigatória, a exclusividade
territorial e o bônus semestral com elegibilidade por presença têm cara
de subordinação — os 4 requisitos clássicos de vínculo empregatício
disfarçado (subordinação, habitualidade, pessoalidade, onerosidade). Um
"PJ autônomo" comum, sem essa proteção legal específica, corre risco
real de reclassificação trabalhista com passivo retroativo (FGTS, INSS,
13º, férias). A representação comercial é a categoria legal brasileira
feita pra esse formato: comissionada, com direitos próprios, sem virar
vínculo empregatício.

**Alinhamento importante**: pra essa classificação se sustentar, a
comissão precisa ser genuinamente variável — e é: o piso de salário
mínimo não é um valor fixo, é o que a comissão normal já rende quando o
mix-alvo do Consolidado é batido (ver acima). A exclusividade
territorial também é prevista pela própria lei (Art. 31), não é um
fator de risco adicional aqui, desde que conste expressamente no
contrato.

**Requisitos práticos**:
- Registro no CORE (Conselho Regional dos Representantes Comerciais) do
  estado antes de atuar — sem isso a proteção legal não se aplica.
  Complexidade baixa-média: RG/CNH, CPF, foto digital, comprovante de
  residência, informação de escolaridade, antecedentes criminais,
  quitação do serviço militar (homens até 45) e eleitoral. Registro é
  pessoal (vinculado ao CPF/CNPJ do representante) — a empresa não
  registra por ele, mas pode facilitar/financiar (ver abaixo).
- Contrato escrito obrigatório (Art. 27): zona de atuação, exclusividade
  de zona, produtos/planos representados, prazo, forma e época de
  pagamento da comissão.
- Rescisão sem justa causa: aviso prévio de 30 dias (ou 1/3 das
  comissões dos últimos 3 meses no lugar) + indenização mínima de 1/12
  do total de comissões recebidas durante todo o contrato, sem tempo
  mínimo de casa. Exemplo: vendedor no Consolidado por 24 meses
  acumula R$40.857,60 em comissões → indenização mínima de R$3.404,80
  no desligamento. Orçar como custo de saída, separado do custo
  mensal corrente.
- **Advogado trabalhista precisa confirmar o contrato antes de assinar
  o primeiro vendedor** — este documento orienta o desenho, não
  substitui aconselhamento jurídico.

### Financiamento do registro no CORE

Anuidade CORE-RJ pessoa física 2026 (confirmado direto no site,
core-rj.org.br): **R$ 705,94**, cobrança única (sem taxa de inscrição
separada). Desconto por antecipação: 20% em janeiro, 15% em fevereiro,
10% em março — de abril em diante, valor cheio. Como a contratação cai
na janela de setembro-novembro (fora do desconto), usa-se o valor
cheio no financiamento.

Mecânica: a empresa paga o boleto na contratação e desconta de volta em
**11 parcelas fixas de R$ 64,18/mês, do 2º ao 12º mês de contrato**
(1º mês livre de desconto). Dívida sempre quitada no aniversário de 1
ano. **A partir do mês 13, a renovação da anuidade é despesa própria do
vendedor** — a essa altura ele já está no Consolidado (R$1.702,40/mês
ou mais), e R$705,94/ano deixa de ser barreira. Saldo em caso de saída
antes de quitar: abate primeiro da indenização de rescisão; sem
indenização suficiente, vira cobrança direta — precisa constar em
cláusula específica do contrato.

**Em aberto**: não há confirmação de proporcionalidade pra registro no
meio do ano, nem se a próxima anuidade cheia (jan-mar do ano seguinte)
cai dentro do período de desconto do vendedor. Confirmar por telefone:
(21) 3174-8400, seg-sex 8h-17h, antes de fechar o valor definitivo.

## Programas complementares

### Indicação (cliente indica cliente)

Recompensa = **1 mensalidade, limitada ao menor valor entre o plano do
indicador e o do indicado** — `min(mensalidade indicador, mensalidade
indicado)`. A trava evita o caso em que um indicador caro (Crescendo)
indicando um negócio barato (Entrada) geraria recompensa desproporcional
(sem a trava, chegava a 85% da margem de 12 meses do indicado; com ela,
fica perto de ~10% em qualquer combinação).

Diluído em **12 parcelas mensais**, condicionadas ao indicado continuar
ativo naquele mês — não é reembolso à vista. A condicional transforma o
programa em incentivo de retenção mútua (indicador tem motivo pra
torcer pelo indicado continuar ativo), não só bônus de aquisição.
Atribuição: conta pro vendedor que já atende o indicador.

### Bônus semestral

Métrica: **% de superação da meta do período** — MRR real ÷ MRR
planejado pelo plano de rampa (não MRR líquido absoluto, que favoreceria
quem já tem carteira maior, nem categorias por tier de entrada, que
quebram quando o time entra sincronizado, como é o caso do piloto
regional). Prêmios: 1º lugar R$2.000, 2º lugar R$1.000 — pool de
R$3.000/semestre. Elegibilidade: ativo o semestre inteiro, sem clawback
por venda forçada.

Ambos os programas dependem de rastreamento de churn por vendedor, que
o cálculo de comissão atual não faz (só rastreia ganhos) — é um
requisito de dado a desenhar uma vez só no backoffice (Frente 2 do
plano geral), não em paralelo pros dois programas.

## Piloto regional: Região dos Lagos (RJ)

Primeira frente geográfica: **São Pedro da Aldeia, Cabo Frio, Búzios e
Araruama**. Dados reais 2026:

| Cidade | Empresas ativas | População |
|---|---|---|
| Cabo Frio | 32.848 (63% MEI) | 242.457 |
| Araruama | 15.596 (92% microempresas) | 137.922 |
| São Pedro da Aldeia | 14.021 | ~118.700 |
| Búzios | 10.130 | 42.527 (+ ~1,5mi turistas/ano) |
| **Total** | **72.595** | **541.617** |

SAM estimado (40% das empresas ativas, perfil de atendimento direto ao
consumidor via WhatsApp — comércio, alimentação, beleza, turismo): ~29
mil. Estimativa de planejamento, não contagem por CNAE — validar com
levantamento mais fino antes de comprometer meta de vendedor.

Plano de rampa: **1 vendedor por cidade (4 no total)**, régua crescente,
progredindo Início→Crescimento→Consolidado ao longo de 4 trimestres.
T3-T4: os 4 juntos chegam a 80 clientes ativos, R$24.320/mês de MRR
regional, R$6.809,60/mês em comissão total — cada vendedor já batendo
o piso de salário mínimo. Penetração implícita ~0,28% do SAM — meta de
entrada, não de saturação.

**Sazonalidade importa pro timing**: Búzios recebe ~35× sua população em
turistas por ano, concentrados dez-mar. Recrutar e treinar vendedores em
setembro-novembro pra chegarem operando na alta temporada.

**Capacidade de infra**: 80 clientes na região já excede os 42
tenants/nó do estudo de capacidade
(`https://claude.ai/code/artifact/d18856f0-af24-4adf-b9f9-6088005a11fb`)
— planejar o segundo nó Hetzner junto com a meta comercial.

### Captação de leads

Taxa de conversão assumida (lead frio → cliente fechado): **6%**,
estimativa a validar com os primeiros vendedores reais.

Canais: porta a porta (40% do funil, ~10% conversão — maior confiança
em comércio local pequeno), WhatsApp frio (30%, ~4% — cuidado: mandar
template sem contato prévio arrisca bloqueio do número pela Meta),
indicação de cliente ativo (15%, ~25%), parcerias locais (15%, ~12% —
ACIC Cabo Frio, ACIAA Araruama, Sebrae, contadores, associação de
pousadas em Búzios).

Lista fria pode nascer segmentada pelo mix-alvo: CNAEs de comércio/
serviços simples pras vagas de Entrada/Começando, pousadas/restaurantes/
turismo pras vagas de Crescendo que o mix exige a partir do Crescimento.

## Pendências (dependem de terceiros, não de decisão de negócio)

1. Advogado trabalhista precisa revisar/aprovar o contrato de
   representação comercial antes do primeiro vendedor assinar.
2. Confirmar por telefone com o CORE-RJ ((21) 3174-8400): proporcionalidade
   de anuidade pra registro no meio do ano, e quando cai a 2ª cobrança.
3. Contador local pra tramitar o registro dos 4 vendedores da Região dos
   Lagos de uma vez (mesmo CORE, já que as 4 cidades são no RJ).
4. Validar taxa de conversão de 6% com os primeiros vendedores reais.
5. Definir política de WhatsApp frio antes de qualquer vendedor mandar
   mensagem (risco de bloqueio de número).
6. Desenhar rastreamento de churn-por-vendedor no backoffice (Frente 2) —
   pré-requisito pro crédito de indicação e pro bônus semestral.
7. Regra própria de comissão pro plano "Sem limite" (contratos sob
   consulta, sem preço de tabela).
8. Revisar prazo de exclusividade territorial (6 meses) depois do
   primeiro ciclo real da Região dos Lagos.
