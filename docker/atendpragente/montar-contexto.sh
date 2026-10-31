#!/usr/bin/env bash
# Monta o contexto de build das imagens do AtendPraGente a partir DESTE commit: tools/ + Dockerfiles + requirements + locks, todos na raiz do contexto
# (os Dockerfiles copiam `tools/`, `locks/` e `requirements-tools.txt` da raiz). Usado pelo GitHub Actions (.github/workflows/imagens.yml) e pelo
# construir-imagens.sh do infra-aws, para os dois produzirem a mesma imagem.   bash docker/atendpragente/montar-contexto.sh <pasta-vazia>
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${1:?uso: montar-contexto.sh <pasta de destino>}"
mkdir -p "$DEST/locks"
cp -r "$RAIZ/tools" "$DEST/tools"
cp "$RAIZ"/docker/atendpragente/Dockerfile.* "$RAIZ"/docker/atendpragente/requirements-tools.txt "$DEST/"
cp "$RAIZ"/docker/atendpragente/locks/*.txt "$DEST/locks/"
find "$DEST/tools" -name __pycache__ -type d -prune -exec rm -r {} + 2>/dev/null || true
