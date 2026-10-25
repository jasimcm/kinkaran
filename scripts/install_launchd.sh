#!/bin/bash
# Kinkaran: start everything automatically on this Mac, and restart it if it crashes.
#
#   scripts/install_launchd.sh              install and start the launchd agents
#   scripts/install_launchd.sh --uninstall  stop and remove them
#   scripts/install_launchd.sh --print DIR  only write the .plist files into DIR (nothing is installed)
#
# Agents (in ~/Library/LaunchAgents, they start when the shop account logs in):
#   com.kinkaran.server    backend/main.py: API, counter screen, Telegram bot   (restarts on crash)
#   com.kinkaran.whatsapp  whatsapp-sender/index.mjs                            (restarts on crash)
#   com.kinkaran.tunnel    scripts/run_tunnel.sh: tunnel + PUBLIC_URL in .env   (restarts on crash)
#   com.kinkaran.backup    scripts/backup.sh every night at 23:30
#
# Turn on automatic login for the shop account so a power cut doesn't leave everything stopped.
# Don't also run ./start.sh: this replaces it. Logs: logs/*.log
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS="com.kinkaran.server com.kinkaran.whatsapp com.kinkaran.tunnel com.kinkaran.backup"
PATH_FOR_AGENTS="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

xml() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; printf '%s' "${s//>/&gt;}"; }

plist() {  # plist LABEL PROGRAM... ; reads KEEP_ALIVE, SCHEDULE and WORKDIR from the caller
  local label="$1"; shift
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo "  <key>Label</key><string>$label</string>"
  echo "  <key>WorkingDirectory</key><string>$(xml "${WORKDIR:-$ROOT}")</string>"
  echo '  <key>ProgramArguments</key><array>'
  for arg in "$@"; do echo "    <string>$(xml "$arg")</string>"; done
  echo '  </array>'
  echo "  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH_FOR_AGENTS</string></dict>"
  echo "  <key>StandardOutPath</key><string>$(xml "$ROOT/logs/${label#com.kinkaran.}.log")</string>"
  echo "  <key>StandardErrorPath</key><string>$(xml "$ROOT/logs/${label#com.kinkaran.}.log")</string>"
  if [ "$KEEP_ALIVE" = yes ]; then
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>ThrottleInterval</key><integer>10</integer>'
  fi
  if [ -n "$SCHEDULE" ]; then
    echo "  <key>StartCalendarInterval</key><dict>$SCHEDULE</dict>"
  fi
  echo '</dict></plist>'
}

write_plists() {  # write_plists DIR
  local dir="$1"
  mkdir -p "$dir" "$ROOT/logs"
  KEEP_ALIVE=yes SCHEDULE="" plist com.kinkaran.server "$ROOT/.venv/bin/python" "$ROOT/backend/main.py" > "$dir/com.kinkaran.server.plist"
  # the sender keeps its WhatsApp login in auth/ next to it, so it must run in its own folder
  WORKDIR="$ROOT/whatsapp-sender" KEEP_ALIVE=yes SCHEDULE="" plist com.kinkaran.whatsapp "$(command -v node || echo /opt/homebrew/bin/node)" "$ROOT/whatsapp-sender/index.mjs" > "$dir/com.kinkaran.whatsapp.plist"
  KEEP_ALIVE=yes SCHEDULE="" plist com.kinkaran.tunnel /bin/bash "$ROOT/scripts/run_tunnel.sh" > "$dir/com.kinkaran.tunnel.plist"
  KEEP_ALIVE=no SCHEDULE="<key>Hour</key><integer>23</integer><key>Minute</key><integer>30</integer>" \
    plist com.kinkaran.backup /bin/bash "$ROOT/scripts/backup.sh" > "$dir/com.kinkaran.backup.plist"
}

case "${1:-install}" in
  --print)
    write_plists "${2:?usage: --print DIR}"
    echo "Wrote plists to $2"
    ;;
  --uninstall)
    for label in $LABELS; do
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
    done
    echo "Removed the Kinkaran launchd agents."
    ;;
  install)
    [ -x .venv/bin/python ] || { echo "❌ Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }
    [ -f .env ] || { echo "❌ Copy .env.example to .env and fill it in first." >&2; exit 1; }
    chmod 600 .env
    ./stop.sh > /dev/null 2>&1 || true  # launchd takes over from start.sh
    write_plists "$AGENTS"
    for label in $LABELS; do
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
    done
    echo "✅ Installed. Check with: launchctl list | grep kinkaran"
    echo "   Logs are in $ROOT/logs/. Remove with: scripts/install_launchd.sh --uninstall"
    ;;
  *)
    echo "usage: $0 [--print DIR | --uninstall]" >&2
    exit 2
    ;;
esac
