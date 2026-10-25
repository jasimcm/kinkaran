#!/bin/bash
# Cloudflare quick tunnel for Easy Bill, run under launchd (com.easybill.tunnel).
# The quick-tunnel URL changes every run, so this waits for the new URL, writes it to
# PUBLIC_URL in .env, then restarts the server (launchd KeepAlive brings it back with
# the fresh address, so WhatsApp PDF links keep working).
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

cloudflared tunnel --no-autoupdate --url http://localhost:8000 > logs/tunnel.log 2>&1 &
CLOUDFLARED=$!

(
  URL=""
  for _ in $(seq 1 90); do
    URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" logs/tunnel.log | tail -1)
    [ -n "$URL" ] && break
    sleep 0.5
  done
  if [ -n "$URL" ] && ! grep -q "^PUBLIC_URL=$URL\$" .env 2>/dev/null; then
    sed -i '' "s#^PUBLIC_URL=.*#PUBLIC_URL=$URL#" .env
    sleep 2
    lsof -ti tcp:8000 | xargs kill 2>/dev/null
  fi
) &

wait $CLOUDFLARED
