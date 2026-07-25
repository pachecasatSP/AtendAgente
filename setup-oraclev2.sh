#!/usr/bin/env bash
#
# setup-oracle-linux.sh — Bootstrap de VPS Oracle Cloud para hermes-agent
#                          em ORACLE LINUX 8/9 (x86_64 ou aarch64)
#
# v2 — correções para máquinas de pouca memória (E2.1.Micro, 1 GB):
#   • swap criado ANTES de qualquer dnf (causa do OOM na v1)
#   • instalações fatiadas, sem weak deps, com limpeza de cache entre elas
#   • sshd protegido do OOM killer; o agente é sacrificado primeiro
#
# Uso:
#   ssh -i hermes opc@<IP>
#   sed -i 's/\r$//' setup-oracle-linux.sh && chmod +x setup-oracle-linux.sh
#   ./setup-oracle-linux.sh
#
set -euo pipefail

# ─────────────────────────── Configuração ───────────────────────────
HERMES_USER="hermes"
TIMEZONE="America/Sao_Paulo"
INSTALL_PODMAN="false"
SSH_PORT="22"
# SWAP_SIZE_MB é calculado automaticamente conforme a RAM (veja etapa 1)

# ──────────────────────────── Helpers ───────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[erro]${NC} $*" >&2; exit 1; }

# dnf enxuto: sem dependências fracas, limpando o cache depois de cada lote
dnf_lean() {
    sudo dnf install -y -q --setopt=install_weak_deps=False "$@" \
        || { warn "Falha ao instalar: $*"; return 1; }
    sudo dnf clean packages -q >/dev/null 2>&1 || true
}

# ───────────────────────── 0. Verificações ──────────────────────────
log "Verificando ambiente..."

[[ $EUID -eq 0 ]] && die "Não rode como root. Use o usuário 'opc'."
sudo -n true 2>/dev/null || sudo true || die "Este usuário precisa de sudo."
command -v dnf >/dev/null || die "Este script é para Oracle Linux 8/9. Para Ubuntu, use setup-oracle.sh."

# shellcheck disable=SC1091
source /etc/os-release
OS_MAJOR="${VERSION_ID%%.*}"
[[ "$OS_MAJOR" =~ ^(8|9)$ ]] || warn "Versão $VERSION_ID não testada (esperado 8 ou 9)."
ok "Detectado: $PRETTY_NAME ($(uname -m))"

AUTH_KEYS="$HOME/.ssh/authorized_keys"
[[ -s "$AUTH_KEYS" ]] || die "Nenhuma chave SSH em $AUTH_KEYS. Não é seguro continuar."
ok "Chave SSH presente ($(grep -c . "$AUTH_KEYS") chave(s))."

# ══════════ 1. SWAP — PRIMEIRA COISA, antes de qualquer pacote ══════
# Numa E2.1.Micro (1 GB) o dnf é morto pelo OOM killer se não houver swap.
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
if   (( RAM_MB < 2048 )); then SWAP_MB=4096; LOW_MEM="true"
elif (( RAM_MB < 4096 )); then SWAP_MB=2048; LOW_MEM="false"
else                           SWAP_MB=2048; LOW_MEM="false"
fi
log "RAM detectada: ${RAM_MB} MB → swap alvo: ${SWAP_MB} MB"
[[ "$LOW_MEM" == "true" ]] && warn "Máquina de pouca memória: modo econômico ativado."

CUR_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
if (( CUR_SWAP_MB < SWAP_MB )); then
    log "Criando swapfile de ${SWAP_MB} MB..."
    sudo swapoff /swapfile 2>/dev/null || true
    sudo rm -f /swapfile
    sudo fallocate -l "${SWAP_MB}M" /swapfile 2>/dev/null \
        || sudo dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_MB" status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    sudo sysctl -qw vm.swappiness=60          # em RAM curta, usar swap cedo ajuda
    grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=60' | sudo tee -a /etc/sysctl.conf >/dev/null
    ok "Swap ativo: $(free -h | awk '/^Swap:/{print $2}')"
else
    ok "Swap já suficiente: $(free -h | awk '/^Swap:/{print $2}')"
fi

# ══════════════ 2. Repositórios e pacotes (fatiado) ═════════════════
log "Habilitando EPEL..."
sudo dnf install -y -q "oracle-epel-release-el${OS_MAJOR}" 2>/dev/null \
    || sudo dnf install -y -q epel-release 2>/dev/null \
    || warn "EPEL indisponível — fail2ban será pulado."
sudo dnf clean packages -q >/dev/null 2>&1 || true

if [[ "$LOW_MEM" == "true" ]]; then
    log "Aplicando apenas atualizações de SEGURANÇA (economia de memória)..."
    sudo dnf upgrade --security -y -q --setopt=install_weak_deps=False \
        || warn "Upgrade de segurança parcial — seguindo."
else
    log "Atualizando o sistema..."
    sudo dnf upgrade -y -q --setopt=install_weak_deps=False || warn "Upgrade parcial — seguindo."
fi
sudo dnf clean packages -q >/dev/null 2>&1 || true

log "Instalando pacotes em lotes pequenos..."
dnf_lean curl git tar xz jq            || true
dnf_lean gcc gcc-c++ make              || true
dnf_lean python3-devel libffi-devel openssl-devel || true
dnf_lean firewalld cronie              || true
dnf_lean policycoreutils-python-utils  || true
dnf_lean dnf-automatic                 || true

if dnf_lean fail2ban; then HAS_FAIL2BAN="true"; else HAS_FAIL2BAN="false"; fi

sudo timedatectl set-timezone "$TIMEZONE"
sudo systemctl enable --now crond >/dev/null 2>&1 || true
ok "Pacotes instalados — timezone: $TIMEZONE"

# ══════════════════ 3. Firewall (firewalld + iptables) ══════════════
log "Reconfigurando firewall..."
sudo iptables -P INPUT ACCEPT 2>/dev/null || true
sudo iptables -P FORWARD ACCEPT 2>/dev/null || true
sudo iptables -P OUTPUT ACCEPT 2>/dev/null || true
sudo iptables -F 2>/dev/null || true
sudo systemctl disable --now iptables 2>/dev/null || true

sudo systemctl enable --now firewalld >/dev/null
sudo firewall-cmd --permanent --add-port="${SSH_PORT}/tcp" >/dev/null
sudo firewall-cmd --reload >/dev/null
ok "firewalld ativo — entrada só na porta $SSH_PORT (saída liberada)."
warn "Libere a porta $SSH_PORT também na Security List da VCN."

# ══════════════════════ 4. fail2ban + SSH ═══════════════════════════
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
sudo sshd -t || die "Config de SSH inválida — nada foi aplicado."
sudo systemctl restart sshd
ok "Login por senha desabilitado, apenas chave SSH."

# ── 4b. Blindar o sshd contra o OOM killer (crítico em 1 GB) ────────
sudo mkdir -p /etc/systemd/system/sshd.service.d
sudo tee /etc/systemd/system/sshd.service.d/oom.conf >/dev/null <<'EOF'
[Service]
OOMScoreAdjust=-900
EOF
sudo systemctl daemon-reload
ok "sshd protegido do OOM killer — você não perde o acesso se faltar memória."

# ═══════════════ 5. Updates de segurança automáticos ════════════════
if [[ -f /etc/dnf/automatic.conf ]]; then
    sudo sed -i 's/^upgrade_type.*/upgrade_type = security/;s/^apply_updates.*/apply_updates = yes/' \
        /etc/dnf/automatic.conf
    sudo systemctl enable --now dnf-automatic.timer >/dev/null 2>&1 || true
    ok "Patches de segurança automáticos habilitados."
fi

# ═══════════════════════ 6. Podman (opcional) ═══════════════════════
if [[ "$INSTALL_PODMAN" == "true" ]]; then
    dnf_lean podman podman-docker && ok "Podman instalado."
fi

# ══════════════════ 7. Usuário de serviço do Hermes ═════════════════
if ! id -u "$HERMES_USER" >/dev/null 2>&1; then
    sudo useradd -m -s /bin/bash "$HERMES_USER"
    sudo passwd -l "$HERMES_USER" >/dev/null
    ok "Usuário '$HERMES_USER' criado (sem senha, sem sudo)."
else
    ok "Usuário '$HERMES_USER' já existe."
fi

# ════════════════════ 8. Instalação do Hermes ═══════════════════════
if sudo -u "$HERMES_USER" bash -lc 'command -v hermes' >/dev/null 2>&1; then
    ok "Hermes já instalado."
else
    log "Instalando hermes-agent (pode demorar bastante em máquina pequena)..."
    [[ "$LOW_MEM" == "true" ]] && warn "Se for morto por OOM, veja a seção MEMÓRIA no final."
    sudo -u "$HERMES_USER" bash -lc \
        'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash' \
        || die "Falha na instalação do hermes-agent. Veja a seção MEMÓRIA no final."
fi

HERMES_BIN="$(sudo -u "$HERMES_USER" bash -lc 'command -v hermes' 2>/dev/null || true)"
[[ -z "$HERMES_BIN" ]] && HERMES_BIN="/home/$HERMES_USER/.local/bin/hermes"
ok "Binário do Hermes: $HERMES_BIN"

# ═══════════════════ 9. SELinux (específico do OL) ══════════════════
SELINUX_MODE="$(getenforce 2>/dev/null || echo Disabled)"
log "SELinux está em modo: $SELINUX_MODE"
if [[ "$SELINUX_MODE" == "Enforcing" ]]; then
    sudo semanage fcontext -a -t bin_t "/home/$HERMES_USER/\.local/bin(/.*)?" 2>/dev/null || true
    sudo restorecon -R "/home/$HERMES_USER/.local/bin" 2>/dev/null || true
    ok "Contexto SELinux aplicado em ~/.local/bin."
fi

# ══════════════════════ 10. Serviço systemd ═════════════════════════
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
RestartSec=15

# Em caso de falta de memória, o agente morre ANTES do sshd (que está em -900).
# Restart=always o traz de volta; você nunca perde o acesso ao servidor.
OOMScoreAdjust=500
MemoryHigh=600M

StandardOutput=journal
StandardError=journal

# Isolamento básico
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/home/$HERMES_USER/.hermes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
ok "Serviço criado (ainda NÃO iniciado — falta o setup interativo)."

# ═════════════════ 11. Backup da memória do agente ══════════════════
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

# ════════════════════════════ Resumo ════════════════════════════════
cat <<EOF

$(echo -e "${GREEN}╭──────────────────────────────────────────────╮${NC}")
$(echo -e "${GREEN}│  Servidor pronto. Faltam 3 passos manuais.   │${NC}")
$(echo -e "${GREEN}╰──────────────────────────────────────────────╯${NC}")

RAM: ${RAM_MB} MB   |   Swap: $(free -h | awk '/^Swap:/{print $2}')

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

$(echo -e "${YELLOW}MEMÓRIA — se algo for 'Killed':${NC}")
     sudo dmesg | grep -i "out of memory" | tail -5
     free -h
   O culpado quase sempre é um processo grande demais para a RAM disponível.
   Aumente o swap (repita a etapa 1 com valor maior) e repita o comando.

$(echo -e "${YELLOW}SELINUX — se o serviço falhar com 'Permission denied':${NC}")
     sudo ausearch -m avc -ts recent | audit2allow -M hermes-local
     sudo semodule -i hermes-local.pp
   Evite 'setenforce 0' — resolve na hora, mas derruba a proteção toda.

$(echo -e "${YELLOW}Lembretes:${NC}")
  • Libere a porta $SSH_PORT na Security List da VCN.
  • Você entra como 'opc'; o agente roda como '$HERMES_USER'.
  • Rode 'free -h' com o agente ativo: livre abaixo de ~80 MB = instável.

EOF