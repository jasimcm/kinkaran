# Change log

Record each meaningful project change, newest first. Use a real timestamp with timezone,
for example `YYYY-MM-DDTHH:MM:SS+05:30`.

## 2026-10-26T02:00:00+05:30 — Release hardening: shopkeeper lock, API lock, test suite

- Changed: `backend/bot.py`, `backend/main.py`, `backend/store.py`, `backend/parser.py`,
  `backend/whatsapp.py`, `backend/wa_sender.py`, `frontend/app.js`, `frontend/counter.js`,
  `.env.example`, `README.md`, `PRODUCTION.md`; added `tests/` (137 tests), `requirements-dev.txt`
- Why: a review before release found that customers (who reach the same bot through the counter QR)
  could bill, change prices and redirect other customers' bills, and that the API was open on the tunnel.
- Details:
  - Security:
    - Only `SHOPKEEPER_IDS` can use the bot. Customers only get their own bill, and `/myid` shows a
      user's id.
    - Through the tunnel, `/api` needs `ACCESS_KEY` (cookie set via `/?key=`). On localhost no key is
      needed.
    - Telegram `/start`, WhatsApp "BILL" and PDF links carry the bill's random key.
    - The WhatsApp Cloud API bot requires `WHATSAPP_APP_SECRET`, and only `WHATSAPP_SHOPKEEPERS`
      can bill.
  - Right customer:
    - A number sent before the items is kept for the next bill instead of overwriting the last
      customer.
    - A second bill no longer cancels the first bill's WhatsApp countdown.
    - Sends keep the minimum gap even when two countdowns end together.
    - Countdowns lost in a restart are marked failed.
  - Drafts: OK and Cancel buttons carry the draft number, so an old message can't bill a newer draft.
  - Data:
    - NaN, infinite and absurd quantities are rejected.
    - Bill numbers never repeat and sort numerically, so the counter screen no longer freezes after
      EB-9999.
    - Re-adding an existing item updates its price instead of creating a duplicate (the unit is kept).
  - Intents: "add 2 kg rice", "1 kg rice total" and "stock" inside an order now make bills.
  - Screens: item names are HTML-escaped. The item preview warns that photo prices become selling
    prices.
- Validation: `python -m pytest tests -q` → 137 passed (run 3× for stability); real server
  smoke-tested on a spare port:
  - localhost API: 200
  - tunnel without key: 403
  - tunnel with key: 200
  - NaN: 422
- Limitations: one shared `ACCESS_KEY` (no per-user logins); bill drafts still live in memory.

## 2026-10-25T06:00:00+05:30 — Confirm-before-bill, resend on WhatsApp, A4 printing

- Changed: `backend/parser.py`, `backend/bot.py`, `backend/pdf_gen.py`, `backend/printer.py`,
  `backend/store.py`, `backend/main.py`, `backend/whatsapp.py`, `.env.example`, `README.md`
- Why: the shopkeeper needed a check step before a bill exists, a way to send a bill to WhatsApp
  after the fact, a voice command to reprint, and receipts that print correctly on an A4 inkjet
  (HP Smart Tank 510) instead of only opening in Preview.
- Details:
  - Voice/text order now shows an item preview with ✅ OK / ✋ Cancel; the bill is created only on
    confirm. "ശരി"/"ok" as a voice note or text confirms too.
  - "send EB-0042 to whatsapp, number 98..." attaches a number to a named bill; "whatsapp-il
    ayakkoo" resends the last bill to its saved customer.
  - "ഇതൊന്ന് print ചെയ്യൂ" / "print EB-0013" reprints a bill; the reply shows the CUPS job id.
  - `PRINT_PAPER=a4` wraps the receipt at native 80mm size centred on A4 and prints with
    `lp -o media=A4 -o fit-to-page=false`; `receipt` keeps the old 80mm behaviour.
  - `printer.print_bill` returns (mode, detail); lp stderr is logged and reported by
    `POST /api/bills/{id}/print`.
- Validation: parser unit checks for summary/confirm/whatsapp/print intents (all pass);
  `build_a4_pdf` produces a 210×297mm MediaBox with the receipt rendered at native size
  (Quick Look render inspected); lp command args and job-id parsing verified with a stubbed
  subprocess; all modules compile; `/api/summary` verified end-to-end on a running server.
- Limitations: bill drafts live in memory only (a restart loses an unconfirmed preview);
  A4 printing not yet verified on the physical printer; very long bills could overflow one
  A4 page.
