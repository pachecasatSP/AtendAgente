# Interpretação de mensagens de áudio (voice notes do WhatsApp)

## Contexto

Cliente manda um áudio pro bot de um tenant (`novo-negocio`) e recebe,
em sequência: (1) uma mensagem em **inglês** que parece o eco de algo
transcrito, e (2) uma resposta em português dizendo "Consigo receber a
mensagem, mas não consigo ouvir o áudio diretamente por aqui. Se puder,
escreve o que você precisa...".

Investigação direta no pod (`kubectl exec` read-only, autorizado pelo
usuário) mostrou que **não é um recurso a construir do zero** — o
`hermes-agent` já baixa o áudio do WhatsApp Cloud API
(`whatsapp_cloud_patched.py`, fase 4, `_download_media_to_cache`) e já
tem um bloco `stt:` inteiro no `config.yaml`, ativo (`enabled: true`).
O problema é que esse bloco está **mal configurado e sem backend
funcional**, então todo áudio falha silenciosamente e o próprio
framework (ou o modelo, sem transcript de verdade) devolve a mensagem
de desculpa acima.

## O que foi encontrado (2026-08-21, pod `novo-negocio-hermes`)

`cat /opt/data/config.yaml` (bloco `stt:`, resumido — ver
`hermes config get stt` pro schema completo com todos os provedores
suportados: `local`, `groq`, `openai`, `mistral`, `xai`, `elevenlabs`,
`deepinfra`):

```yaml
stt:
  enabled: true
  echo_transcripts: true   # ecoa a transcrição como mensagem própria antes de responder
  language: en              # força a transcrição para INGLÊS
  local:
    model: base
  # nenhuma chave "provider:" definida
```

Três problemas, nenhum deles decisão consciente de ninguém — é o
default herdado da imagem `nousresearch/hermes-agent`, nunca tocado
por `provision_tenant.py`:

1. **Nenhum backend funciona.** Sem `stt.provider` explícito, o
   `transcription_tools.py` do próprio pacote (`/opt/hermes/tools/`,
   inspecionado direto no container) cai no provedor `local`. Mas
   `python3 -c "import faster_whisper"` e `import whisper` no pod
   devolvem `ModuleNotFoundError` — **o pacote de Whisper local nem
   está instalado** na imagem. Nenhuma env var de provedor de nuvem
   (`OPENAI_API_KEY`, `GROQ_API_KEY`, etc.) está setada no Secret do
   tenant tampouco. Resultado: toda tentativa de transcrição falha, o
   agente vê um áudio sem texto e responde com a desculpa genérica.
2. **`language: en`** força a saída da transcrição pro inglês
   (mesmo quando/se o backend funcionasse) — daí a primeira mensagem
   em inglês que o usuário viu.
3. **`echo_transcripts: true`** manda o texto transcrito de volta como
   mensagem própria do bot antes de responder — comportamento que o
   usuário já decidiu que não quer (ver decisão abaixo).

## Decisões (conversadas com o usuário, 2026-08-21)

- **Backend: Groq (`whisper-large-v3-turbo`) via API própria da
  Groq**, não local e não OpenAI.
  - Local foi descartado: o pod roda com **limit 1 vCPU / 1Gi**
    (confirmado via `kubectl get deploy ... -o jsonpath resources`,
    mesma faixa apertada do teste de carga real documentado no
    roadmap) — rodar Whisper localmente competiria por CPU com o
    próprio atendimento, e o pacote teria que ser reinstalado toda vez
    que a imagem `nousresearch/hermes-agent:latest` for repuxada
    (mesmo padrão frágil de "patch de vendor se perde no pull" já
    registrado noutras partes do projeto).
  - **Groq não é roteável pelo OpenRouter** — confirmado lendo
    `transcription_tools.py` no próprio container: o provedor `groq`
    chama `https://api.groq.com/openai/v1` direto, com `GROQ_API_KEY`
    própria. OpenRouter não proxeia transcrição de áudio, só chat
    completions — não dá pra reaproveitar a `OPENROUTER_API_KEY`
    existente. Uma `GROQ_API_KEY` nova é inevitável.
  - Preço real da Groq confirmado (não estimativa): free tier = 20
    req/min, 2.000 req/dia, até 8h de áudio/dia (28.800
    audio-segundos), mínimo de 10s cobrado por requisição. Pago (se
    estourar, sem precisar cartão pra começar): **US$0,04/hora de
    áudio** pro Turbo. No volume atual (poucos tenants live, pico de
    ~10 conversas simultâneas medido no teste de carga), o free tier
    cobre folgado.
- **`GROQ_API_KEY` compartilhada entre todos os tenants**, mesmo
  padrão já usado hoje pra `OPENROUTER_API_KEY` — decisão forçada
  pela realidade da Groq, não só preferência: a Groq **não tem API de
  gerenciamento** pra criar chaves programaticamente (confirmado —
  só criação manual no console, por team owner/developer), então
  gerar uma chave nova por tenant dentro de `provision_tenant.py` não
  é tecnicamente possível hoje. Uma chave só, criada uma vez à mão em
  `console.groq.com`, entregue como Secret do jeito de sempre (nunca
  pelo chat).
- **`language: pt`** (não `en`, não vazio) — força a transcrição pro
  português, consistente com o público do AtendPraGente.
- **`echo_transcripts: false`** — o bot responde direto à pergunta
  contida no áudio, tratando o texto transcrito como se fosse
  digitado. Não ecoa "entendi que você disse X" antes de responder.

## Mudanças a aplicar

### 1. Novo secret compartilhado

Criar `GROQ_API_KEY` em `console.groq.com` (conta AtendPraGente/Ac
Soluções) e adicionar como mais uma env var obrigatória em
`provision_tenant.py`, ao lado de `OPENROUTER_API_KEY`:

- `REQUIRED_ENV` (linha ~48): incluir `"GROQ_API_KEY"`.
- `build_secret_manifest()` (linha ~658): incluir
  `GROQ_API_KEY: "{env['GROQ_API_KEY']}"` no `stringData` do Secret
  `{prefix}-env`, junto de `OPENROUTER_API_KEY`.
- Docstring do topo do arquivo (linha ~17): adicionar
  `export GROQ_API_KEY=...` ao exemplo de uso.

### 2. Config do STT no provisionamento

Estender `apply_display_defaults()` (mesma função que já ajusta
`display.memory_notifications`, `model.max_tokens`, `model.default`
via `hermes config set`) com três chamadas novas:

```
hermes config set stt.provider groq
hermes config set stt.language pt
hermes config set stt.echo_transcripts false
```

Nenhuma mudança em `stt.groq.model` — o default do framework já é
`whisper-large-v3-turbo`, que é a escolha decidida acima.

### 3. Tenants já em produção

`linda-ana-calcados`, `novo-negocio` e `sandbox-tenant` já existem e
não passam de novo por `provision()` — aplicar os mesmos três
`hermes config set` (mais o Secret atualizado com `GROQ_API_KEY` +
`kubectl rollout restart`) manualmente nos três, mesmo padrão já usado
quando `model.default` foi corrigido em todos os tenants live
(2026-08-19).

## Fora de escopo desta rodada

- **Diarização / múltiplos falantes**: a Groq expõe isso só via
  ElevenLabs (`diarize` no bloco `elevenlabs:`), não no provedor Groq
  escolhido. Não é caso de uso relevante pra atendimento 1:1 no
  WhatsApp.
- **Limite de duração/tamanho de áudio**: o cap de 16MB por áudio já é
  imposto pelo próprio `whatsapp_cloud_patched.py`
  (`_MEDIA_SIZE_LIMITS["audio"]`) antes mesmo de chegar no STT: não
  precisa de trava adicional agora.
- **Confirmação/eco da transcrição**: decidido explicitamente que não
  (ver decisões acima). Se no futuro a taxa de erro de transcrição se
  mostrar alta o suficiente pra preocupar, revisitar
  `echo_transcripts: true` como mitigação.
