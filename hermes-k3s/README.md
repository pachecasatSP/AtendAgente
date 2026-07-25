# Migração do Hermes para K3s na Hetzner

Manifestos versionados para rodar o Hermes Agent num cluster K3s single-node na
Hetzner (CX32), com webhook estável em `bot.colocar-me.com.br` e HTTPS
automático. Preparado para receber os projetos do Azure depois, cada um no seu
namespace.

## O que este repositório resolve

- **Fim do túnel frágil.** O webhook passa a ser `https://bot.colocar-me.com.br`,
  URL fixa, em vez do quick tunnel do cloudflared que muda a cada reinício.
- **Fim dos 498 MB.** A CX32 tem 8 GB — folga real para o agente e para religar
  ferramentas com backend Docker no futuro.
- **Base para o multi-tenant e para o Azure.** Cluster K3s com um namespace por
  projeto; adicionar cargas depois é acrescentar namespaces, não recomeçar.

## Estrutura

```
hermes-k3s/
├── cluster/                      # configuração de nível de cluster
│   ├── 01-clusterissuer-staging.yaml
│   └── 02-clusterissuer-prod.yaml
├── hermes/                       # o Hermes, no namespace hermes
│   ├── 00-namespace.yaml
│   ├── 01-pvc.yaml
│   ├── 02-secret.EXAMPLE.yaml    # TEMPLATE — não versionar o preenchido
│   ├── 03-deployment.yaml
│   ├── 04-service.yaml
│   └── 05-ingress.yaml
├── scripts/
│   └── setup-k3s.sh              # instala K3s + cert-manager no servidor
└── README.md
```

## Pré-requisitos

1. **CX32 provisionada** na Hetzner, Ubuntu 24.04, com sua chave SSH.
2. **Firewall da Hetzner** liberando as portas **22, 80 e 443**. As portas 80 e
   443 são obrigatórias: 80 para o desafio HTTP-01 do Let's Encrypt, 443 para o
   webhook em si.
3. **DNS**: acesso ao painel do Registro.br para criar o registro A de
   `bot.colocar-me.com.br`.

## Ordem de execução

### Passo 1 — Instalar o cluster (no servidor, via SSH)

```bash
ssh root@<IP_DA_CX32>
# copie o setup-k3s.sh para o servidor (scp ou cole com nano), depois:
bash setup-k3s.sh
```

O script instala K3s (com Traefik embutido), Helm e cert-manager, e no final
imprime o IP público da máquina.

### Passo 2 — Apontar o DNS

No Registro.br, na zona de `colocar-me.com.br`, crie:

```
Tipo: A   |   Host: bot   |   Valor: <IP_PÚBLICO_DA_CX32>
```

Aguarde propagar (minutos a algumas horas). Confira com:

```bash
dig +short bot.colocar-me.com.br
```

Quando retornar o IP da CX32, siga. **Não aplique o Ingress antes disso** — o
Let's Encrypt precisa alcançar o servidor pelo domínio para emitir o certificado.

### Passo 3 — Aplicar os issuers

Antes, edite `<SEU_EMAIL>` nos dois arquivos de `cluster/`.

```bash
kubectl apply -f cluster/
kubectl get clusterissuer
```

### Passo 4 — Criar o Secret com as credenciais

A forma mais direta na migração é reaproveitar o `.env` que já funciona na
Oracle. Copie o `~/.hermes/.env` da máquina Oracle para onde você está rodando o
kubectl, e:

```bash
kubectl create secret generic hermes-env --namespace hermes --from-env-file=.env
rm .env    # apague depois de criar — o segredo já está no cluster
```

(O namespace `hermes` precisa existir antes; ele é criado no passo 5, então rode
`kubectl apply -f hermes/00-namespace.yaml` primeiro, ou crie o secret depois do
passo 5.)

### Passo 5 — Aplicar o Hermes

```bash
kubectl apply -f hermes/
kubectl -n hermes get pods,svc,ingress,certificate -w
```

Acompanhe até o pod ficar `Running` e o `certificate` ficar `True`.

### Passo 6 — Trocar staging por produção

O Ingress vem apontando para `letsencrypt-staging` (certificado de teste, some
rate limit). Quando o certificate de staging aparecer como `Ready=True`,
confirmando que o fluxo funciona:

1. Em `hermes/05-ingress.yaml`, troque `letsencrypt-staging` por `letsencrypt-prod`.
2. `kubectl apply -f hermes/05-ingress.yaml`
3. Force a reemissão, se necessário:
   `kubectl -n hermes delete secret hermes-bot-tls`
4. Aguarde o novo certificate ficar `True`.

Teste no navegador: `https://bot.colocar-me.com.br/health` deve responder com o
JSON de saúde do gateway, com cadeado válido.

### Passo 7 — Apontar o webhook na Meta

No App Dashboard da Meta → WhatsApp → Configuration → Webhook:

- **Callback URL:** `https://bot.colocar-me.com.br/whatsapp/webhook`
- **Verify Token:** o mesmo do `.env` (`WHATSAPP_CLOUD_VERIFY_TOKEN`)

Reassine o campo `messages`. Mande um "oi" de teste e confirme pelo log:

```bash
kubectl -n hermes logs -f deploy/hermes
```

### Passo 8 — Desligar a Oracle

Só depois de confirmar que o bot responde pela Hetzner, desligue/destrua a
instância Oracle para não deixar recurso órfão. Guarde um backup do `~/.hermes/`
da Oracle antes, por segurança.

## Passo 9 — Expor a API OpenAI-compatible (`/agent-api`)

Usada pelo `re-colocar-me` (repo `core`) pra rotear as sugestões de IA (bio,
apresentação, skills) pelo Hermes em vez de bater direto numa API externa.

1. Gere um valor forte para `API_SERVER_KEY` (ex: `openssl rand -hex 32`).
2. Adicione ao Secret `hermes-env` já existente no cluster (edite direto, sem
   recriar do zero):
   ```bash
   kubectl -n hermes patch secret hermes-env \
     --type=merge \
     -p "{\"stringData\":{\"API_SERVER_ENABLED\":\"true\",\"API_SERVER_KEY\":\"<seu-valor>\"}}"
   kubectl -n hermes rollout restart deploy/hermes
   ```
3. Aplique o Deployment/Service atualizados (portas novas) e o Ingress novo:
   ```bash
   kubectl apply -f hermes/03-deployment.yaml
   kubectl apply -f hermes/04-service.yaml
   kubectl apply -f hermes/06-ingress-agent-api.yaml
   ```
   Se o `apply` do `06-ingress-agent-api.yaml` falhar com
   `no matches for kind "Middleware"`, o Traefik deste cluster espera o grupo
   antigo `traefik.containo.us/v1alpha1` — troque no `apiVersion` do
   `Middleware` e na annotation `router.middlewares` do arquivo, e reaplique.
4. Teste:
   ```bash
   curl -s https://bot.colocar-me.com.br/agent-api/v1/chat/completions \
     -H "Authorization: Bearer <seu-valor>" \
     -H "Content-Type: application/json" \
     -d '{"model":"hermes-agent","messages":[{"role":"user","content":"diga oi"}]}'
   ```
   Deve responder `200` com um `choices[0].message.content`. Guarde esse
   mesmo `API_SERVER_KEY` — é o `ApiKey` que vai em
   `re-colocar-me-core/appsettings.Development.json`, seção
   `HermesAgentOptions`, com `BaseAddress` = `https://bot.colocar-me.com.br/agent-api/`.

## Comandos do dia a dia

```bash
kubectl -n hermes get pods                    # estado
kubectl -n hermes logs -f deploy/hermes       # logs ao vivo
kubectl -n hermes rollout restart deploy/hermes   # reiniciar
kubectl -n hermes exec -it deploy/hermes -- sh    # entrar no container
```

## Notas de segurança

- **Nunca** versione o Secret preenchido nem o `.env` com valores reais. O
  `.gitignore` já cobre os padrões comuns.
- O Secret guarda as credenciais em base64 dentro do cluster (não é
  criptografia forte). Para produção séria com múltiplos clientes, considere um
  gerenciador de secrets (Sealed Secrets, External Secrets) — mas para o Hermes
  único, o Secret nativo é aceitável.
- Mantenha o firewall da Hetzner restrito: 22 (idealmente só do seu IP), 80 e
  443 abertos ao mundo (necessários para Let's Encrypt e webhook).

## Quando trouxer o Azure

Cada projeto do AKS vira um namespace novo aqui. Os pontos de tradução
(StorageClass do Azure → local-path/Longhorn, LoadBalancer → ServiceLB/MetalLB,
Ingress → Traefik, Key Vault → Secret/External Secrets) são decididos projeto a
projeto. Este cluster já está pronto para recebê-los — é só `kubectl apply` dos
manifestos adaptados, sem tocar no Hermes.
