#!/bin/bash
# Easy Bill: stop everything that start.sh started.
cd "$(dirname "$0")" || exit 1

stop_port() {  # stop whatever is listening on this port (our server or the WhatsApp sender)
  local pids
  pids=$(lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null)
  [ -n "$pids" ] && kill $pids && echo "Stopped $2"
}

stop_port 8000 "server + Telegram bot"
stop_port 3001 "WhatsApp sender"
pkill -f "cloudflared tunnel --no-autoupdate --url http://localhost:8000" && echo "Stopped tunnel"
exit 0
