# Subida de Catálogos para o bot

## Contexto

Hoje o bot só sabe sobre produtos/serviços do que está escrito à mão no
SOUL.md (campo `servicos`, uma lista curta de {nome, descrição,
público_alvo} injetada como prosa no system prompt — ver
`tools/soul-generator/generate_soul.py`). Isso não escala pra um
catálogo de verdade (dezenas/centenas de itens com preço, foto,
categoria) nem dá pro cliente final ver o que o negócio vende sem
perguntar ao bot item por item.

Investigação no framework Hermes (via SSH no pod `hermes-duda`)
confirmou: **não existe RAG/busca em documento nativo**. A única
ferramenta é `read_file`, que lê um arquivo inteiro (texto puro, CSV,
XLSX, DOCX — não PDF, falta a dependência `firecrawl-anydoc` na imagem)
até um teto de ~100K caracteres, sem busca interna. Ou seja: o bot pode
"ler" um catálogo se ele existir como arquivo de texto no pod, mas não
existe indexação/busca — pra um catálogo pequeno-médio isso é
suficiente, pra um catálogo enorme seria preciso paginar (fora do
escopo desta v1).

Decisão de arquitetura (conversada com o usuário, revisada em
2026-08-16): produtos estruturados continuam no **Mongo compartilhado**
(já usado por sessions/messages/signups, particionado por `tenant_id`),
mas **sem rota `/painel/catalogo` própria** — a gestão do catálogo vira
uma seção dentro de `/painel/configuracoes`, mesmo padrão de fila já
usado pelo SOUL. A única rota pública nova é `/vitrine` (sem login — o
bot pode mandar esse link pro cliente final). Fotos saem do Mongo:
ficam num bucket S3-compatible na Hetzner Object Storage, servidas via
CDN da Cloudflare. O bot em si continua consultando via `read_file`,
contra um arquivo CSV **derivado** do Mongo (sem foto), sincronizado
pelo mesmo mecanismo assíncrono que já publica o SOUL.md.

Campos do produto: nome, slug, descrição, preço, foto (URL), categoria,
ativo/inativo.

## Modelo de dados

Coleção Mongo `catalogo_itens` (mesmo Mongo de `sessions`/`messages`/`signups`):
```
{
  _id: ObjectId,
  tenant_id: str,
  nome: str,
  slug: str,             # gerado na criação, único por tenant, CONGELADO
                          # (não recalcula se o nome mudar depois)
  descricao: str,
  preco: float,
  categoria: str,        # "" se não usar
  ativo: bool,            # default True
  foto_url: str|None,     # URL no CDN Cloudflare (origem: bucket Hetzner)
  criado_em: datetime,
  atualizado_em: datetime,
}
```

### Slug

- Gerado a partir do `nome` na criação do item: minúsculas, sem acento,
  espaço → `_`, remove pontuação
  (`Cadeirão de praia vermelha 10kg` → `cadeirao_de_praia_vermelha_10kg`).
- **Congelado**: editar o nome depois não recalcula o slug, pra não
  quebrar a vinculação com a foto já enviada.
- **Colisão**: se dois produtos gerarem o mesmo slug, o segundo recebe
  sufixo numérico (`-2`, `-3`, ...).
- É a chave de casamento entre produto e foto no import em massa (ver
  abaixo) — evita depender de o cliente adivinhar a regra de
  normalização ou cadastrar um SKU manual.

### Fotos — bucket + CDN

- Origem: bucket S3-compatible **já existente** na Hetzner Object
  Storage (`recolocare-me-blob`, compartilhado com outro produto do
  usuário — "recoloca-me" — rodando no mesmo cluster/namespace
  `consultor`, ver `infra_atendagente_k3s`). Tudo do AtendAgente vive
  sob o prefixo `atendpragente-pics/`, nunca solto na raiz, pra não
  misturar com o outro produto.
- **CNAME direto não funciona**: o Hetzner Object Storage não suporta
  domínio customizado em bucket — o backend valida que o header `Host`
  bata com o domínio dele (`<bucket>.fsn1.your-objectstorage.com`),
  rejeitando qualquer outro Host com HTTP 400, mesmo em path-style
  (confirmado testando direto, fora da Cloudflare). Documentado pelo
  próprio Hetzner ("Custom Domain with S3 Proxy").
- Solução aplicada: um **reverse proxy nginx** (`object-storage-cdn-
  proxy`, Deployment+Service+Ingress no namespace `atendagente`,
  imagem `nginx:alpine`, config via ConfigMap) que reescreve o `Host`
  pra `recolocare-me-blob.fsn1.your-objectstorage.com` antes de
  encaminhar — mesmo padrão de Ingress/cert-manager (Let's Encrypt via
  HTTP-01, Traefik) já usado pros tenants.
- Cloudflare fica na frente **desse proxy**, não do bucket direto:
  registro A `cdn.atendpragente.com.br` → IP do servidor
  (62.238.103.17), proxied (nuvem laranja) — cacheia extensões
  estáticas (jpg/png/webp) por padrão, sem precisar de Cache Rule
  customizada (o token de API do servidor só tem escopo de DNS, não
  Zone Settings/Cache Rules).
- Validado ponta a ponta 2026-08-16: upload via boto3 direto no
  bucket, leitura via `https://cdn.atendpragente.com.br/...` retornando
  o conteúdo certo tanto sem quanto com o proxy da Cloudflare ligado.
- Bucket é público-read (fotos de produto não são sensíveis, a vitrine
  já é pública).
- Upload redimensiona no navegador antes de enviar (mantém arquivos
  pequenos), mas sem o limite de 16MB/doc do Mongo, já que não fica
  mais inline.
- Metadado de uso (contagem de itens com foto, bytes no bucket) é
  registrado desde a v1, mesmo sem cobrança — vira gancho pra upsell
  de plano depois ("cliente que usa fotos paga um adicional").

## Fluxo — painel (dentro de Configurações + vitrine pública)

`tools/tenant-panel/app.py` (mesmo código base do painel de todos os
tenants AtendAgente — o tenant da Duda roda um painel à parte e **não**
recebe a funcionalidade de catálogo, não precisa atualizar lá):

- Seção nova em `templates/configuracoes.html` — lista os produtos do
  tenant, formulário de novo item, mesma linguagem visual do resto da
  página. Sem template `catalogo.html` separado, sem rota
  `/painel/catalogo`.
- `POST /painel/api/catalogo` — cria item (nome → gera slug, descrição,
  preço, categoria, ativo, foto opcional). Marca `catalogo_pending: True`
  no doc do `signups_col` (mesmo padrão do `soul_pending` já existente
  em `api_salvar_configuracoes`).
- `PUT/POST /painel/api/catalogo/{item_id}` — edita (nome pode mudar,
  slug não).
- `DELETE /painel/api/catalogo/{item_id}` — remove (e a foto associada
  no bucket, se houver).
- `POST /painel/api/catalogo/import` — import em massa via CSV/XLSX
  (colunas `nome,descricao,preco,categoria`). Cria os itens, gera os
  slugs, e **devolve pro usuário a lista `nome → slug`** (tela e/ou
  download) — é assim que ele sabe como nomear as fotos, sem precisar
  adivinhar a regra de normalização.
- `POST /painel/api/catalogo/fotos-bulk` — upload em massa (zip ou
  múltiplos arquivos), casa cada arquivo pelo nome (sem extensão) com o
  `slug` do item já existente, sobe pro bucket, grava `foto_url`. Itens
  sem correspondência ficam listados como "sem foto" pra completar
  depois pelo formulário unitário.
- Import de CSV/XLSX e upload de fotos em massa rodam **assíncronos**
  (fila, mesmo padrão do `catalogo_pending`/worker) — não bloqueiam a
  requisição HTTP, já que resize/upload de dezenas de imagens pode
  estourar timeout. Painel mostra status ("processando 40 itens...").
- `GET /vitrine` — **pública, sem `require_session`** — lista só os
  itens `ativo: True` do tenant, agrupados por categoria, com foto/
  preço/descrição. Template novo `vitrine.html`.
- Link novo no header do `index.html` apontando pra vitrine pública
  (ex: "ver vitrine" ao lado do link de Configurações).

## Fluxo — publicação pro bot (reaproveita o mecanismo do SOUL)

O painel não tem `kubectl`, só Mongo — mesma limitação já resolvida
pra edição de SOUL. Reaproveita a fila assíncrona:

1. `tools/onboarding-service/app/store.py` — novas funções
   `list_catalogo_pending_signups()` / `mark_catalogo_applied(signup_id)`,
   espelhando `list_soul_pending_signups`/`mark_soul_applied`.
2. `tools/onboarding-service/app/main.py` — o loop `_soul_apply_loop`
   (ou um irmão dele, mesmo intervalo `SOUL_APPLY_INTERVAL_SECONDS`)
   passa a também checar `catalogo_pending`: busca os itens `ativo: True`
   do tenant, monta um CSV compacto (`nome,preco,categoria,descricao` —
   sem foto/slug, o bot não precisa disso), publica via
   `publish_catalog()` — **sem restart**, porque `read_file` lê o
   arquivo ao vivo a cada chamada (diferente do SOUL.md, que só é
   carregado na inicialização do processo).
3. `tools/provision-tenant/provision_tenant.py` — nova função
   `publish_catalog(tenant_id, csv_text)`, igual a `publish_soul()` mas
   sem o `rollout restart`: só `kubectl_exec_stdin` escrevendo
   `/opt/data/catalogo.csv`.
4. **Só na primeira vez** que um tenant cadastra algum item (transição
   `catalogo_ativo: False → True` no `config`), também seta
   `soul_pending: True` pra republicar o SOUL.md com o parágrafo novo
   apontando pro catálogo — esse parágrafo é fixo, só cadastros
   seguintes de produto tocam o CSV, não o SOUL.

## SOUL.md — novo bloco condicional

`tools/soul-generator/generate_soul.py` + `SOUL.template.md`: novo
`${catalog_block}`, vazio se `config.get("catalogo_ativo")` for falso,
senão algo como:

> ## Catálogo de produtos
> Você tem acesso a um catálogo de produtos/serviços em
> `/opt/data/catalogo.csv` (nome, preço, categoria, descrição — um por
> linha). Use a ferramenta de leitura de arquivo pra consultar preços e
> detalhes quando o cliente perguntar sobre algo específico. Não invente
> item nem preço que não esteja lá — se não achar, diga que vai
> confirmar. Pra mostrar o catálogo inteiro, pode mandar o link da
> vitrine: `https://{dominio}/vitrine`.

## Limites da v1 (comunicar, não resolver agora)

- Sem PDF (dependência ausente na imagem do Hermes — mudança de imagem
  vendorizada, fora do alcance de `kubectl exec`).
- Sem busca dentro do arquivo — `read_file` lê tudo de uma vez, até
  ~100K caracteres. Suficiente pra um catálogo pequeno/médio; catálogos
  muito grandes (centenas de itens) seriam cortados. Painel pode avisar
  visualmente perto desse teto, sem bloquear.
- Vinculação de foto no import em massa é só por slug exato — sem UI de
  arrastar-e-soltar pra corrigir itens "sem foto" na v1 (fica pro
  formulário unitário já existente).
- Sem cobrança de upsell por uso de fotos na v1 — só o metadado de uso
  é registrado, a régua de cobrança fica pra depois.

## Arquivos a tocar

- `tools/tenant-panel/app.py` — rotas de CRUD/import/fotos-bulk +
  `/vitrine` pública
- `tools/tenant-panel/storage.py` — novo, upload S3-compatible pro
  bucket Hetzner (via CDN Cloudflare)
- `tools/tenant-panel/templates/configuracoes.html` — seção nova de
  catálogo
- `tools/tenant-panel/templates/vitrine.html` — novo
- `tools/tenant-panel/templates/index.html` — link pra vitrine
- `tools/soul-generator/generate_soul.py` + `SOUL.template.md` — bloco condicional
- `tools/provision-tenant/provision_tenant.py` — `publish_catalog()`
- `tools/onboarding-service/app/store.py` — helpers de fila
- `tools/onboarding-service/app/main.py` — loop de sincronização

## Verificação

1. Rodar `python -m py_compile` nos arquivos Python tocados.
2. Reprovisionar um tenant fake de teste (mesmo padrão usado antes,
   `teste-config-painel`), cadastrar produto com foto em
   Configurações → Catálogo, confirmar que aparece em `/vitrine` sem
   login.
3. Testar import em massa: subir CSV com alguns produtos, conferir que
   os slugs voltam certinho pro usuário, subir fotos nomeadas com os
   slugs, confirmar casamento correto e que itens sem foto correspondente
   aparecem como pendentes.
4. Conferir via `kubectl exec` que `/opt/data/catalogo.csv` foi
   escrito no pod depois do ciclo da fila (~5 min).
5. Mandar uma pergunta sobre produto pelo WhatsApp de teste e confirmar
   que o bot usa `read_file` pra responder com dado real do catálogo.
6. Deploy só no servidor AtendAgente (62.238.103.17) — o tenant da Duda
   **não** vai ter catálogo, então não replicar esse código lá.
7. Depois de validar, derrubar o tenant fake (mesmo ritual de limpeza:
   k8s + DNS + doc no Mongo, incluindo agora também os itens de
   `catalogo_itens` e os objetos correspondentes no bucket).
