# Kinkaran 🧾🎙️

**Voice billing for small rural shops in Kerala.**

> **മുതലാളി പറഞ്ഞാ മതി, ബാക്കി കിങ്കരൻ നോക്കിക്കോളും.** 💪
> *The boss just says it — Kinkaran does the rest.*

**കിങ്കരൻ / Kinkaran** — the helper who does the work. The shopkeeper sends a voice note with
the items, in Malayalam, English or Manglish; Kinkaran builds the bill from the shop's own price
list, prints it, shows it on the counter screen with a UPI QR, and sends the PDF to the customer
on Telegram or WhatsApp. No typing, no billing software, no extra staff.

Built at the **Devin Hackathon**, JAIN University, Kochi — 24–25 October 2026.
Track: *Kill the Busywork*. Code name: **Easy Bill** — the Telegram bot is **@KINKARANBOT**.

## Project overview

- **Project name:** Kinkaran (code name Easy Bill)
- **Short description:** A shopkeeper sends a Telegram voice note with the items — in Malayalam,
  English or Manglish (*"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ"*). Kinkaran builds the bill
  using the shop's own price list, shows it on a counter screen with a UPI QR, and sends the bill
  as a PDF back on Telegram or WhatsApp. No typing, no billing software, no printer needed.
- **Team:** Jasim CM ([@jasimcm](https://github.com/jasimcm)) — add teammates here
- **Working demo:** run `./start.sh`, then follow the 2-minute script in [DEMO.md](DEMO.md)
- **Technology stack:** Python 3.9+ (FastAPI, uvicorn, python-telegram-bot), Google Gemini API
  (audio transcription + item matching), ReportLab (PDF), vanilla-JS PWA (counter screen + mic page),
  optional Node.js 20 + Baileys (hands-free WhatsApp sender)

```
 Shopkeeper's Telegram ──voice note──▶ Telegram servers ◀──long polling── Mac: FastAPI (one Python process)
                                                                           ├─ Gemini: audio → transcript → item ids + qty
                                                                           ├─ prices ALWAYS from inventory.json → bills.json
                                                                           ├─ ReportLab 80mm receipt → Preview / printer
                                                                           ├─ counter.html: bill + UPI QR + Telegram QR
                                                                           └─ replies with summary + PDF
 Customer scans Telegram QR (t.me/<bot>?start=EB-0042_<key>) → taps Start → bot sends the PDF
```

The Mac asks Telegram for new messages ("long polling"), so **no webhook, tunnel or public URL is needed**.
The Mac only needs internet.

## Run the project

Prerequisites: Python 3.9+, a Telegram account for the bot, a free Gemini API key. Node.js 20+ only
for the optional WhatsApp sender.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in TELEGRAM_BOT_TOKEN, BOT_USERNAME, GEMINI_API_KEY, shop details
./start.sh                  # server + bot + tunnel + counter screen in one command
python backend/main.py      # or just the server and bot, by hand
```

Then open **http://localhost:8000/counter.html** on the Mac and send the bot a voice note.
Full setup walkthrough (BotFather, Gemini key, printer, WhatsApp): [Setup](#setup-about-10-minutes) below.

## Current status

- **Working features:** Malayalam/English/Manglish voice-note billing; typed input with an offline
  fallback parser; prices always from `inventory.json` (Gemini never sets a price); 80mm PDF receipts;
  customer gets the bill via Telegram deep-link QR or WhatsApp (`wa.me` message, or hands-free
  auto-send through a linked spare number); counter screen with UPI payment QR; web mic page.
- **Known limitations:** single shop, JSON storage; UPI payment is not verified
  (see [Limits](#limits-of-this-mockup)). Only `SHOPKEEPER_IDS` can use the bot, and the API needs
  `ACCESS_KEY` from outside the Mac (see [Security](#security)).
- **Libraries, APIs, datasets:** [FastAPI](https://fastapi.tiangolo.com),
  [python-telegram-bot](https://python-telegram-bot.org), [Gemini API](https://ai.google.dev),
  [ReportLab](https://www.reportlab.com), [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz),
  [QRCode.js](https://github.com/davidshimjs/qrcodejs), [Baileys](https://github.com/WhiskeySockets/Baileys)
  (unofficial WhatsApp Web), [WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp).

See [AI_USAGE.md](AI_USAGE.md) for AI assistance and [CHANGELOG.md](CHANGELOG.md) for the development
record. Submission details: [SUBMISSION.md](SUBMISSION.md).

## Project layout

```
easy-bill/
├── backend/
│   ├── main.py         FastAPI app: API, serves the screens, starts the Telegram bot, WhatsApp webhook
│   ├── bot.py          Telegram bot: voice note/text → bill; /start <BillID> → customer gets PDF
│   ├── parser.py       Gemini (audio and text) + offline fallback parser
│   ├── pdf_gen.py      80mm receipt PDF with UPI QR
│   ├── printer.py      Opens the receipt in Preview or prints it (lp)
│   ├── store.py        Config (.env), inventory, bills.json storage
│   ├── wa_sender.py    Talks to whatsapp-sender/ for hands-free WhatsApp sending
│   ├── whatsapp.py     Optional WhatsApp bot (official Cloud API)
│   └── inventory.json  20 grocery items with Malayalam/English/Manglish aliases
├── frontend/
│   ├── counter.html counter.js Counter screen on the Mac (faces the customer)
│   ├── index.html  app.js      Mic billing page (phone)
│   └── style.css  manifest.json  sw.js  icons/
├── whatsapp-sender/    Node + Baileys: sends bills from a linked spare WhatsApp (unofficial)
├── requirements.txt
├── .env.example
├── DEMO.md             2-minute demo script for the judges
└── README.md
```

## Setup (about 10 minutes)

### 1. Install
Needs Python 3.9 or newer.

```bash
cd easy-bill
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

### 2. Create the Telegram bot (2 min)
1. In Telegram, open **@BotFather** and send `/newbot`.
2. Pick a name (e.g. *Sreeja Stores Bill*) and a username ending in `bot` (e.g. `SreejaBillBot`).
3. Put the token BotFather sends you in `TELEGRAM_BOT_TOKEN`, and the username without `@` in `BOT_USERNAME`.

### 3. Gemini API key (2 min, needed for voice notes)
1. Go to **https://aistudio.google.com/apikey**, sign in, click **Create API key**.
2. Put it in `GEMINI_API_KEY`. The default model `gemini-flash-lite-latest` is free and understands audio.

Each voice note uses one free Gemini request; check your daily limit in AI Studio. Typed items still work
without Gemini, using the offline parser.

### 4. Shop details and printing
Set `SHOP_NAME`, `SHOP_ADDRESS`, `SHOP_PHONE` and `UPI_VPA` (the UPI ID that receives the money).

`PRINTER` decides what happens to each new bill:

| `PRINTER=` | What happens |
|---|---|
| `preview` *(default)* | The 80mm receipt opens in Preview on the Mac. No printer needed. |
| *(empty)* | Only saved as `backend/printed/EasyBill_EB-0042.pdf` |
| a printer name | Printed with `lp` (list names with `lpstat -p`, e.g. `HP_Smart_Tank_510_series`) |

`PRINT_PAPER` picks the page layout:

| `PRINT_PAPER=` | What happens |
|---|---|
| `a4` *(default)* | The receipt at native 80mm size, centred at the top of an A4 sheet — for inkjet/laser printers. Sent with `lp -o media=A4 -o fit-to-page=false`, so CUPS can't scale it up and the QR stays scannable. |
| `receipt` | The 80mm-wide page itself — for a thermal printer. |

Check the print queue with `lpstat -o`. The shopkeeper can also reprint by voice or text:
*"ഇതൊന്ന് print ചെയ്യൂ"* / *"print"* for the last bill, or *"print EB-0013"* for a specific one.

### 5. Run

**Everything with one command** (server + Telegram bot, Cloudflare tunnel, WhatsApp sender):

```bash
./start.sh     # also updates PUBLIC_URL in .env and opens the counter screen
./stop.sh      # stop everything
```

It keeps running after you close the Terminal. Logs are in `logs/`.

**In the shop, use auto-start instead.** It restarts after a crash, starts at login, and backs up every night:

```bash
scripts/install_launchd.sh              # replaces ./start.sh; check with: launchctl list | grep kinkaran
scripts/install_launchd.sh --uninstall  # remove
```

**Backups:** `scripts/backup.sh` copies `bills.json` and `inventory.json` to `~/KinkaranBackups`
(or `BACKUP_DIR=/Volumes/USB/kinkaran`), checks the copies, and keeps the newest 30. It runs every night at
23:30 under launchd. To restore, stop the server and copy both files from the newest backup folder
into `backend/`.

To run only the server and bot by hand instead:

```bash
python backend/main.py
```

The log should say `Telegram bot @SreejaBillBot is polling.`
Open **http://localhost:8000/counter.html** on the Mac. Press `Ctrl+Cmd+F` for full screen, and turn it to face the customer.

### 6. Try it
1. On the shopkeeper's phone, open your bot in Telegram, tap **Start**, and send a voice note:
   *"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ"*.
2. In about 5–10 seconds the bot replies with what it heard and the parsed bill, with **✅ OK** /
   **✋ Cancel** buttons. Tap **OK** — or just say *"ശരി"* — to make the bill: it prints, the PDF
   arrives, and the counter screen shows it with two QR codes. **Cancel** discards it: no bill is made.
3. The customer scans the **Telegram QR** and taps **Start**. The bot sends them the PDF, and the counter screen
   shows **✅ Bill sent on Telegram**.

You can also type the items to the bot instead of speaking.

**Day-end report:** ask the bot *"ഇന്നത്തെ ആകെ"* or *"sales report"* (voice note, text, or `/summary` on
Telegram) for today's bill count, total sales and best sellers — text plus a PDF. The same works on
WhatsApp, and the counter screen can read it from `GET /api/summary`.

**Forgot the number?** Say or type it afterwards, e.g. *"send EB-0042 to whatsapp, number 98470 12345"* —
the number attaches to that bill and the send countdown starts. *"whatsapp-il ayakkoo"* alone resends the
last bill to its saved customer.

### 7. Send the bill to the customer's WhatsApp (optional)
Say the customer's number at the end of the voice note:
*"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, നമ്പർ 98470 12345"*.

1. The number is never billed as a quantity.
2. The bot replies with a **📲 Send on WhatsApp to +91 98470 12345** button.
3. Tapping it opens the shopkeeper's own WhatsApp, in a chat with that customer, with the bill already typed in.
   Tap send. This is free and works with any number; no Meta setup is needed.

WhatsApp can't attach the PDF this way, so the message includes a link to it. For that link to work,
run a tunnel and put its URL in `PUBLIC_URL`:

```bash
cloudflared tunnel --url http://localhost:8000     # copy the https://….trycloudflare.com URL into PUBLIC_URL
```

Restart the server after changing `.env`. The quick-tunnel URL changes on every restart, so links in old
messages stop working; ngrok's free fixed domain avoids that.

### 8. Send on WhatsApp automatically, hands-free (spare number)
With this set up, the shopkeeper doesn't touch the button. The bot sends the PDF to the customer's
WhatsApp by itself, after a **10-second countdown** so the number can be checked:

```
Voice note: "2 kg rice, 1 soap, send it on WhatsApp, the number is 7034191969"
→ Telegram and the counter screen show, in large text:
     📲 EB-0007 → +91 70341 91969  ·  Sending on WhatsApp in 10 seconds…  [ ✋ Cancel ]
→ Number right: do nothing. The PDF and summary arrive on the customer's WhatsApp → "✅ Sent".
→ Number wrong: tap Cancel, or say or type "cancel" / "വേണ്ട". Or just send the right number,
  which replaces the wrong one and starts a new countdown.
```

> ⚠️ **This uses Baileys, an unofficial WhatsApp Web library.** It's against WhatsApp's terms, and WhatsApp
> may ban the linked number. **Link a spare SIM, never the shop's main number.** The official way is the
> WhatsApp Cloud API with a paid, pre-approved template (see "Extra: WhatsApp bot" below).

Setup (needs Node.js 20 or newer):

```bash
cd whatsapp-sender
npm install
npm start
```

1. Open **http://localhost:3001** on the Mac. It shows a QR code.
2. On the spare phone: **WhatsApp → Settings → Linked devices → Link a device**, and scan the QR.
3. The page changes to **✅ Linked as +91…**. The session is saved in `whatsapp-sender/auth/`, so after a
   restart you don't need to scan again. To link a different phone, delete that folder.

Keep `npm start` running next to `python backend/main.py`. If the sender isn't running or linked,
or the number isn't on WhatsApp, the bot falls back to the **📲 Send on WhatsApp** button.
`AUTO_SEND_SECONDS` in `.env` changes the countdown. The sender only listens on `127.0.0.1`,
so the tunnel doesn't expose it.

**Ban safety settings** in `.env`. When a limit is reached, the bot falls back to the button and says why.

| Setting | Default | Meaning |
|---|---|---|
| `AUTO_SEND` | `on` | `off` = never auto-send; always show the button |
| `WA_MAX_PER_HOUR` | `10` | Most auto-sends in one hour |
| `WA_MAX_PER_DAY` | `30` | Most auto-sends in 24 hours |
| `WA_MIN_GAP_SECONDS` | `20` | The countdown is stretched so two sends are at least this far apart |

If WhatsApp shows the linked phone a temporary ban, stop, set `AUTO_SEND=off`, unlink "Easy Bill" in
Linked devices, and wait it out.

Why not send from the bot itself?
- **Telegram:** bots can't message anyone by phone number.
- **WhatsApp:** a business can only message someone first with a paid, pre-approved template.

Prices and items are in `backend/inventory.json`. Edit them and restart.

## How it works

- **Voice notes:** the bot downloads the `.ogg` voice note and sends it to Gemini together with the inventory.
  Gemini returns `{"transcript", "items":[{"item_id","qty"}]}`. If Gemini hears words but finds no items,
  the offline parser tries the transcript.
- **Typed items:** Gemini first, then the offline parser (`parser.py`). The offline parser understands:
  - Malayalam number words (അര 0.5, ഒരു 1, ഒന്നര 1.5 … പത്ത് 10), Manglish (*randu, ara*), English and digits
  - units: kg/കിലോ, litre/ലിറ്റർ, gram/ഗ്രാം, dozen/ഡസൻ
  - item aliases including "ഉം" forms (*സോപ്പും*)
  - rapidfuzz fuzzy matching for misspellings
- **Prices:** always from `inventory.json`, on the server. Gemini never sees or sets prices.
- **Items not in the price list:** Gemini must only match the same product. It may not turn sunflower oil
  into coconut oil, for example. Anything it can't match gets a second try with the offline parser, then
  the bot lists it under **⚠️ Not in price list (not billed)** so the shopkeeper can add it to `inventory.json`.
  Packet and piece items are always whole numbers.
- **Getting the bill to the customer:** a Telegram bot can't message someone who hasn't started a chat with it.
  So the counter screen shows `https://t.me/<bot>?start=EB-0042_<key>`. The customer taps **Start**, which sends
  `/start EB-0042`, and the bot replies with the PDF. The bot also gives the shopkeeper this link to forward.
- **PDF:** ReportLab, 80mm receipt. Item names are in English because ReportLab can't shape Malayalam text.
  Malayalam names appear on the screens.
- **UPI:** `upi://pay?pa=…&pn=…&am=…&cu=INR&tn=EB-0042`. GPay, PhonePe, Paytm and BHIM all read it.

## API

| Method | Path | Body → Response |
|---|---|---|
| POST | `/api/parse` | `{transcript}` → `{items:[…], total, source}` |
| POST | `/api/bills` | `{items:[{item_id,qty}]}` → bill + `upi_link`, `telegram_link`, `whatsapp_link` (also prints) |
| GET | `/api/bills/latest` | Newest bill, or `null` (used by the counter screen) |
| GET | `/api/bills/{id}` | Bill with links and `delivered` / `delivered_via` |
| GET | `/api/bills/{id}/pdf` | The PDF |
| POST | `/api/bills/{id}/print` | Print again |
| GET | `/api/inventory`, `/api/config` | Item list; shop name and which features are on |
| GET | `/share/{id}/{key}` | PDF link used in WhatsApp messages (needs the bill's random key) |
| GET/POST | `/webhook/whatsapp` | Optional WhatsApp webhook |

Interactive API docs: http://localhost:8000/docs

## Troubleshooting

| Problem | Fix |
|---|---|
| Log says `TELEGRAM_BOT_TOKEN not set` | Fill in `.env` and restart. |
| Bot doesn't reply at all | Only one copy of the server can poll a bot. Stop any other running copy, including on a teammate's laptop. |
| "Couldn't process the voice note" | Check `GEMINI_API_KEY` and its daily free limit in AI Studio. Typing the items still works. |
| Counter QR says "Set BOT_USERNAME" | Set `BOT_USERNAME` without `@`, then restart. |
| Nothing opens or prints | Use `PRINTER=preview`, or copy the exact name from `lpstat -p`. Check the queue with `lpstat -o`. |
| Bill prints huge or cut off on A4 | `PRINT_PAPER` should be `a4` (default): the job goes out with `-o media=A4` and no fit-to-page, so it can't be scaled up. `receipt` is for 80mm thermal printers only. |
| Print failed message in Telegram | The reply shows `lp`'s error. Usually the printer name or a stuck job — see `lpstat -o`. |
| Want a fresh bill counter | Stop the server and delete `backend/bills.json`. |

## Extra: mic page on an Android phone

The phone mic needs HTTPS, so run a tunnel:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

Open the `https://….trycloudflare.com` URL in Chrome on the phone and allow the microphone.
Use Chrome menu ⋮ → **Add to Home screen** to install it. The **▶ Demo** button fills in sample sentences.

## Extra: WhatsApp bot (optional, ≈ 45 min setup)

The same voice-note flow also works on WhatsApp through Meta's WhatsApp Cloud API. It is harder to set up
than Telegram:
- Meta's free test number can only message up to 5 phone numbers that you add in the dashboard.
- It needs a webhook address that never changes.
- The first access token expires after 24 hours.

1. **Fixed HTTPS address:** sign up at dashboard.ngrok.com, claim your free domain, then run
   `ngrok http --url=<your-domain>.ngrok-free.app 8000`.
2. **Meta app:** at developers.facebook.com → **Create app** → use case **Connect with customers through WhatsApp**.
3. **Copy the settings:** in **WhatsApp → API Setup**, copy the token to `WHATSAPP_TOKEN` and the Phone number ID
   to `WHATSAPP_PHONE_NUMBER_ID`. Put the test number, digits only, in `WHATSAPP_NUMBER`.
   Add up to 5 recipient numbers under **To**.
4. **Webhook:** in **WhatsApp → Configuration → Webhook**, set:
   - Callback URL: `https://<your-domain>.ngrok-free.app/webhook/whatsapp`
   - Verify token: the same word as `WHATSAPP_VERIFY_TOKEN`

   Click **Verify and save**, then subscribe to **messages**.
5. **Signature check (optional):** **App settings → Basic → App secret** → `WHATSAPP_APP_SECRET`.
6. **Permanent token:** business.facebook.com → Settings → System users → generate a token that never expires,
   with `whatsapp_business_messaging` and `whatsapp_business_management`.

On WhatsApp the counter screen shows a `wa.me` QR. The customer sends "BILL EB-0042 <key>" and gets the PDF.
The Cloud API bot needs `WHATSAPP_APP_SECRET` (webhook signatures are always checked), and only numbers in
`WHATSAPP_SHOPKEEPERS` can make bills or see reports.

## Security

| Who | Can do |
|---|---|
| Telegram users in `SHOPKEEPER_IDS` | Everything: bill, print, reports, change prices, send on WhatsApp |
| Anyone else on Telegram (customers) | Only open their own bill with the counter-screen QR (`/start EB-0042_<key>`), and `/myid` |
| This Mac (localhost) | The whole API and screens, no key needed |
| Through the tunnel, with `ACCESS_KEY` | The API (the mic page on the shop phone) |
| Through the tunnel, without the key | Only customer PDF links `/share/EB-0042/<key>`, the static screens (no data) and Meta's webhook |

**First run:** start the bot and send it `/myid` (or any message). It replies with your Telegram ID. Put it in
`.env` as `SHOPKEEPER_IDS=123456789` (several people: `123,456`) and restart.

**Mic page on the shop phone:** set `ACCESS_KEY` to a long random word (e.g. `openssl rand -hex 16`), then open
`https://<tunnel>/?key=<ACCESS_KEY>` once. The phone remembers it; the key disappears from the address bar.

Every customer link (Telegram, WhatsApp, PDF) carries the bill's random key, so bills can't be fetched by
guessing numbers. Item names are escaped on the screens, and quantities must be real numbers (no NaN).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

137 tests run offline in a few seconds, and on every push through GitHub Actions
(`.github/workflows/tests.yml`, Python 3.9 and 3.12). Telegram, Gemini and WhatsApp are faked, and every test uses a
temporary copy of the bills and price list, so nothing real is sent, billed or printed.

## Limits of this mockup
- **Storage:** one shop, one price list, bills in a JSON file.
- **Who can bill:** anyone who sends items to the bot creates a bill. A real version would only accept the
  shopkeeper's Telegram account.
- **Payment:** "Paid via UPI" is printed on the bill, but the app doesn't check that the payment arrived.
- **One access key:** the API through the tunnel is protected by a single shared `ACCESS_KEY`, not per-user
  logins. Keep it secret, and change it if a phone is lost.
