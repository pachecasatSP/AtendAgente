#!/usr/bin/env bash
#
# setup-k3s.sh — Instala K3s + cert-manager numa VPS Hetzner (Ubuntu 24.04)
#                para hospedar o Hermes e, no futuro, os projetos do Azure.
#
# Rode via SSH NO SERVIDOR (não local), como root ou com sudo:
#   ssh root@<IP_DA_CX32>
#   bash setup-k3s.sh
#
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log(){ echo -e "${BLUE}==>${NC} $*"; }
ok(){ echo -e "${GREEN}[ok]${NC} $*"; }
die(){ echo -e "${RED}[erro]${NC} $*" >&2; exit 1; }

# --- Descobrir o IP público (para o --tls-san) ---
PUBLIC_IP="$(curl -s https://api.ipify.org || true)"
[[ -z "$PUBLIC_IP" ]] && die "Não consegui descobrir o IP público. Informe manualmente editando o script."
log "IP público detectado: $PUBLIC_IP"

# --- 1. Instalar K3s ---
# --tls-san: permite acessar o cluster de fora (do seu notebook) pelo IP.
# --write-kubeconfig-mode 644: deixa o kubeconfig legível sem sudo.
# O Traefik já vem embutido e sobe sozinho.
if ! command -v k3s >/dev/null 2>&1; then
  log "Instalando K3s..."
  curl -sfL https://get.k3s.io | \
    INSTALL_K3S_EXEC="--tls-san ${PUBLIC_IP} --write-kubeconfig-mode 644" sh -
  ok "K3s instalado."
else
  ok "K3s já instalado."
fi

# Espera o nó ficar Ready
log "Aguardando o nó ficar Ready..."
for i in $(seq 1 30); do
  if k3s kubectl get nodes 2>/dev/null | grep -q " Ready "; then ok "Nó Ready."; break; fi
  sleep 4
done

# Atalho: usar kubectl sem o prefixo k3s
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
ln -sf /usr/local/bin/k3s /usr/local/bin/kubectl 2>/dev/null || true

# --- 2. Instalar Helm ---
if ! command -v helm >/dev/null 2>&1; then
  log "Instalando Helm..."
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  ok "Helm instalado."
else
  ok "Helm já instalado."
fi

# --- 3. Instalar cert-manager ---
if ! kubectl get ns cert-manager >/dev/null 2>&1; then
  log "Instalando cert-manager (via Helm)..."
  helm repo add jetstack https://charts.jetstack.io >/dev/null
  helm repo update >/dev/null
  helm install cert-manager jetstack/cert-manager \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true --wait
  ok "cert-manager instalado."
else
  ok "cert-manager já instalado."
fi

kubectl get pods -n cert-manager

cat <<EOF

$(echo -e "${GREEN}Cluster pronto.${NC}")

IP público desta máquina: ${PUBLIC_IP}
--> Crie no Registro.br um registro A:  bot.colocar-me.com.br  ->  ${PUBLIC_IP}
    (e aponte também colocar-me.com.br se for hospedar a landing aqui depois)

Próximos passos (do seu notebook ou aqui no servidor):
  1. Ajuste <SEU_EMAIL> nos arquivos cluster/*.yaml
  2. kubectl apply -f cluster/
  3. Crie o Secret hermes-env (veja hermes/02-secret.EXAMPLE.yaml)
  4. kubectl apply -f hermes/
  5. Acompanhe: kubectl -n hermes get pods,ingress,certificate -w

Para administrar do seu notebook: copie /etc/rancher/k3s/k3s.yaml,
troque 127.0.0.1 por ${PUBLIC_IP} no arquivo, e use como KUBECONFIG.
EOF
