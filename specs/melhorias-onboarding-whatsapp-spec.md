# Spec técnica — Melhorias no Onboarding (Embedded Signup)

Baseado em `specs/melhorias-onboarding-whatsapp.md` (doc de conteúdo/UX). Este
documento fecha as duas pendências levantadas lá e traduz cada sugestão em
mudança concreta nos arquivos de `tools/onboarding-service/`.

## 1. Como o fluxo funciona hoje (achado ao ler o código)

```
GET  /signup                        → signup.html (landing + botão FB.login)
POST /api/signup/callback           → troca code por access_token, cria o
                                       signup no Mongo, devolve {next: "/signup/{id}/form"}
GET  /signup/{id}/form              → form.html (wizard de 7 passos: SOUL + plano + cobrança)
POST /signup/{id}/form              → valida, gera checkout Asaas (ou provisiona
                                       direto se for convite gratuito), redireciona
GET  /signup/{id}/aguardando        → aguardando.html (poll de status a cada 4s)
POST /api/asaas/webhook             → confirma pagamento → _run_provisioning()
GET  /signup/{id}/done              → done.html (só depois de status == "live")
```

Fonte: `tools/onboarding-service/app/main.py:170-517`.

## 2. Pendências do doc original — resolvidas

**"Tem tela de sucesso separada depois do embedded signup, ou o modal só
fecha?"** — Nenhuma das duas. O modal do FB fecha e o browser é redirecionado
**imediatamente para o Passo 1 do wizard de SOUL** (`form.html`), sem
nenhuma tela intermediária. `done.html` existe, mas só aparece bem depois,
após pagamento confirmado e provisionamento concluído — é a tela errada pro
item "confirmação pós-signup" do doc.

→ **Decisão:** o bloco de confirmação (item 4 do doc) não vira página nova.
Vira um cabeçalho de confirmação no topo do Passo 1 do `form.html`, porque é
ali que o cliente cai logo depois de fechar o modal da Meta — é o único lugar
onde a "saída" do embedded signup e o "início" de outra coisa se encontram.

**"Definir o tom do texto"** — Confirmado: mesmo tom direto/informal já usado
em `signup.html` ("Enquanto você dorme, seu zap continua respondendo") e nos
eyebrows de `form.html` ("Vamos começar", "Show, agora", "Quase lá").

## 3. Lacuna adicional encontrada (não estava no doc original)

Pra mostrar "conectamos o número (11) 9XXXX-XXXX / Nome da Empresa" no
cabeçalho do Passo 1, falta o dado: hoje `signup_callback` só grava
`waba_id` e `phone_number_id` (IDs internos da Meta), nunca o número visível
nem o nome verificado do WABA. É preciso uma chamada adicional à Graph API.

`tools/onboarding-service/app/meta_client.py` só tem `exchange_code_for_token`
— nenhuma chamada de leitura de metadados de telefone existe ainda.

## 4. Mudanças por arquivo

### 4.1 `templates/signup.html` — bloco "o que vai acontecer" (doc item 1)

Inserir entre `.g-benefits` (linha ~214) e `.g-cta` (linha ~216), goal:
baixar ansiedade antes do clique, sem alongar demais a landing (ela é
propositalmente curta/emocional — não virar uma parede de texto).

```html
<ol class="g-steps">
  <li>Você faz login com sua conta do Facebook</li>
  <li>A Meta pede pra criar (ou escolher) o Portfólio Empresarial da sua empresa</li>
  <li>Você confirma seu número por SMS ou ligação</li>
</ol>
```
CSS mínimo reaproveitando os tokens já definidos no arquivo (`--ink-soft`,
`--leaf-deep` etc. — conferir bloco `<style>` no topo do arquivo antes de
inventar cor nova).

### 4.2 Aviso sobre Portfólio Empresarial (doc item 2)

Mesma área, logo abaixo do `<ol>` acima, como parágrafo de aviso (reusar
padrão visual de `.invite-expired`, que já existe no arquivo linha 194, com
o emoji ⚠️):

```html
<p class="g-warn">⚠️ Se sua empresa já tem um Portfólio Empresarial no
Facebook, selecione ele em vez de criar um novo.</p>
```

### 4.3 Aviso sobre o número de telefone (doc item 3)

Mesmo bloco, texto:

```html
<p class="g-warn">Esse número passa a funcionar pelo WhatsApp Business API.
Se ele já tem o app do WhatsApp normal instalado, você vai precisar
desinstalar o app desse número depois de conectar.</p>
```

### 4.4 Confirmação pós-signup (doc item 4 — reescopo, ver seção 2)

**Backend — `app/meta_client.py`:** nova função

```python
def get_phone_number_details(phone_number_id: str, access_token: str) -> dict:
    """GET /{phone_number_id}?fields=display_phone_number,verified_name.
    Best-effort: chamado logo após exchange_code_for_token, com o mesmo
    access_token de curto prazo do cliente."""
```
Segue o mesmo padrão de tratamento de erro de `exchange_code_for_token`
(`MetaApiError` em `HTTPError`), mas **não deve travar o cadastro** se
falhar — é só um enriquecimento cosmético do Passo 1.

**Backend — `app/main.py:187-212` (`signup_callback`):** depois de obter
`access_token`, chamar `get_phone_number_details` em `try/except
MetaApiError` (log e segue sem os campos se falhar), e persistir
`display_phone_number` / `verified_name` no `store.create_signup(...)`
(assinatura em `app/store.py:27` aceita só `waba_id, phone_number_id,
access_token` hoje — precisa de dois parâmetros novos opcionais).

**Backend — `app/store.py:27-45` (`create_signup`):** adicionar
`display_phone_number: str | None = None, verified_name: str | None = None`
como campos do doc inserido.

**Frontend — `templates/form.html`, Passo 1 (linha ~254-256):** cabeçalho de
confirmação antes do `<h2>Me conta sobre o seu negócio</h2>`, condicional
(só renderiza se os dados vieram da Meta):

```html
{% if display_phone_number %}
<p class="g-confirm">✅ Conectamos o número {{ display_phone_number }}{% if verified_name %} ({{ verified_name }}){% endif %}.</p>
{% endif %}
```

**Backend — `app/main.py:215-222` (`form_page`):** passar
`signup.get("display_phone_number")` e `signup.get("verified_name")` pro
contexto do template.

### 4.5 Mini FAQ (doc item 5)

Em `signup.html`, um `<details>` nativo logo abaixo do aviso de Portfólio —
sem JS extra, sem custar peso de carregamento à landing:

```html
<details class="g-faq">
  <summary>Já tenho um Portfólio Empresarial, mas não sei onde acessar</summary>
  <dl>
    <dt>Conta pessoal do Facebook</dt><dd>seu login, com seu nome.</dd>
    <dt>Página do Facebook</dt><dd>a página pública da sua empresa no Facebook (curtidas, posts).</dd>
    <dt>Portfólio Empresarial (Business Manager)</dt><dd>o painel de administração onde ficam WhatsApp, anúncios e páginas da empresa — é esse que o cadastro pede.</dd>
  </dl>
</details>
```

## 5. Fora de escopo

- Não mexe em `done.html`, `aguardando.html` nem no fluxo de pagamento.
- Não adiciona chamada de Graph API que possa falhar de forma bloqueante —
  `get_phone_number_details` é sempre best-effort.
- Não altera `provision_tenant.py` nem o schema de `signups` além dos dois
  campos novos opcionais.

## 6. Critérios de aceite

- `/signup` mostra os 3 passos, aviso de Portfólio, aviso de número e o FAQ
  antes do botão, sem quebrar o layout mobile existente.
- Um cadastro real via Embedded Signup grava `display_phone_number` e
  `verified_name` no documento de `signups` (conferir no Mongo).
- Se a chamada à Graph API de metadados falhar, o cadastro segue normalmente
  e o Passo 1 do form simplesmente não mostra o cabeçalho de confirmação
  (sem erro pro usuário).
- Passo 1 do wizard mostra "Conectamos o número..." quando os dados existem.
