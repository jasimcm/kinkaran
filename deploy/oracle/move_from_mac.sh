#!/bin/bash
# Move a running Kinkaran from this Mac to the Oracle VM, then print from here. Run ON THE MAC,
# in the Kinkaran folder (e.g. ~/kinkaran), after setup.sh has run on the VM:
#
#   deploy/oracle/move_from_mac.sh <vm-ip> <domain>       e.g.  141.148.1.2  myshop.duckdns.org
#   SSH_USER=ubuntu SSH_KEY=~/.ssh/oracle.key deploy/oracle/move_from_mac.sh ...
#
# Order matters (only ONE copy of the bot may run, or Telegram messages go missing):
#   1. stop the bot, tunnel and backup on this Mac          (nothing can bill meanwhile)
#   2. copy .env, bills.json and inventory.json to the VM    (PRINTER=remote, PUBLIC_URL=https://domain)
#   3. start the cloud server and wait until it answers over HTTPS
#   4. set up and start the print helper on this Mac
# If step 3 fails, scripts/install_launchd.sh starts everything on the Mac again.
set -euo pipefail
cd "$(dirname "$0")/../.."
IP="${1:?usage: $0 <vm-ip> <domain>}"
DOMAIN="${2:?usage: $0 <vm-ip> <domain>}"
SSH_USER="${SSH_USER:-ubuntu}"
SSH_OPTS=(-o ConnectTimeout=15)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
REMOTE="$SSH_USER@$IP"
say() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
env_value() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }

[ -f .env ] && [ -f backend/bills.json ] || { echo "Run this in the Kinkaran folder on the Mac (needs .env and backend/bills.json)." >&2; exit 1; }
[ -n "$(env_value ACCESS_KEY)" ] || { echo "Set ACCESS_KEY in .env first (openssl rand -hex 16): the print helper needs it." >&2; exit 1; }
ssh "${SSH_OPTS[@]}" "$REMOTE" "test -d /opt/kinkaran/app" || { echo "Run deploy/oracle/setup.sh on the VM first." >&2; exit 1; }

read -r -p "This stops Kinkaran on this Mac and starts it on $DOMAIN. Continue? [y/N] " answer
[ "$answer" = y ] || [ "$answer" = Y ] || exit 1

say "1/4 Stopping Kinkaran on this Mac"
scripts/install_launchd.sh --uninstall
./stop.sh > /dev/null 2>&1 || true

say "2/4 Copying settings and data to the VM"
TMP="$(mktemp -d)"
sed -e "s#^PRINTER=.*#PRINTER=remote#" -e "s#^PUBLIC_URL=.*#PUBLIC_URL=https://$DOMAIN#" .env > "$TMP/.env"
grep -q "^PRINTER=" "$TMP/.env" || echo "PRINTER=remote" >> "$TMP/.env"
grep -q "^PUBLIC_URL=" "$TMP/.env" || echo "PUBLIC_URL=https://$DOMAIN" >> "$TMP/.env"
cp backend/bills.json backend/inventory.json "$TMP/"
scp "${SSH_OPTS[@]}" -q "$TMP/.env" "$TMP/bills.json" "$TMP/inventory.json" "$REMOTE:/tmp/"
rm -rf "$TMP"
ssh "${SSH_OPTS[@]}" "$REMOTE" 'sudo install -o kinkaran -g kinkaran -m 600 /tmp/.env /opt/kinkaran/app/.env \
  && sudo install -o kinkaran -g kinkaran -m 644 /tmp/bills.json /tmp/inventory.json /opt/kinkaran/app/backend/ \
  && rm -f /tmp/.env /tmp/bills.json /tmp/inventory.json'

say "3/4 Starting Kinkaran on the VM"
ssh "${SSH_OPTS[@]}" "$REMOTE" "sudo systemctl restart kinkaran"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/counter.html" || true)
  [ "$code" = 200 ] && break
  sleep 2
done
if [ "$code" != 200 ]; then
  echo "❌ https://$DOMAIN doesn't answer (got $code). Check: ssh $REMOTE 'journalctl -u kinkaran -n 50'" >&2
  echo "   To run on the Mac again: scripts/install_launchd.sh" >&2
  exit 1
fi
echo "✅ https://$DOMAIN is up"

say "4/4 Print helper on this Mac"
cat > print_agent/agent.env <<EOF
KINKARAN_URL=https://$DOMAIN
ACCESS_KEY=$(env_value ACCESS_KEY)
PRINTER=$(env_value PRINTER)
PRINT_PAPER=$(env_value PRINT_PAPER || echo a4)
POLL_SECONDS=3
EOF
chmod 600 print_agent/agent.env
scripts/install_print_agent.sh

echo
echo "✅ Moved. The bot now runs in the cloud; bills print here."
echo "   Counter screen: open https://$DOMAIN/?key=<ACCESS_KEY> once in the browser, then https://$DOMAIN/counter.html"
