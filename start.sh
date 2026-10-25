#!/bin/bash
# Easy Bill: start everything for the demo on this Mac with one command.
#   ./start.sh   WhatsApp sender + Cloudflare tunnel + server and Telegram bot (PUBLIC_URL is updated in .env)
#   ./stop.sh    stop them all
# Everything keeps running after you close the Terminal. Logs are in logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
./stop.sh > /dev/null

echo "1/3 WhatsApp sender"
(cd whatsapp-sender && nohup node index.mjs > ../logs/whatsapp-sender.log 2>&1 &)

echo "2/3 Cloudflare tunnel"
nohup cloudflared tunnel --no-autoupdate --url http://localhost:8000 > logs/tunnel.log 2>&1 &
URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" logs/tunnel.log | head -1)
  [ -n "$URL" ] && break
  sleep 0.5
done
if [ -n "$URL" ]; then
  sed -i '' "s#^PUBLIC_URL=.*#PUBLIC_URL=$URL#" .env  # the server reads it when it starts below
else
  echo "   ⚠️  No tunnel address (no internet?). WhatsApp messages will have no PDF link."
fi

echo "3/3 Easy Bill server + Telegram bot"
nohup .venv/bin/python backend/main.py > logs/server.log 2>&1 &
for _ in $(seq 1 40); do
  curl -sf http://localhost:8000/api/config > /dev/null && break
  sleep 0.5
done
sleep 2  # give the bot and the WhatsApp sender a moment to connect

echo
if grep -q "is polling" logs/server.log; then
  echo "✅ Telegram bot: $(grep -o '@[A-Za-z0-9_]* is polling' logs/server.log | head -1)"
else
  echo "❌ Telegram bot not running. See logs/server.log"
fi
if curl -s http://localhost:3001/status | grep -q '"connected":true'; then
  echo "✅ WhatsApp sender: linked"
else
  echo "⚠️  WhatsApp sender: not linked. Open http://localhost:3001 and scan with the spare phone."
fi
echo "🌐 Public address: ${URL:-none}"
echo "🖥️  Counter screen: http://localhost:8000/counter.html"
open http://localhost:8000/counter.html
