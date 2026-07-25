#!/usr/bin/env bash
#
# setup-oracle-linux.sh — Bootstrap de VPS Oracle Cloud (ARM / Ampere A1)
#                          para hermes-agent em ORACLE LINUX 8/9
#
# Uso:
#   1. Instância VM.Standard.A1.Flex com Oracle Linux (aarch64) e sua chave SSH
#   2. ssh -i hermes opc@<IP>          # usuário 'opc', não 'ubuntu'
#   3. chmod +x setup-oracle-linux.sh && ./setup-oracle-linux.sh
#
set -euo pipefail

# ─────────────────────────── Configuração ───────────────────────────
HERMES_USER="hermes"
TIMEZONE="America/Sao_Paulo"
SWAP_SIZE="2G"
INSTALL_PODMAN="false"
SSH_PORT="22"

# ──────────────────────────── Helpers ───────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[erro]${NC} $*" >&2; exit 1; }

# ───────────────────────── 0. Verificações ──────────────────────────
log "Verificando ambiente..."
[[ $EUID -eq 0 ]] && die "Não rode como root. Use o usuário 'opc'."
sudo -n true 2>/dev/null || sudo true || die "Este usuário precisa de sudo."

command -v dnf >/dev/null || die "Este script é para Oracle Linux 8/9. Se a imagem for Ubuntu, use setup-oracle.sh."

source /etc/os-release
OS_MAJOR="${VERSION_ID%%.*}"
[[ "$OS_MAJOR" =~ ^(8|9)$ ]] || warn "Versão $VERSION_ID não testada."
ok "Detectado: $PRETTY_NAME"

[[ "$(uname -m)" == "aarch64" ]] || warn "Arquitetura $(uname -m) (esperado aarch64)."

AUTH_KEYS="$HOME/.ssh/authorized_keys"
[[ -s "$AUTH_KEYS" ]] || die "Nenhuma chave SSH em $AUTH_KEYS. Não é seguro continuar."
ok "Chave SSH presente ($(grep -c . "$AUTH_KEYS") chave(s))."

# ──────────────────── 1. Repositórios e pacotes base ────────────────
log "Habilitando EPEL e atualizando o sistema..."
sudo dnf install -y -q "oracle-epel-release-el${OS_MAJOR}" 2>/dev/null \
    || sudo dnf install -y -q epel-release 2>/dev/null \
    || warn "EPEL indisponível — fail2ban será pulado."

sudo dnf makecache -q
sudo dnf upgrade -y -q
sudo dnf install -y -q curl git tar xz jq gcc gcc-c++ make \
    python3-devel libffi-devel openssl-devel \
    firewalld cronie policycoreutils-python-utils dnf-automatic

if sudo dnf install -y -q fail2ban 2>/dev/null; then
    HAS_FAIL2BAN="true"
else
    HAS_FAIL2BAN="false"; warn "fail2ban indisponível — seguindo sem ele."
fi

sudo timedatectl set-timezone "$TIMEZONE"
sudo systemctl enable --now crond >/dev/null 2>&1 || true
ok "Pacotes instalados — timezone: $TIMEZONE"

# ─────────────────────────── 2. Swap ────────────────────────────────
if ! sudo swapon --show | grep -q '/swapfile'; then
    log "Criando swap de $SWAP_SIZE..."
    sudo fallocate -l "$SWAP_SIZE" /swapfile || \
        sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
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

# ──────────────────── 3. Firewall (firewalld + iptables) ────────────
log "Reconfigurando firewall..."
sudo iptables -P INPUT ACCEPT 2>/dev/null || true
sudo iptables -P FORWARD ACCEPT 2>/dev/null || true
sudo iptables -P OUTPUT ACCEPT 2>/dev/null || true
sudo iptables -F 2>/dev/null || true
sudo systemctl disable --now iptables 2>/dev/null || true

sudo systemctl enable --now firewalld >/dev/null
sudo firewall-cmd --permanent --add-port="${SSH_PORT}/tcp" >/dev/null
sudo firewall-cmd --reload >/dev/null
ok "firewalld ativo — entrada só na porta $SSH_PORT."
warn "Libere a porta $SSH_PORT também na Security List da VCN."

# ─────────────────────── 4. fail2ban + SSH ──────────────────────────
if [[ "$HAS_FAIL2BAN" == "true" ]]; then
    sudo tee /etc/fail2ban/jail.local >/dev/null <<EOF
[sshd]
enabled  = true
port     = $SSH_PORT
backend  = systemd
maxretry = 4
bantime  = 1h
findtime = 10m
EOF
    sudo systemctl enable --now fail2ban >/dev/null 2>&1 || warn "fail2ban não subiu."
fi

log "Endurecendo o acesso SSH..."
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
EOF
sudo sshd -t || die "Config de SSH inválida — revise antes de continuar."
sudo systemctl restart sshd
ok "Login por senha desabilitado, apenas chave SSH."

# ─────────────────── 5. Updates de segurança automáticos ────────────
sudo sed -i 's/^upgrade_type.*/upgrade_type = security/;s/^apply_updates.*/apply_updates = yes/' \
    /etc/dnf/automatic.conf
sudo systemctl enable --now dnf-automatic.timer >/dev/null
ok "Patches de segurança automáticos habilitados."

# ─────────────────────── 6. Podman (opcional) ───────────────────────
if [[ "$INSTALL_PODMAN" == "true" ]]; then
    sudo dnf install -y -q podman podman-docker
    ok "Podman instalado (alias 'docker' disponível)."
fi

# ────────────────── 7. Usuário de serviço do Hermes ─────────────────
if ! id -u "$HERMES_USER" >/dev/null 2>&1; then
    sudo useradd -m -s /bin/bash "$HERMES_USER"
    sudo passwd -l "$HERMES_USER" >/dev/null
    ok "Usuário '$HERMES_USER' criado (sem senha, sem sudo)."
else
    ok "Usuário '$HERMES_USER' já existe."
fi

# ────────────────────── 8. Instalação do Hermes ─────────────────────
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

# ───────────────────── 9. SELinux (específico do OL) ────────────────
SELINUX_MODE="$(getenforce 2>/dev/null || echo Disabled)"
log "SELinux está em modo: $SELINUX_MODE"
if [[ "$SELINUX_MODE" == "Enforcing" ]]; then
    sudo semanage fcontext -a -t bin_t "/home/$HERMES_USER/\.local/bin(/.*)?" 2>/dev/null || true
    sudo restorecon -R "/home/$HERMES_USER/.local/bin" 2>/dev/null || true
    ok "Contexto SELinux aplicado em ~/.local/bin."
    warn "Se o serviço não subir, veja a seção SELINUX no final."
fi

# ──────────────────────── 10. Serviço systemd ───────────────────────
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
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/home/$HERMES_USER/.hermes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
ok "Serviço criado (ainda NÃO iniciado)."

# ──────────────────── 11. Backup da memória do agente ───────────────
sudo -u "$HERMES_USER" tee "/home/$HERMES_USER/backup-hermes.sh" >/dev/null <<'EOF'
#!/usr/bin/env bash
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
ok "Backup diário às 03:00 agendado."

# ──────────────────────────── Resumo ────────────────────────────────
cat <<EOF

╭──────────────────────────────────────────────╮
│  Servidor pronto. Faltam 3 passos manuais.   │
╰──────────────────────────────────────────────╯

1) SETUP INTERATIVO (provider de LLM + QR code do WhatsApp):
     sudo -u $HERMES_USER -i
     hermes setup

2) SUBIR O SERVIÇO:
     sudo systemctl enable --now hermes
     sudo systemctl status hermes
     journalctl -u hermes -f

3) BACKUP (opcional) — repo privado + deploy key com escrita:
     sudo -u $HERMES_USER -i
     cd ~/.hermes && git init && git remote add origin git@github.com:USER/REPO.git

SELINUX — se o serviço falhar com 'Permission denied':
     sudo ausearch -m avc -ts recent
     sudo ausearch -m avc -ts recent | audit2allow -M hermes-local
     sudo semodule -i hermes-local.pp
   Evite 'setenforce 0' — resolve na hora, mas derruba a proteção toda.

Lembretes:
  • Libere a porta $SSH_PORT na Security List da VCN.
  • Você entra como 'opc'; o agente roda como '$HERMES_USER'.
  • Não cole comandos no console web — use sempre SSH.

EOF