#!/usr/bin/env bash
#
# setup-oracle.sh — Bootstrap de VPS Oracle Cloud (ARM / Ampere A1) para hermes-agent
#
# Uso:
#   1. Crie a instância VM.Standard.A1.Flex com Ubuntu 22.04 (aarch64) e sua chave SSH
#   2. ssh -i ~/.ssh/hermes ubuntu@<IP>
#   3. curl -O <url-do-script> && chmod +x setup-oracle.sh && ./setup-oracle.sh
#
# O script NÃO roda o `hermes setup` (é interativo) — ele prepara tudo e te diz
# exatamente o que fazer no final.
#
set -euo pipefail

# ─────────────────────────── Configuração ───────────────────────────
HERMES_USER="hermes"          # usuário de serviço que roda o agente
TIMEZONE="America/Sao_Paulo"
SWAP_SIZE="2G"                # swap de segurança
INSTALL_DOCKER="false"        # true se for usar o terminal backend em container
SSH_PORT="22"

# ──────────────────────────── Helpers ───────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[erro]${NC} $*" >&2; exit 1; }

# ───────────────────────── 0. Verificações ──────────────────────────
log "Verificando ambiente..."

[[ $EUID -eq 0 ]] && die "Não rode como root. Use o usuário 'ubuntu' (o sudo é chamado quando necessário)."
sudo -n true 2>/dev/null || sudo true || die "Este usuário precisa de sudo."

ARCH="$(uname -m)"
[[ "$ARCH" == "aarch64" ]] || warn "Arquitetura $ARCH (esperado aarch64). O script segue, mas foi feito para ARM."

command -v apt-get >/dev/null || die "Este script assume Ubuntu/Debian."

# TRAVA DE SEGURANÇA: sem chave SSH instalada, desabilitar senha te tranca do lado de fora.
AUTH_KEYS="$HOME/.ssh/authorized_keys"
if [[ ! -s "$AUTH_KEYS" ]]; then
    die "Nenhuma chave SSH encontrada em $AUTH_KEYS.
     Recrie a instância informando sua chave pública antes de continuar."
fi
ok "Chave SSH presente ($(wc -l < "$AUTH_KEYS") chave(s))."

# ──────────────────── 1. Sistema base e timezone ────────────────────
log "Atualizando o sistema (pode demorar alguns minutos)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get upgrade -y -qq
sudo apt-get install -y -qq \
    curl git ca-certificates gnupg ufw fail2ban \
    unattended-upgrades netfilter-persistent \
    build-essential python3-venv ripgrep ffmpeg jq
sudo timedatectl set-timezone "$TIMEZONE"
ok "Sistema atualizado — timezone: $TIMEZONE"

# ─────────────────────────── 2. Swap ────────────────────────────────
if ! sudo swapon --show | grep -q '/swapfile'; then
    log "Criando swap de $SWAP_SIZE..."
    sudo fallocate -l "$SWAP_SIZE" /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    sudo sysctl -qw vm.swappiness=10
    grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null
    ok "Swap ativo."
else
    ok "Swap já configurado."
fi

# ─────────────────── 3. Firewall (a pegadinha da Oracle) ────────────
# As imagens Ubuntu da Oracle vêm com iptables bloqueando quase tudo, o que
# atrapalha até o tráfego de saída em alguns casos. Limpamos e passamos o
# controle para o ufw, que é mais legível e persistente.
log "Reconfigurando firewall..."

sudo iptables -P INPUT ACCEPT
sudo iptables -P FORWARD ACCEPT
sudo iptables -P OUTPUT ACCEPT
sudo iptables -F
sudo iptables -X
sudo netfilter-persistent save >/dev/null 2>&1 || true

sudo ufw --force reset >/dev/null
sudo ufw default deny incoming
sudo ufw default allow outgoing          # Baileys/WhatsApp é conexão de SAÍDA
sudo ufw allow "$SSH_PORT"/tcp comment 'SSH'
sudo ufw --force enable
ok "Firewall: só a porta $SSH_PORT aberta na entrada; saída liberada."
warn "Lembre-se de liberar a porta $SSH_PORT também na Security List da VCN (console Oracle)."

# ─────────────────────── 4. fail2ban + SSH ──────────────────────────
log "Endurecendo o acesso SSH..."
sudo tee /etc/fail2ban/jail.local >/dev/null <<EOF
[sshd]
enabled  = true
port     = $SSH_PORT
maxretry = 4
bantime  = 1h
findtime = 10m
EOF
sudo systemctl enable --now fail2ban >/dev/null 2>&1

sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
EOF
sudo sshd -t || die "Config de SSH inválida — nada foi aplicado. Revise antes de continuar."
sudo systemctl restart ssh
ok "Login por senha desabilitado, apenas chave SSH. fail2ban ativo."

# ─────────────────── 5. Updates de segurança automáticos ────────────
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
ok "Patches de segurança automáticos habilitados."

# ─────────────────────── 6. Docker (opcional) ───────────────────────
if [[ "$INSTALL_DOCKER" == "true" ]]; then
    log "Instalando Docker..."
    if ! command -v docker >/dev/null; then
        curl -fsSL https://get.docker.com | sudo sh
    fi
    sudo usermod -aG docker "$HERMES_USER" 2>/dev/null || true
    ok "Docker instalado."
fi

# ────────────────── 7. Usuário de serviço do Hermes ─────────────────
if ! id -u "$HERMES_USER" >/dev/null 2>&1; then
    log "Criando usuário de serviço '$HERMES_USER'..."
    sudo adduser --disabled-password --gecos "" "$HERMES_USER" >/dev/null
    ok "Usuário '$HERMES_USER' criado (sem senha, sem sudo — roda só o agente)."
else
    ok "Usuário '$HERMES_USER' já existe."
fi

# ────────────────────── 8. Instalação do Hermes ─────────────────────
# Em ARM a imagem Docker oficial pode não ter build arm64 — usamos o
# instalador nativo, que resolve uv, Python 3.11 e Node.js sozinho.
HERMES_BIN=""
if sudo -u "$HERMES_USER" bash -lc 'command -v hermes' >/dev/null 2>&1; then
    ok "Hermes já instalado."
else
    log "Instalando hermes-agent (instalação nativa ARM)..."
    sudo -u "$HERMES_USER" bash -lc \
        'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash' \
        || die "Falha na instalação do hermes-agent."
fi

HERMES_BIN="$(sudo -u "$HERMES_USER" bash -lc 'command -v hermes' 2>/dev/null || true)"
[[ -z "$HERMES_BIN" ]] && HERMES_BIN="/home/$HERMES_USER/.local/bin/hermes"
ok "Binário do Hermes: $HERMES_BIN"

# ───────────────────────── 9. Serviço systemd ───────────────────────
log "Criando o serviço systemd..."
sudo tee /etc/systemd/system/hermes.service >/dev/null <<EOF
[Unit]
Description=Hermes Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$HERMES_USER
WorkingDirectory=/home/$HERMES_USER
Environment="PATH=/home/$HERMES_USER/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$HERMES_BIN gateway run
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Isolamento básico
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/$HERMES_USER/.hermes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
ok "Serviço criado (ainda NÃO iniciado — falta o setup interativo)."

# ──────────────────── 10. Backup da memória do agente ───────────────
log "Instalando rotina de backup..."
sudo -u "$HERMES_USER" tee "/home/$HERMES_USER/backup-hermes.sh" >/dev/null <<'EOF'
#!/usr/bin/env bash
# Backup da memória/config do agente para um repositório git privado.
set -euo pipefail
cd "$HOME/.hermes" || exit 0
[[ -d .git ]] || exit 0
git add -A
git diff --cached --quiet || git commit -q -m "backup $(date +%F_%H:%M)"
git push -q origin HEAD 2>/dev/null || echo "push falhou — verifique o deploy key"
EOF
sudo chmod +x "/home/$HERMES_USER/backup-hermes.sh"

sudo -u "$HERMES_USER" bash -c \
    '(crontab -l 2>/dev/null | grep -v backup-hermes; echo "0 3 * * * $HOME/backup-hermes.sh >> $HOME/backup.log 2>&1") | crontab -'
ok "Backup diário às 03:00 agendado (falta apontar o repositório git)."

# ──────────────────────────── Resumo ────────────────────────────────
cat <<EOF

$(echo -e "${GREEN}╭──────────────────────────────────────────────╮${NC}")
$(echo -e "${GREEN}│  Servidor pronto. Faltam 3 passos manuais.   │${NC}")
$(echo -e "${GREEN}╰──────────────────────────────────────────────╯${NC}")

1) SETUP INTERATIVO (escolher o provider de LLM e conectar o WhatsApp):

     sudo -u $HERMES_USER -i
     hermes setup

   Deixe a sessão aberta para ler o QR code do WhatsApp com o celular.

2) SUBIR O SERVIÇO (depois que o setup terminar):

     sudo systemctl enable --now hermes
     sudo systemctl status hermes
     journalctl -u hermes -f          # acompanhar os logs

3) BACKUP (opcional, mas recomendado) — crie um repo privado no GitHub,
   adicione um deploy key com permissão de escrita e rode:

     sudo -u $HERMES_USER -i
     cd ~/.hermes && git init && git remote add origin git@github.com:USER/REPO.git

$(echo -e "${YELLOW}Lembretes:${NC}")
  • Libere a porta $SSH_PORT na Security List da VCN, no console da Oracle.
  • Não cole comandos no console web do provedor — ele corrompe ':' '@' '=';
    use sempre SSH.
  • O login por senha foi desabilitado. Guarde bem a sua chave privada.

EOF
