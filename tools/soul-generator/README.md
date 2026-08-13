# Gerador de SOUL (Fase 2 do roadmap multi-tenant)

Transforma um YAML de configuração de tenant num `SOUL.md` pronto pra
publicar, seguindo a estrutura já validada nos SOULs existentes (Quem eu
sou / Como eu falo / O que a gente faz / Como a gente trabalha / O que
eu ainda não sei / O que eu NÃO faço / Quando encaminhar / Memória /
Exemplos).

Sem dependências além do PyYAML (já vem em qualquer imagem Python
padrão) — roda em qualquer servidor sem setup extra, propositalmente
mais simples que um motor de template completo (Jinja2 etc.), já que o
mecanismo é só substituição de blocos de texto.

## Uso

```bash
python3 generate_soul.py tenants/<arquivo>.yaml -o SOUL.md
# ou pra stdout:
python3 generate_soul.py tenants/<arquivo>.yaml
```

Veja `tenants/exemplo-ac-solucoes.yaml` (reconstrução do SOUL real da AC
Soluções, pra validar que o template reproduz a estrutura) e
`tenants/exemplo-clinica-teste.yaml` (negócio bem diferente, pra provar
que o template generaliza).

## Schema do YAML (campos obrigatórios em negrito)

- **`nome_negocio`**, `artigo_negocio` (default `"a"`, ex: "a AC
  Soluções", "o Studio X")
- **`descricao_negocio`** — texto livre, vira a abertura de "Quem eu sou"
- `descricao_publico` — quem costuma escrever, opcional
- `tom.descricao`, `tom.emoji` — como o bot fala
- **`servicos`** — lista de `{nome, descricao, publico_alvo}`
- `como_trabalhamos` — dict livre `{label: texto}` (stack, contrato,
  prazo, horário, o que fizer sentido pro negócio)
- `lacunas_conhecidas` — lista de strings (coisas que o bot ainda não
  sabe e não deve inventar)
- **`escalacao.nome`**, **`escalacao.telefone`** — pra quem encaminhar
- `escalacao.pronome` (default `"ele"`) e `escalacao.gatilhos_extra`
  (lista, some aos gatilhos padrão)
- `exemplos` — lista de `{pergunta, resposta}`, opcional

## Limitação conhecida

Se `escalacao.nome` já vier com artigo definido embutido (ex: `"a
recepção"`), o texto gerado fica gramaticalmente estranho em alguns
pontos (`"encaminho pra a recepção"`). Preferir um nome próprio
(`"Adolfo"`, `"a Dra. Fernanda"`) ou um substantivo com artigo
indefinido (`"um atendente"` — funciona bem: "encaminho pra um
atendente") em vez de artigo definido solto.

## Próximo passo (Fase 3)

Este script vira uma peça do provisionamento automático: dado o YAML
(vindo de um formulário de onboarding, Fase 4), gera o SOUL e junto cria
os manifests K8s do tenant (ver `specs/roadmap-multi-tenant.md`).
