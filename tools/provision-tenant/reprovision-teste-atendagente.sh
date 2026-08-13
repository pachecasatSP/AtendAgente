#!/usr/bin/env bash
# Roda NO SERVIDOR (root@62.238.103.17). Valida a Fase 3 (provision_tenant.py)
# de ponta a ponta reaproveitando o tenant de teste da Fase 1
# (teste-atendagente-hermes), sem que nenhuma credencial passe pelo
# terminal/chat do operador: lê tudo direto do Secret k8s já existente.
#
# O token da Cloudflare NUNCA é passado por variável de ambiente/linha de
# comando aqui — é lido de um arquivo local no servidor
# (/root/.cloudflare_api_token), que o operador cria manualmente por SSH
# direto (nunca colado no chat). Isso evita repetir o vazamento de token
# que aconteceu ao passá-lo como argumento de um comando remoto.
#
# Uso (no servidor):
#   # uma vez, direto no terminal SSH interativo (não pelo agente/chat):
#   umask 077 && cat > /root/.cloudflare_api_token   # cola o token, Ctrl-D
#   chmod 600 /root/.cloudflare_api_token
#
#   export CLOUDFLARE_ZONE_ID=5435cf54669fa51f002f1e2a8b59ae61  # zone colocar-me.com.br
#   export SERVER_IP=62.238.103.17
#   bash reprovision-teste-atendagente.sh
set -euo pipefail

CLOUDFLARE_TOKEN_FILE="${CLOUDFLARE_TOKEN_FILE:-/root/.cloudflare_api_token}"
if [ ! -s "$CLOUDFLARE_TOKEN_FILE" ]; then
  echo "ERRO: $CLOUDFLARE_TOKEN_FILE não existe ou está vazio." >&2
  echo "Crie com: umask 077 && cat > $CLOUDFLARE_TOKEN_FILE   (cola o token, Ctrl-D)" >&2
  exit 1
fi
export CLOUDFLARE_API_TOKEN
CLOUDFLARE_API_TOKEN="$(tr -d '[:space:]' < "$CLOUDFLARE_TOKEN_FILE")"

NAMESPACE=atendagente
SECRET=teste-atendagente-hermes-env

get() { kubectl -n "$NAMESPACE" get secret "$SECRET" -o "jsonpath={.data.$1}" | base64 -d; }

export WHATSAPP_ACCESS_TOKEN
WHATSAPP_ACCESS_TOKEN="$(get WHATSAPP_CLOUD_ACCESS_TOKEN)"
export WHATSAPP_APP_SECRET
WHATSAPP_APP_SECRET="$(get WHATSAPP_CLOUD_APP_SECRET)"
export OPENROUTER_API_KEY
OPENROUTER_API_KEY="$(get OPENROUTER_API_KEY)"

PHONE_NUMBER_ID="$(get WHATSAPP_CLOUD_PHONE_NUMBER_ID)"
WABA_ID="$(get WHATSAPP_CLOUD_WABA_ID)"

: "${CLOUDFLARE_ZONE_ID:?export CLOUDFLARE_ZONE_ID antes de rodar}"
: "${SERVER_IP:?export SERVER_IP antes de rodar}"

cd "$(dirname "$0")"

cat > tenants/teste-atendagente.yaml <<YAML
tenant_id: teste-atendagente
dominio: teste-atendagente.colocar-me.com.br

whatsapp:
  phone_number_id: "${PHONE_NUMBER_ID}"
  waba_id: "${WABA_ID}"

nome_negocio: "AtendAgente - Teste"
artigo_negocio: "o"

descricao_negocio: |
  Sou o atendimento de teste do AtendAgente, usado para validar o
  provisionamento automatizado de tenants (Fase 3 do roadmap).

descricao_publico: |
  Quem escreve aqui é a equipe validando o pipeline de provisionamento.

tom:
  descricao: "Português do Brasil, direto e objetivo."
  emoji: "nenhum"

servicos:
  - nome: Teste de provisionamento
    descricao: "Confirma que o pipeline de deploy automático de tenants funciona."
    publico_alvo: "Equipe de desenvolvimento do AtendAgente."

como_trabalhamos:
  Ambiente: "tenant de teste, não é atendimento real."

lacunas_conhecidas:
  - nada além do escopo de teste

escalacao:
  nome: "a equipe de desenvolvimento"
  telefone: "(11) 5000-0000"
  pronome: "ela"

exemplos: []
YAML

echo "== Rodando provision_tenant.py de verdade (sem --dry-run) =="
python3 provision_tenant.py tenants/teste-atendagente.yaml
