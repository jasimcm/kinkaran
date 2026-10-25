#!/bin/bash
# Kinkaran: run the print helper on the shop Mac (when the server runs in the cloud).
#
#   scripts/install_print_agent.sh              check settings, then install and start it
#   scripts/install_print_agent.sh --uninstall  stop and remove it
#   scripts/install_print_agent.sh --print DIR  only write the .plist into DIR
#
# Fill in print_agent/agent.env first (copy print_agent/agent.env.example).
# The helper starts at login and restarts if it crashes. Log: logs/printer.log
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="com.kinkaran.printer"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="${PYTHON:-/usr/bin/python3}"  # the Mac's own python3: the helper needs nothing else

xml() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; printf '%s' "${s//>/&gt;}"; }

write_plist() {  # write_plist FILE
  mkdir -p "$(dirname "$1")" "$ROOT/logs"
  cat > "$1" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$(xml "$ROOT")</string>
  <key>ProgramArguments</key><array>
    <string>$(xml "$PYTHON")</string>
    <string>$(xml "$ROOT/print_agent/print_agent.py")</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$(xml "$ROOT/logs/printer.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$ROOT/logs/printer.log")</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
</dict></plist>
EOF
}

case "${1:-install}" in
  --print)
    write_plist "${2:?usage: --print DIR}/$LABEL.plist"
    echo "Wrote $2/$LABEL.plist"
    ;;
  --uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed the print helper."
    ;;
  install)
    [ -f print_agent/agent.env ] || { echo "❌ Copy print_agent/agent.env.example to print_agent/agent.env and fill it in." >&2; exit 1; }
    chmod 600 print_agent/agent.env  # it holds the access key
    "$PYTHON" print_agent/print_agent.py --check || { echo "❌ Fix the problems above, then run this again." >&2; exit 1; }
    write_plist "$PLIST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "✅ Print helper running. Log: $ROOT/logs/printer.log"
    ;;
  *)
    echo "usage: $0 [--print DIR | --uninstall]" >&2
    exit 2
    ;;
esac
