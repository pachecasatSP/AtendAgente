# Reaproveitamento de contatos antigos (histórico pré-AtendPraGente)

## Contexto

O tenant já usava o número de WhatsApp no **app/Web do WhatsApp Business**
antes de conectá-lo ao AtendPraGente via Embedded Signup (Cloud API). Ele
quer que o bot "saiba" que um contato que escreve agora já era cliente
antigo desse número — não necessariamente reproduzir o texto das
conversas antigas, só dar esse contexto pro atendimento.

## Restrição técnica real (não é decisão de produto, é limite da Meta)

A migração de um número do app/Web do WhatsApp Business pra Cloud API
**não carrega histórico nenhum** — é um comportamento da própria
plataforma, não um buraco que o AtendPraGente tenha como preencher via
API. O Cloud API não tem, e nunca teve, endpoint pra ler o banco de
conversas do celular. Duas consequências diretas:

- Não existe forma de "puxar" automaticamente as conversas antigas — o
  dado só existe no aparelho (ou em backup do WhatsApp) de quem já era
  dono do número.
- Qualquer reaproveitamento depende de o próprio tenant **exportar** algo
  de lá antes, fora do AtendPraGente, e a gente **importar** esse
  material pelo painel.

## Opções de extração (o que o tenant consegue tirar do WhatsApp)

| Opção | O que dá pra extrair | Esforço do tenant | O que a gente ganha |
|---|---|---|---|
| **A. Lista simples (nome + telefone)** | Contatos que ele lembra/já tem salvos, digitados ou exportados da agenda do celular | Baixo — uma planilha | Só a flag "esse já era cliente", sem conteúdo de conversa |
| **B. Export chat do WhatsApp (.txt por conversa)** | Recurso nativo "Exportar conversa" (Configurações da conversa → Exportar), gera um `.txt` por contato, às vezes com mídia anexada | Alto — é manual, um .txt por conversa, não existe "exportar tudo de uma vez" no WhatsApp | Texto real das mensagens antigas, mas picotado e sem estrutura (nomes de arquivo variam, timestamps em formato de log, `<Mídia oculta>` no lugar de anexos) |
| **C. Backup do WhatsApp (Google Drive/iCloud)** | Banco completo, mas **criptografado e no formato interno do WhatsApp** — não existe parser oficial nem API pra abrir isso fora do próprio app | — | Na prática inviável sem engenharia reversa do formato, fora de cogitação |

**C está descartada.** Entre A e B, a pergunta que decide é: o objetivo
(dado pelo usuário) é só o bot **reconhecer** que é cliente antigo, não
reconstruir o histórico de mensagens. Isso pesa a favor de A como V1:
entrega o essencial do que foi pedido com uma fração do esforço e do
risco de parsing de B. B fica como possível V2 se, na prática, um resumo
curto por contato (extraído manualmente do .txt, não o texto inteiro) se
mostrar valioso — mas B não deveria ser o ponto de partida.

## Proposta (V1): lista de contatos + flag "cliente antigo"

### 1. Nova coleção `contatos_importados` (Mongo, escopada por `tenant_id`)

```
{
  tenant_id: str,
  telefone_normalizado: str,   # só dígitos, com DDI — mesmo formato do chat_id do WhatsApp
  nome: str | None,
  nota: str | None,            # campo livre opcional, ex. "cliente desde 2023, comprava sapatos"
  origem: "import_csv",
  importado_em: datetime,
}
```

Índice único em `(tenant_id, telefone_normalizado)` — reimportar a mesma
planilha faz upsert, não duplica.

### 2. Import pelo painel (reaproveita o padrão do catálogo)

`tools/tenant-panel/app.py` já tem exatamente esse fluxo pra produtos —
`api_catalogo_template` (baixa um CSV modelo) + `api_importar_catalogo`
(sobe um CSV, faz upsert) — linhas 757–823. A proposta é replicar o
mesmo par de endpoints pra contatos:

- `GET /painel/api/contatos/template` → CSV com colunas `nome,telefone,nota`
- `POST /painel/api/contatos/import` → parse, normaliza telefone (remove
  tudo que não é dígito, garante DDI 55 se vier sem), upsert em
  `contatos_importados`

Tela nova (ou seção dentro de Configurações) com o botão de baixar
modelo + upload, mesmo componente visual já usado no catálogo.

### 3. Consulta em tempo real pelo hermes (mesmo padrão do handoff)

O container `hermes` não fala com o Mongo direto — só o sidecar
`mongo-sync` fala (`tools/provision-tenant/mongo_sync/sync_conversations.py`).
O sidecar já expõe um HTTP local (`127.0.0.1:8091`) com `GET /handoff?chat_id=...`,
consultado pelo hermes antes de cada envio automático
(`HandoffHTTPHandler.do_GET`, linha 741). Proposta: endpoint irmão:

```
GET /contato-conhecido?chat_id=...
→ {"conhecido": true, "nome": "...", "nota": "..."} | {"conhecido": false}
```

Consulta direto em `contatos_importados` por `telefone_normalizado ==
chat_id` (o `chat_id` do WhatsApp já vem só com dígitos, mesmo formato).
Sem cache em memória feito `_handoff_chat_ids` — o volume de consulta é
baixo (uma por conversa nova, não por mensagem) e o dado muda pouco.

### 4. Injeção de contexto (novo plugin `pre_llm_call`)

Mesmo mecanismo do plugin `agendamento_contexto`
(`provision_tenant.py`, `AGENDAMENTO_CONTEXTO_PLUGIN_INIT`, linha 739) —
só que em vez de só ecoar o `sender_id` já disponível em memória, faz uma
chamada HTTP local pro endpoint acima (mesma rede do pod, latência
desprezível perto do round-trip do LLM):

```python
def on_pre_llm_call(*, sender_id: str = "", **_):
    if not sender_id:
        return None
    info = _consultar_contato_conhecido(sender_id)  # GET local :8091
    if not info or not info.get("conhecido"):
        return None
    nota = f" — nota: {info['nota']}" if info.get("nota") else ""
    return {"context": f"[Sistema] Este contato já era cliente antes de usar o AtendPraGente{nota}."}
```

Registrado como plugin de usuário separado (`/opt/data/plugins/`), opt-in
via `plugins.enabled`, mesma convenção de `enable_agendamento_context_plugin`.
Só ativa pra tenants que de fato importaram uma lista — plugin sem dado
nenhum na coleção simplesmente nunca injeta nada (comportamento hoje,
zero mudança pra quem não usa a feature).

## Fora de escopo desta rodada

- **Import do `.txt` de "Exportar conversa"** (opção B) — parsing do
  formato de export do WhatsApp (nomes de arquivo, timestamps, mídia
  omitida) fica pra uma V2 se o resumo simples (V1) não for suficiente.
- **Resumo automático via LLM do conteúdo antigo** — só faz sentido depois
  de já ter B implementado; não há texto de conversa nenhum na V1 pra
  resumir.
- **Sync contínuo/automático** — a lista é importada uma vez (ou
  reimportada manualmente); não há integração ativa com o celular do
  tenant nem polling de nada.
- **Disparo de campanha de reengajamento** — o usuário decidiu que o
  objetivo agora é só dar contexto ao bot, não gerar lista pra disparo
  ativo. Se isso vier depois, a mesma coleção `contatos_importados` já
  serve de base (é só decisão de UI/fluxo, não de dado).

## Decisões

- **Campo `nota` no CSV: livre** — o tenant escreve o que quiser (ex.
  "cliente desde 2023, comprava sapato social"), sem formato fixo. É esse
  texto que entra direto na injeção de contexto do plugin (seção acima).
- **Import fica dentro de Configurações** (`templates/configuracoes.html`,
  mesmo template que já reúne o resto da config do tenant) — não vira
  seção nova no menu do painel. Botão de baixar modelo + upload do CSV,
  mesmo componente visual do import de catálogo, numa nova subseção
  (ex. "Contatos antigos") ao lado das já existentes.
