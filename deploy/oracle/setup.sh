#!/bin/bash
# Kinkaran on an Oracle Cloud Always Free VM (Ubuntu 24.04, ARM or x86). Run ON the VM:
#
#   curl -fsSL https://raw.githubusercontent.com/jasimcm/kinkaran/main/deploy/oracle/setup.sh -o setup.sh
#   sudo DOMAIN=myshop.duckdns.org bash setup.sh
#
# Safe to run again: it updates the code and packages and keeps .env, bills and backups.
# What it does:
#   - sets the clock to India time (the day report closes at midnight IST)
#   - installs Python, git and Caddy (automatic HTTPS from Let's Encrypt)
#   - creates the user "kinkaran" and puts the app in /opt/kinkaran/app
#   - opens ports 80/443 in the VM's own firewall (Oracle images block them)
#   - installs systemd services: kinkaran (restart on crash) and a nightly backup at 23:30
#   - starts the app only when /opt/kinkaran/app/.env exists (copy yours from the Mac first)
#
# You still open 80/443 in the Oracle console (VCN security list), see deploy/oracle/README.md.
set -euo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN, e.g.: sudo DOMAIN=myshop.duckdns.org bash setup.sh}"
REPO="${REPO:-https://github.com/jasimcm/kinkaran.git}"
BRANCH="${BRANCH:-main}"
HOME_DIR=/opt/kinkaran
APP="$HOME_DIR/app"

say() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] && [ "${VERSION_ID%%.*}" -ge 24 ] \
  || { echo "Use an Ubuntu 24.04 image (this is ${PRETTY_NAME:-unknown}): Caddy comes from its package list." >&2; exit 1; }

say "Time zone: Asia/Kolkata"
timedatectl set-timezone Asia/Kolkata

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git curl caddy

say "User and code"
id kinkaran >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /usr/sbin/nologin kinkaran
mkdir -p "$HOME_DIR/backups"
chown kinkaran:kinkaran "$HOME_DIR" "$HOME_DIR/backups"
if [ -d "$APP/.git" ]; then
  sudo -u kinkaran git -C "$APP" pull --ff-only
else
  sudo -u kinkaran git clone --branch "$BRANCH" "$REPO" "$APP"
fi
sudo -u kinkaran python3 -m venv "$APP/.venv"
sudo -u kinkaran "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u kinkaran "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

say "Firewall on the VM: allow 80 and 443"
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
done
if command -v netfilter-persistent >/dev/null; then
  netfilter-persistent save
else
  echo "   (netfilter-persistent not found: the rule lasts until reboot; install iptables-persistent)"
fi

say "HTTPS with Caddy for $DOMAIN"
cat > /etc/caddy/Caddyfile <<EOF
# Kinkaran (written by deploy/oracle/setup.sh). Caddy gets and renews the HTTPS certificate itself.
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable caddy
systemctl reload-or-restart caddy

say "systemd services"
install -m 644 "$APP/deploy/oracle/kinkaran.service" /etc/systemd/system/kinkaran.service
install -m 644 "$APP/deploy/oracle/kinkaran-backup.service" /etc/systemd/system/kinkaran-backup.service
install -m 644 "$APP/deploy/oracle/kinkaran-backup.timer" /etc/systemd/system/kinkaran-backup.timer
systemctl daemon-reload
systemctl enable --now kinkaran-backup.timer
systemctl enable kinkaran.service

if [ -f "$APP/.env" ]; then
  chown kinkaran:kinkaran "$APP/.env"
  chmod 600 "$APP/.env"
  grep -q "^PRINTER=remote$" "$APP/.env" || echo "   ⚠️  .env: set PRINTER=remote (the shop Mac's print helper prints)"
  grep -q "^PUBLIC_URL=https://$DOMAIN$" "$APP/.env" || echo "   ⚠️  .env: set PUBLIC_URL=https://$DOMAIN"
  systemctl restart kinkaran.service
  sleep 3
  systemctl --no-pager --lines=5 status kinkaran.service || true
  say "Done. Check: curl -s https://$DOMAIN/counter.html -o /dev/null -w '%{http_code}\n'"
else
  say "Almost done: no .env yet, so the app is not started"
  echo "   Copy your .env from the Mac (see deploy/oracle/README.md, step 5), then:"
  echo "   sudo systemctl start kinkaran && journalctl -u kinkaran -f"
fi
