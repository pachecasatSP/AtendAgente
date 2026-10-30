# Infraestrutura do AtendPraGente na AWS

> Estado: **em produção (2026-10-30)**, com pendências na seção 11. Substitui, como descrição do que está no ar, tudo o que as specs, READMEs e o roadmap deste repositório dizem sobre os dois servidores da Hetzner (`62.238.103.17`, `ubuntu-16gb-hel1-1`, tenants; `2.28.15.6`, `ubuntu-4gb-fsn1-1`, Duda/backoffice), que **deixaram de existir para nós em 2026-10-29** quando a conta ficou inacessível. O AtendPraGente foi reconstruído **do código, sem nenhum dado dos discos** (decisão do dono do produto: os dados eram de teste). As specs anteriores **não foram reescritas**: receberam uma nota `Atualização (2026-10-30)`. Fonte da verdade da infraestrutura: repositório `infra-aws` (`docs/plano-atendpragente.md`, `k8s/atendpragente/`, `scripts/atendpragente/`).

## Contexto

O roadmap multi-tenant (`roadmap-multi-tenant.md`) foi escrito sobre dois servidores Hetzner com o código servido por `hostPath` (`/root/atendagente-tools`) e `pip install` a cada início de pod. A Fase 13 ("código em imagem Docker") estava **planejada, não iniciada**. Com a perda dos servidores ela foi **feita de verdade**, porque a reconstrução não tinha servidor de onde copiar nada. Esta spec registra a topologia nova, o que mudou no código de provisionamento e o que ficou pendente.

Decisões do dono (2026-10-30): reconstruir **tudo**; **um nó único** `m7i.large` por enquanto (a cota de vCPU da AWS é 8 e está esgotada: os dois nós previstos, `atend-admin` e `atend-tenants`, não cabiam); painel da Duda reescrito **a partir do painel do tenant**, só com agenda (sem catálogo); **registry próprio** (GHCR) levando em conta o provisionamento dinâmico; **todas as credenciais externas recriadas**.

## Topologia

| Item | Valor |
|---|---|
| Conta / região | AWS `492715478912`, `sa-east-1`, perfil SSO `recolocarme` |
| Nó | `recolocarme-staging-atendpragente` (`i-0b9c470b5b32d4c7f`), `m7i.large` (2 vCPU, 8 GB), volume de 80 GB, k3s `v1.36.3+k3s1` de nó único, independente dos nós do re.colocarme e do HupyFlow |
| IP público | Elastic IP **`18.228.100.213`** (substitui `62.238.103.17` e `2.28.15.6`) |
| Acesso administrativo | sem SSH: túnel AWS SSM, porta local `16445` (`infra-aws/scripts/tunel-ssm.ps1 -No atendpragente -Porta 16445`); contexto `atendpragente-staging` |
| Namespace `atendagente` | tenants (`<tenant>-hermes`, `<tenant>-hermes-panel`), Mongo compartilhado, `calendar-mcp`, CDN, CronJobs de tenant |
| Namespace `atendagente-admin` | `onboarding-service`, `admin-mcp`, backoffice (+ Mongo próprio), Duda (`duda-hermes`, `duda-hermes-panel`), CronJob `lixeira-watch` |
| TLS | cert-manager + `ClusterIssuer letsencrypt-prod` (Cloudflare) |
| DNS (zona `atendpragente.com.br`, Cloudflare) | A para `18.228.100.213`, sem proxy, TTL 300: `onboarding`, `admin-mcp`, `backoffice`, `duda`, `cdn`, e um host `<tenant>` por cliente (criado pelo provisionamento) |
| Bucket | `recolocarme-staging-atend-492715478912` (S3, `sa-east-1`, ACLs permitidas só nele: `BucketOwnerPreferred`); prefixos públicos `atendpragente-pics/` e `atendpragente-agenda-ics/`, privado `atendpragente-comprovantes/`; usuário IAM `recolocarme-staging-atend-blob` |
| CDN | `cdn.atendpragente.com.br` = nginx (`object-storage-cdn-proxy`) que reescreve o `Host` para o bucket e só serve os dois prefixos públicos; substitui o proxy do Object Storage da Hetzner |

Os registros DNS dos antigos `linda-ana-calcados` e `loja-de-teste2` apontavam para `62.238.103.17`; o segundo foi removido e o primeiro foi atualizado quando o tenant foi recriado.

## Imagens e registry (Fase 13, feita)

Cinco imagens em **`ghcr.io/pachecasatsp/atendpragente-<nome>:<commit do bot-hermes>`** (privadas; o cluster puxa com `ghcr-pull-secret`; tags imutáveis por commit, nunca `:latest`): `tools` (onboarding, `admin-mcp`, `calendar-mcp`, scripts dos CronJobs, `kubectl` v1.36.3 com checagem de sha256), `panel` (tenant-panel), `sync` (mongo-sync), `backoffice` e `hermes` (Hermes por digest `nousresearch/hermes-agent@sha256:d4da4a40…` + `whatsapp_cloud_patched.py` embutido). Base `python@sha256:aa943ce4…`, usuário 10001, código somente leitura, dependências travadas em `infra-aws/atendpragente/locks/`. Construção: `infra-aws/scripts/atendpragente/construir-imagens.sh <commit>` (via `git archive`, independe da árvore de trabalho). Smoke test 10/10: serviços sobem como não-root e respondem `/health`; o Hermes com o adaptador de WhatsApp com patch recebe um webhook assinado (HMAC) e roda o agente.

**Achado:** o adaptador `whatsapp_cloud_patched.py` (2.456 linhas) foi escrito para uma versão antiga do Hermes; na versão fixada (0.21.5) o adaptador nativo tem 1.021 linhas. O patch importa e atende webhooks na 0.21.5 (provado localmente e depois com mensagem real do `linda-ana-calcados`); recursos avançados dele seguem sem prova.

## Provisionamento dinâmico (mudanças em `provision_tenant.py`)

- As imagens do pod de cada tenant vêm da configuração do onboarding: `TENANT_HERMES_IMAGE`, `TENANT_PANEL_IMAGE`, `TENANT_SYNC_IMAGE` (**sem default**: se faltar, o provisionamento falha de propósito) e `TENANT_IMAGE_PULL_SECRET` (opcional; gera `imagePullSecrets`). Sem `hostPath`, sem ConfigMap de código (`mongo-sync-script`, `whatsapp-cloud-patch` deixam de ser usados), sem `pip install` no boot. O sync roda com o uid do Hermes (10000) para ler o volume.
- O onboarding usa o **ServiceAccount do pod** (`onboarding-service`, em `atendagente-admin`) com uma `Role tenant-provisioner` **só no namespace `atendagente`** (Deployments, Services, Secrets, PVCs, Ingresses, `pods/exec`, jobs): substitui o kubeconfig restrito em Secret da Hetzner. Verificado: lista os recursos dos tenants e é negado em `kube-system`.
- `provision_tenant.create_dns_record` agora é **idempotente (upsert)**: antes só fazia POST e, havendo um A do mesmo nome com outro IP, o Cloudflare aceitava os dois (metade das conexões ia para o IP morto; aconteceu com o `linda-ana-calcados`).
- `provisioning_env()` do onboarding passa a incluir **`GROQ_API_KEY`**: sem isso o primeiro provisionamento pelo onboarding dava `KeyError` em `build_secret_manifest`.
- O resumo final do provisionamento **não imprime mais** o verify token do webhook nem o link de configuração do painel quando chamado pelo onboarding (`provision(..., mostrar_segredos=False)`, padrão); só a CLI mostra. O stdout do onboarding vai para o log do pod, e logs são lidos e colados.
- Painel: `PANEL_MODE=admin` (padrão `tenant`) liga o **modo da Duda** na mesma imagem do painel: só conversas e agenda; vitrine, configurações, pedidos, catálogo, contatos, troca de número e cancelamento devolvem 404 e o menu os esconde.

## Serviços e CronJobs

Manifestos em `infra-aws/k8s/atendpragente/` (`rbac`, `mongo`, `cdn`, `calendar-mcp`, `admin`, `backoffice`, `duda`, `cronjobs`), pods não-root, `readOnlyRootFilesystem`, sem capabilities, Mongo e nginx por digest. Os **5 CronJobs nascem suspensos**: `agenda-lembrete` (a cada 15 min, **envia WhatsApp**), `pedido-retencao`, `usage-watch`, `lixeira-watch` (apaga tenants cancelados) e `backoffice-prospeccao` (consome a cota do Google Places). Ligar um a um, com `kubectl patch cronjob … -p '{"spec":{"suspend":false}}'`, depois de um `kubectl create job --from=cronjob/…` manual.

## A Duda

- Hermes no namespace `atendagente-admin` (`duda-hermes`, imagem `atendpragente-hermes`) + painel em `PANEL_MODE=admin` em `https://duda.atendpragente.com.br/painel`. Número `+55 11 92008-1743` ("Ac Soluções em Software", qualidade `GREEN`).
- O **SOUL** é `poc/SOUL-ac-solucoes.md` deste repositório (último commit 2026-08-19; cita as 13 ferramentas, as mesmas 13 do `admin-mcp` de hoje); cópia versionada em `infra-aws/atendpragente/duda/SOUL.md`. Alterações feitas direto no servidor depois de 19/08 não voltaram.
- Configuração aplicada por `infra-aws/scripts/atendpragente/configurar-duda.sh`: mesma receita dos tenants (`model.default openai/gpt-5.6-luna` — **nunca `gpt-5-luna`, que não existe** —, Groq para áudio em pt, `approvals.mode off`) e o MCP `admin` pela URL **pública** `https://admin-mcp.atendpragente.com.br/mcp`, porque o `admin-mcp` só aceita o `Host` público (proteção contra DNS rebinding em `TransportSecuritySettings`).
- O webhook **já apontava** para `https://duda.atendpragente.com.br/whatsapp/webhook` (assinatura do app na Meta, sem override por WABA); ligar o adaptador equivale a entrar no ar. O token da Meta precisa ser de **usuário do sistema** (um token de sessão expirou depois de uma hora e derrubou a validação); `preencher-credenciais.py duda-token` recusa tokens que expiram.
- Testes (mensagem assinada de um número fictício): respondeu na persona com os planos (Entrada R$ 84,90, Começando R$ 157,90, Crescendo R$ 717,90, 7 dias grátis, link de cadastro) e, sem PIN, **pediu o PIN** em vez de chamar ferramenta administrativa.

## Tenants

- `linda-ana-calcados` foi **recriado** pelo fluxo normal de cadastro com **convite gratuito** (sem cobrança): pods do Hermes (2/2 com o sync) e do painel, certificado, DNS e webhook da WABA por override; mensagem real recebida e respondida. Roda nas imagens `a534bd0` (ainda sem as correções do DNS e do log).
- `loja-de-teste2` **não** foi recriado.
- Perdido sem recuperação (sem backup, servidor inacessível): catálogos, pedidos, agenda, conversas, logins do painel e Secrets dos tenants antigos.

## Credenciais externas

Todas recriadas e gravadas por `infra-aws/scripts/atendpragente/preencher-credenciais.py <grupo>` (`cloudflare`, `openrouter`, `groq`, `meta`, `duda`, `duda-token`, `asaas`, `google`, `places`, `bucket`): valida no provedor com chamadas de leitura sem custo, grava no Secret certo sem imprimir, tira o item da anotação de pendentes e apaga o arquivo. **A Asaas está em produção** (`ASAAS_BASE_URL=https://api.asaas.com/v3`): um cadastro pago gera cobrança real; para testar sem cobrança usar convite gratuito. O `ASAAS_WEBHOOK_TOKEN` é gerado no cluster e precisa ser configurado no painel da Asaas.

## Arquivos a tocar (neste repositório)

- `specs/infraestrutura-aws-atendpragente.md`: esta spec (nova).
- `tools/provision-tenant/provision_tenant.py`: imagens configuráveis, sem `hostPath`/ConfigMap de código, `create_dns_record` idempotente, `imprimir_resumo` sem segredos.
- `tools/onboarding-service/app/main.py`: `GROQ_API_KEY` em `provisioning_env()`.
- `tools/tenant-panel/app.py`, `templates/index.html`: `PANEL_MODE=admin`.
- Testes novos: `tools/provision-tenant/tests/`, `tools/onboarding-service/tests/`, `tools/tenant-panel/tests/`.
- `specs/roadmap-multi-tenant.md`, `specs/catalogo-produtos.md`, `tools/*/README.md`, `poc/memoria-projeto-hermes.md`: nota `Atualização (2026-10-30)`.

## Verificação realizada

- Testes automatizados: provisionamento 18, onboarding 2, painel 6; scripts de infraestrutura com `kubectl`, `aws` e APIs de mentira (44 do preenchimento de credenciais, 27 de aplicar/segredos/pull secret/rotação de token do painel, 29 do DNS).
- No cluster: 10 pods `Running`; onboarding lista só o namespace `atendagente`; `/health` 200 em onboarding, backoffice e CDN; certificados `True`; webhook do tenant e da Duda verificado com o verify token do Secret (desafio devolvido) e recusado com token errado (403).
- Achados reais durante a verificação: `openssl` do Git Bash termina a saída com `\r` (afeta `base64`); um `aws head-bucket` falhou por herdar variáveis `AWS_*` do shell do operador; o `ligar-duda.sh` não reiniciava o Hermes com o pod já em 1 réplica; o kubeconfig era regravado com o nome de contexto errado.

## 11. Fora de escopo e pendências

- **Backups do Mongo (tenants e backoffice) e telemetria OTel do nó**: ainda não existem. Hoje os dados dos tenants vivem num único disco (só há snapshot diário do volume).
- **Reconstruir/atualizar** o `linda-ana-calcados` para as imagens mais novas (`3799ae0`).
- **Textos do Hermes que vazam para o cliente** na primeira resposta ("o `/help` mostra os comandos…", oferta de "montar um perfil curtinho seu"): desligar com `hermes config set` na Duda e avaliar nos tenants.
- **Agenda da Duda**: o `calendar-mcp` acha o tenant por um documento de signup no Mongo; a Duda não passa pelo onboarding, então precisa de um documento criado à mão para ligar a agenda dela (`CALENDAR_MCP_TOKEN` segue pendente).
- **CronJobs**: todos suspensos; decidir quando ligar cada um.
- Mensagens antigas que a Meta guardou por até 7 dias podem chegar de uma vez para a Duda agora que ela está no ar.
- **Dois nós** (`atend-admin` e `atend-tenants`) continuam desejáveis quando a cota de vCPU subir; o desenho já separa os namespaces para isso.
