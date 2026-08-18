# Spec — Assistente de IA pra preencher Configurações

## 1. Problema

Cliente self-service (sem a Duda ao lado) chega no wizard de cadastro (ou,
depois, no painel de Configurações) e trava nos campos que pedem redação —
não é óbvio o que escrever em "tom de voz" ou como estruturar "serviços" no
formato `nome | descrição | público-alvo`. Campos simples (nome, telefone,
email) não têm esse problema.

## 2. Decisão de escopo (confirmada com o usuário)

- **Interação:** botão "Ajudar" por campo — não um chat único que preenche
  tudo de uma vez. Cada campo difícil gera sua própria sugestão, usando o
  que já foi preenchido nos outros campos como contexto.
- **Campos alvo:** `descricao_negocio`, `tom_descricao`, `servicos`,
  `como_trabalhamos`, `exemplos`.

**Lacuna encontrada ao mapear os dois formulários:** `descricao_negocio` e
`exemplos` só existem no wizard (`form.html`) — o painel de Configurações
(`configuracoes.html`) nunca expôs esses dois campos pro cliente editar
depois. Não vou adicionar esses campos ao painel agora (seria escopo novo,
não pedido) — o botão "Ajudar" nesses dois só aparece no wizard. No painel,
"Ajudar" aparece em `tom_descricao`, `servicos` e `como_trabalhamos` (os
três que realmente existem lá).

| Campo | Formato | Wizard | Painel |
|---|---|---|---|
| `descricao_negocio` | texto livre | ✅ Ajudar | — (campo nem existe no painel) |
| `tom_descricao` | texto livre | ✅ Ajudar | ✅ Ajudar |
| `servicos` | linhas `nome \| descricao \| publico_alvo` | ✅ Ajudar | ✅ Ajudar |
| `como_trabalhamos` | linhas `rotulo: texto` | ✅ Ajudar | ✅ Ajudar |
| `exemplos` | linhas `pergunta \| resposta` | ✅ Ajudar | — (campo nem existe no painel) |

## 3. Onde entra tecnicamente

Achado ao mapear o código: **hoje nenhum lugar em
`onboarding-service`/`tenant-panel`/`soul-generator` chama uma LLM.**
`OPENROUTER_API_KEY` já circula nesses dois serviços, mas só como
pass-through pro Secret do tenant provisionado (pro Hermes/bot em si usar
depois) — nunca é usado pra fazer uma chamada HTTP daqui. Isso é uma
capacidade nova, não um reaproveitamento.

Boa notícia: **não precisa de Secret novo**. `OPENROUTER_API_KEY` já está
disponível nos dois processos:
- `onboarding-service`: já lê `os.environ["OPENROUTER_API_KEY"]` (usado em
  `provisioning_env()`).
- `tenant-panel`: seu Deployment já usa `envFrom` do mesmo Secret
  `{tenant}-hermes-env` que tem `OPENROUTER_API_KEY` (confirmado — é de lá
  que `WHATSAPP_CLOUD_ACCESS_TOKEN` etc. já vêm pro painel) — só falta ler a
  variável em `app.py`, que hoje não lê.

### 3.1 Módulo compartilhado novo: `tools/ai-assist/ai_assist.py`

Mesmo padrão de `tools/soul-generator/` e `tools/provision-tenant/` —
diretório próprio, importado via `sys.path.insert` pelos dois serviços
(igual `main.py` já faz pra `generate_soul`).

```python
class AiAssistError(RuntimeError):
    pass

def suggest_field(field: str, context: dict, hint: str = "") -> str:
    """Chama o OpenRouter (chat completion) e devolve o texto sugerido pro
    campo `field`, já no formato que o parser existente espera (pipe-lines,
    label:texto, etc.) — pronto pra colar na textarea. Nunca inventa fato
    concreto (preço, horário) sem marcar como placeholder pro cliente
    revisar. Levanta AiAssistError em qualquer falha (rede, formato,
    quota) — quem chama decide como degradar, nunca deve travar o
    preenchimento manual do formulário."""
```

Estilo: `urllib.request`/`urllib.error` puro (stdlib), mesmo padrão de
`meta_client.py`/`asaas_client.py` — sem dependência nova no
`requirements.txt`.

**Modelo:** `openai/gpt-5.6-luna` via OpenRouter, fixo por enquanto (é o mais
barato disponível hoje) — não expor como env var configurável nessa v1,
só uma constante `MODEL = "openai/gpt-5.6-luna"` no topo do módulo, fácil de
trocar quando o preço mudar.

Prompt por campo (system prompt fixo por `field`, guarda no próprio
módulo):
- `descricao_negocio`: 1–3 frases, mesmo tom direto usado em
  `descricao_negocio` de exemplo no repo (ver
  `reprovision-teste-atendagente.sh`).
- `tom_descricao`: uma linha curta, estilo do fallback já usado em
  `generate_soul.py` ("Português do Brasil, tom cordial e direto.").
- `servicos`: linhas `nome | descrição | público-alvo`, prontas pro
  `_parse_pipe_lines` existente.
- `como_trabalhamos`: linhas `rótulo: texto`.
- `exemplos`: linhas `pergunta | resposta`, 2–4 pares.

Contexto passado pro modelo: os outros campos do config já preenchidos no
momento do clique (nome_negocio, descricao_negocio se já tiver, tom, etc.)
+ o `hint` opcional que o cliente digitou.

**Guardrail de conteúdo:** o system prompt instrui a nunca inventar preço,
horário ou dado factual específico — quando não souber, usa um placeholder
claro tipo `[ajustar]` em vez de chutar um número, porque cliente que não
revisar com atenção pode publicar informação falsa pro próprio negócio.

### 3.2 Backend — dois endpoints (mesma forma de chamar, autenticação diferente)

**`onboarding-service` — `POST /signup/{signup_id}/ai-assist`**
```json
{"field": "servicos", "hint": "corto cabelo e faço barba"}
```
→ `{"suggestion": "Corte de cabelo | Corte masculino tradicional | Homens adultos\nBarba | Aparar e desenhar barba | Homens adultos"}`

Sem sessão/login nesse ponto do fluxo (é pré-pagamento) — a única proteção é
o `signup_id` (UUID opaco), igual o resto das rotas de `/signup/{id}/...`
hoje. Monta o `context` a partir de `store.get_signup(signup_id)` + o que
já foi digitado no form (client manda o resto do form atual no payload, já
que o servidor só tem o que foi salvo no último submit).

**`tenant-panel` — `POST /painel/api/ai-assist`**
```json
{"field": "tom_descricao", "hint": "sou descontraído mas profissional"}
```
Gated por `require_session` (já autenticado) — monta `context` a partir do
`signup["config"]` atual no Mongo.

Os dois retornam `{"suggestion": "..."}` ou erro `502` com
`{"detail": "..."}` se `AiAssistError`.

### 3.3 Frontend — mesmo padrão nos dois lugares

Ao lado do label de cada campo-alvo, um botão pequeno "✨ Ajudar":

```html
<label>Como o bot deve falar
  <button type="button" class="ai-assist-btn" data-field="tom_descricao">✨ Ajudar</button>
  <textarea name="tom_descricao" ...></textarea>
</label>
```

Clique abre uma caixinha inline (reaproveita o padrão visual já usado pro
"Desistir do cadastro" no onboarding — confirmação/ação inline, sem modal
nem `alert()`/`confirm()` nativo):

1. Campo de hint opcional (uma linha, placeholder específico do campo —
   ex: "ex: corto cabelo e faço barba, atendo em domicílio").
2. Botão "Gerar sugestão".
3. Enquanto carrega: desabilita o botão, texto "Pensando...".
4. Resultado aparece **abaixo do campo real, não dentro dele** — com
   "Usar esta sugestão" (copia pro campo de verdade) e "Descartar".
   **Nunca sobrescreve o campo sozinho** — cliente sempre confirma.

## 4. Fora de escopo (v1)

- Chat único que preenche tudo de uma vez (rejeitado — usuário confirmou
  preferência por botão por campo).
- Adicionar `descricao_negocio`/`exemplos` como campos editáveis no painel
  (fora do que foi pedido; ficaria como trabalho futuro se algum dia fizer
  sentido editar isso pós-cadastro).
- Rate limit/anti-abuso no endpoint — de propósito, sem limite nessa v1.
  Proteção continua sendo só o `signup_id`/sessão opacos, igual o resto das
  rotas. Não travar o cliente por enquanto — só medir (ver seção 6).

## 6. Medição de uso (iterações por cliente)

Sem cobrar nem limitar nada nessa v1 — só **registrar**, porque a ideia é
essa feature virar um add-on pago no futuro e vai precisar de histórico de
uso pra decidir preço/limite (não dá pra cobrar por algo que nunca foi
medido).

Nova coleção `ai_assist_usage` no Mongo compartilhado (mesmo banco que já
tem `signups`/`sessions`/`messages` — acessível dos dois serviços):

```python
{
    "_id": ObjectId(...),
    "signup_id": "...",       # chave universal — existe em wizard e painel
    "tenant_id": "novo-negocio",  # None ainda no wizard antes do tenant_id ser escolhido
    "origem": "wizard" | "painel",
    "field": "tom_descricao",
    "sucesso": True,
    "criado_em": datetime(...),
}
```

Cada chamada a `suggest_field` (sucesso ou falha) grava um documento —
1 iteração = 1 clique em "Gerar sugestão", incluindo re-tentativas. Cada
serviço escreve direto (mesmo padrão de `store.py` já usado em cada um),
não precisa de endpoint dedicado nem de agregação nessa v1 — só o registro
bruto. Consulta/relatório de uso por tenant fica pra quando a decisão de
precificar for tomada.

## 7. Critérios de aceite

- Botão "✨ Ajudar" aparece em `descricao_negocio`, `tom_descricao`,
  `servicos`, `como_trabalhamos`, `exemplos` no wizard.
- Botão "✨ Ajudar" aparece em `tom_descricao`, `servicos`,
  `como_trabalhamos` no painel de Configurações.
- Sugestão nunca substitui o campo sem clique explícito em "Usar esta
  sugestão".
- Falha na chamada da IA (rede, quota, etc.) mostra erro inline e não
  impede o cliente de preencher o campo manualmente.
- Sugestão de `servicos`/`exemplos`/`como_trabalhamos` já vem no formato
  exato que o parser existente (`_parse_pipe_lines`/`_parse_label_lines`)
  espera, sem precisar o cliente reformatar.
- Toda chamada (sucesso ou falha) grava um documento em `ai_assist_usage`
  com `signup_id`/`tenant_id`/`origem`/`field` — dá pra contar iterações
  por cliente sem precisar instrumentar nada além disso.
