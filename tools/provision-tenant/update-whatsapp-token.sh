#!/usr/bin/env bash
# Atualiza o WHATSAPP_CLOUD_ACCESS_TOKEN do tenant de teste sem o token
# passar pelo chat/agente — lê de um arquivo local criado via SSH
# interativo, mesmo padrão usado pro token da Cloudflare
# (ver security_cloudflare_token_leak na memoria).
#
# Uso (no servidor, depois de colar o token novo no arquivo):
#   umask 077 && cat > /root/.whatsapp_access_token
#   (cola o token, Ctrl-D)
#   bash update-whatsapp-token.sh
set -euo pipefail

NAMESPACE=atendagente
SECRET=teste-atendagente-hermes-env
TOKEN_FILE="${WHATSAPP_TOKEN_FILE:-/root/.whatsapp_access_token}"

if [ ! -s "$TOKEN_FILE" ]; then
  echo "arquivo de token nao encontrado ou vazio: $TOKEN_FILE" >&2
  exit 1
fi

NEW_VALUE="$(tr -d '[:space:]' < "$TOKEN_FILE" | base64 -w0)"

kubectl -n "$NAMESPACE" patch secret "$SECRET" \
  --type=json \
  -p="[{\"op\": \"replace\", \"path\": \"/data/WHATSAPP_CLOUD_ACCESS_TOKEN\", \"value\": \"$NEW_VALUE\"}]"

kubectl -n "$NAMESPACE" rollout restart "deploy/teste-atendagente-hermes"
kubectl -n "$NAMESPACE" rollout status "deploy/teste-atendagente-hermes" --timeout=120s

echo "token atualizado e pod reiniciado"
