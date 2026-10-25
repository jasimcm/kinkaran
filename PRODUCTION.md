# Kinkaran — production readiness

What it takes to run this beyond the hackathon demo — in a real shop, every day, unattended.

## What works today (already production-like)

- **Unattended boot:** `scripts/install_launchd.sh` installs `com.kinkaran.server`, `.whatsapp` and `.tunnel`
  in `~/Library/LaunchAgents/`. They start at login and restart on crash (`KeepAlive`, 10 s throttle).
- **Nightly backup:** `com.kinkaran.backup` runs `scripts/backup.sh` at 23:30, keeping 30 copies in
  `~/KinkaranBackups` (or `BACKUP_DIR`).
- **Messages are never dropped:** anything sent while the Mac restarts is answered when it is back.
- **Hands-free flow:** voice note → item preview → *"ശരി"*/OK → print + PDF + counter screen.
- **Real printing:** `PRINT_PAPER=a4` on the HP Smart Tank (or `receipt` for 80mm thermal).
- **WhatsApp sending:** linked spare number via `whatsapp-sender/` (auto-send with cancel window).
- **Day report:** `/summary`, voice *"ഇന്നത്തെ ആകെ"*, or `GET /api/summary`.
- **No inbound exposure needed:** Telegram uses long polling — the shop needs only outbound internet.

## Known gaps to close before real shops

| Gap | Risk | Fix |
|---|---|---|
| ~~Anyone who finds the bot can bill~~ | — | **Done:** `SHOPKEEPER_IDS`; customers can only open their own bill |
| ~~Bill endpoints unauthenticated~~ | — | **Done:** API is localhost-only or needs `ACCESS_KEY`; customer links carry the bill key |
| `bills.json` / `inventory.json` flat files | corruption, no concurrent safety | SQLite (WAL) or Postgres; nightly backup to cloud/USB |
| Payment not verified | "Paid via UPI" is asserted, not proven | Razorpay/Cashfree UPI QR + webhook, or PhonePe merchant API; print "PAID" only on confirm |
| Gemini free tier | quota exhaustion mid-day | paid tier + daily quota alert; offline parser already covers typed text |
| Baileys WhatsApp sender | unofficial; number can be banned | migrate to WhatsApp Cloud API + approved message templates (per-message fee) |
| Quick tunnel URL rotates | PDF links in old WhatsApp msgs die | named Cloudflare tunnel or ngrok fixed domain; or send PDF via chat only |
| Single shop | can't scale | multi-tenant: `shop_id` on inventory/bills, register shops by chat_id |
| No corrections yet | wrong bill stays wrong | cancel + redo (cheapest), then voice edits / Edit buttons |
| Inventory-add half-wired | photo/voice item entry not live | finish handler registration in `bot.py` (photo, confirm:items, is_add_items) |

## Mac at the counter — physical checklist

- [ ] **Auto-login** the shopkeeper account (System Settings → Users) — agents start at *login*, not boot.
- [ ] **Never sleep:** System Settings → Energy → "Prevent automatic sleeping", or `sudo pmset -a sleep 0`.
- [ ] UPS or at least surge protection; on power restore, auto-login + launchd brings the stack back.
- [ ] Printer paper stock; keep `lpstat -o` empty (clear stuck jobs).
- [ ] Phone hotspot ready as backup internet — bot needs only outbound 443.

## Go-live runbook

1. `git clone` the repo to `~/easy-bill` (not `~/Downloads` — TCC blocks services there).
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. `cp .env.example .env` — set `TELEGRAM_BOT_TOKEN`, `BOT_USERNAME`, `GEMINI_API_KEY`, shop
   details, `UPI_VPA`, `PRINTER` (`lpstat -p`), `PRINT_PAPER=a4`.
4. `chmod 600 .env`
5. `cd whatsapp-sender && npm install && npm start` → link the spare phone once (QR).
6. `scripts/install_launchd.sh` writes and loads the four agents with this folder's paths. It replaces
   `./start.sh`, so don't run both. Remove them with `scripts/install_launchd.sh --uninstall`.
   For an off-site copy, set `BACKUP_DIR` to a USB drive or an iCloud Drive folder.
7. Smoke test: voice note → *"ശരി"* → paper prints → *"sales report"* → PDF. Try `/` menu.
8. Failover drill: `kill` the server PID, and watch it come back via `launchctl list | grep kinkaran`.
9. Backup drill: `scripts/backup.sh`, then restore `bills.json` from `~/KinkaranBackups` (TEST_PLAN R4).

## Daily ops (shopkeeper, zero typing)

- Morning: send "hi" to the bot → welcome reply = stack is up.
- Bills: voice note → ശരി. Corrections: currently cancel + resay (see gaps).
- Reprint: *"ഇതൊന്ന് print ചെയ്യൂ"* or *"print EB-0042"*.
- Closing: *"ഇന്നത്തെ ആകെ"* → totals + PDF for the day book.

## If it must move off the Mac

The backend is plain FastAPI — runs on a Raspberry Pi / VPS unchanged. What must stay near the
counter: the printer (CUPS/`lp`) and the counter screen. Cheapest real deployment: keep the Mac
mini/Pi at the shop exactly as now; remote parts (Gemini, Telegram) are already cloud calls.
A VPS move only makes sense for multi-shop — then printing goes through a small local agent
that polls the server and calls `lp`.
