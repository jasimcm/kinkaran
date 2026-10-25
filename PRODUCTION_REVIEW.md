# Kinkaran — production review (2026-10-26)

A deliberately harsh review before running Kinkaran in a real shop. Severity:

- 🔴 **must fix before go-live**
- 🟠 **fix in the first weeks**
- 🟡 **know about it**

Test cases for every area are in [TEST_PLAN.md](TEST_PLAN.md).

## Verdict

**Ready for a supervised pilot in one shop. Not ready to run unattended.**

> Update (fixes in this release): the three wrong-customer defects, lost restart messages, backups and
> auto-start are fixed and tested (151 tests). The rest of this list is still open.

The billing core works and is tested: prices from the price list, drafts before bills, the shopkeeper lock,
and the private links. What isn't ready is everything around it: the data store, restarts, backups,
monitoring, and measured speech accuracy.

## 🔴 Must fix before go-live

1. ✅ **Fixed:** messages sent during a restart are now kept (`drop_pending_updates=False`).
   Originally: **messages sent while the server restarts were lost.**
   - The bot starts with `drop_pending_updates=True` (`bot.py`).
   - A voice note sent during a crash, restart or power cut is discarded, and nobody is told.
   - Fix: remove the flag. Telegram keeps updates for 24 hours.
2. ✅ **Fixed:** `scripts/backup.sh` runs every night at 23:30 and keeps 30 copies.
   Originally: **no backups.**
   - `bills.json` and `inventory.json` are the only copy of every bill and price, on one Mac.
   - A dead disk, a stolen laptop or `rm` loses the shop's records.
   - Fix: a nightly copy to a USB drive or cloud folder, plus a monthly restore test (TEST_PLAN R4).
3. ✅ **Fixed:** `scripts/install_launchd.sh` creates the agents, with restart on crash and start at login.
   Originally: **the auto-start files the runbook needed didn't exist.**
   - `PRODUCTION.md` says "install the three plists", but no launchd plist is in the repo.
   - `start.sh` survives closing the Terminal, but not a crash or a reboot.
   - Fix: commit `deploy/com.kinkaran.*.plist` with `KeepAlive`, and an `install.sh`.
4. **Speech accuracy is unmeasured.**
   - The 137 tests fake Gemini. Malayalam speech, and Malayalam-spoken phone numbers, have only been tried
     by hand a few times, and numbers failed twice.
   - Fix: record 50 real orders from the shopkeeper. Measure "correct without edits" per language and for
     phone numbers before trusting auto-send.
5. **The WhatsApp sender can get the number banned.**
   - Baileys is unofficial and against WhatsApp's terms. A ban takes the shop's customer messaging down
     without warning.
   - Fix: a spare SIM only. Plan the move to the WhatsApp Cloud API with an approved utility template
     (a small fee per bill).
6. **The quick tunnel's address changes on every restart.**
   - Every PDF link already sent to customers dies.
   - Fix: a named Cloudflare tunnel (free with a domain) or an ngrok fixed domain.

## 🟠 Fix in the first weeks

7. **The JSON file store doesn't scale. Measured:**

   | Bills on file | File size | Create a bill | Counter-screen check (every 2 s) |
   |---|---|---|---|
   | 1,000 | 1.7 MB | 46 ms | 8 ms |
   | 10,000 | 16.7 MB | 454 ms | 85 ms |
   | 36,500 (≈ 1 year at 100/day) | 61 MB | **1,721 ms** | **364 ms** |

   These calls are synchronous inside the async server, so **the Telegram bot and every screen freeze while
   they run**. Fix: SQLite (WAL mode) with an index on bill number and date, run off the event loop.
   SQLite also gives atomic writes and easy backups.
8. **Python 3.9 is end-of-life** (no security fixes since October 2025). *(The Oracle deployment runs
   Ubuntu 24.04's Python 3.12; the print helper on the Mac only needs the built-in python3.)*
   - ReportLab is already held below version 5 because of it.
   - Fix: install Python 3.12 (`brew install python@3.12`) and rebuild the venv. CI already tests 3.12.
9. **Dependencies aren't locked.**
   - `requirements.txt` uses ranges, so a reinstall next month can pull different versions.
   - Baileys is a release candidate (`7.0.0-rc14`).
   - Fix: `pip freeze > requirements.lock` and install from it; keep `package-lock.json` committed.
10. **No monitoring.** If the bot dies at 9 am, the shopkeeper finds out from an angry customer.
    - Fix: a heartbeat (the bot messages the owner if it restarted), a `/health` endpoint, and an alert
      when printing or Gemini fails three times in a row.
11. **Logs grow forever** on the Mac: `logs/*.log` are never rotated. Fix: rotate daily with `newsyslog`.
    *(In the cloud, systemd's journal rotates logs automatically.)*
12. **Drafts live only in memory.**
    - A restart between "2 kg rice" and "ശരി" loses the draft.
    - Fix: keep drafts in the database with a 30-minute expiry.
13. **A confirmed bill can't be corrected or cancelled.**
    - There is no void, edit or return, so a wrong bill stays in the day total forever.
    - Fix: "cancel EB-0042" by the shopkeeper marks it void, kept for audit and excluded from totals.
14. **"Paid via UPI" is printed on every bill, paid or not.**
    - This is misleading on a receipt.
    - Fix: print "Pay via UPI" until a payment provider's webhook confirms payment.
15. **Gemini changes under you.**
    - `gemini-flash-lite-latest` is an alias, so Google can swap the model and accuracy changes overnight.
    - Fix: pin a dated model name and re-run the 50-recording accuracy set before changing it.

## 🟡 Know about it

16. **One shop, one price list, one access key.** No per-user logins or roles. Fine for one shop.
17. **Personal data.** Customer phone numbers are stored forever in plain JSON. India's DPDP Act expects a
    purpose, consent and a retention limit. Delete numbers after, say, 90 days, and say so at the counter.
18. **Receipts are English-only.** ReportLab can't shape Malayalam, so the customer's PDF has no
    Malayalam names. A shaping-capable renderer (WeasyPrint with a Malayalam font) would fix it.
19. **No GST or invoice fields.** No GSTIN, tax split, HSN or round-off. Fine for small unregistered shops;
    required once the shop is GST-registered.
20. **No stock levels.** "Stock" means the price list. Nothing tracks quantity on hand.
21. **It needs internet for everything.** Telegram and Gemini are cloud services. When the internet is
    down, the bot is down; only the mic page's offline parser works, and only on the shop network.
22. **Gemini sees every order.** The free tier may use prompts to improve Google's models. Use the paid
    tier for customer data.
23. **Tests use fakes.** No test talks to real Telegram, WhatsApp or a printer. The manual plan covers that.

## Code defects still open (second review of commit df5c201)

Each was confirmed with a scenario or snippet. **Items 1–3 can send a bill to the wrong customer**, so
they are go-live blockers. Items 2 and 3 come from the release-hardening fix itself.

| # | Sev | Defect | Failure |
|---|---|---|---|
| 1 | ✅ | **Fixed: Cancel and typed "cancel" skip the queue (`CancelFirst`).** Was: **Cancel can arrive too late.** The bot handles one Telegram message at a time (`concurrent_updates=1`), but the WhatsApp countdown runs on its own | The shopkeeper taps Cancel while the next voice note is being transcribed (up to 40 s). The cancel waits in line, and the bill goes to the wrong number |
| 2 | ✅ | **Fixed: a noted number is used up by the next draft and expires after 5 min.** Was: **A stray "next customer" number never expires.** It is only used when the next order has no number of its own | Number X is noted; the next order comes with its own number; a later order with no number is sent to X |
| 3 | ✅ | **Fixed: the bot asks which bill.** Was: **A number given before the items can land on the previous bill** when that bill has no customer (e.g. the customer used the QR) | Customer B's number is attached to A's bill, and A's bill is sent to B. After a restart this can hit a day-old bill |
| 4 | 🟠 | **"Localhost is trusted" can be abused by any web page opened on the shop Mac.** There is no Origin/Host check | A malicious page in the Mac's browser can create and print bills, and (via DNS rebinding) read customer phones |
| 5 | 🟠 | **The WhatsApp sender's `/send` has no secret** | A web page on the Mac can make the linked number message anyone, which risks a ban |
| 6 | ✅ | **Fixed: "cancel" stops every countdown.** Was: **"cancel" stops only the newest countdown** | Two countdowns at once: "cancel" stops the second; the first still sends |
| 7 | 🟠 | **Synchronous file I/O on the event loop** (see review item 7) | After about a year, every bill freezes the bot for about 1.8 s |
| 8 | 🟠 | **Malayalam-only new item names collide on one id** | Adding "പരിപ്പ്" overwrites the price of "പഴം" (`item_4c2kvyfu` for both) |
| 9 | 🟡 | **Editing a sent Telegram message crashes the handler** (`update.message` is None) | "Something went wrong"; the edit is ignored |
| 10 | ✅ | **Fixed: keys are compared as bytes.** Was: **A non-ASCII key gives a 500 error** (`compare_digest` on str) | `/share/EB-0001/é` → 500 instead of 404 |
| 11 | 🟡 | **The draft is saved before its preview is sent** | A very long order exceeds Telegram's 4096-character limit, and a hidden draft can be confirmed with "ok" |
| 12 | 🟡 | **The draft counter resets on restart; "ok" ignores draft ids** | An old OK button can confirm a new draft after a restart |
| 13 | 🟡 | **The key cookie is always `Secure` off-localhost** | A second screen on plain-HTTP shop Wi-Fi can never log in |
| 14 | 🟡 | **The mic page shows validation errors as "[object Object]"** | A quantity over 10,000 shows a useless error |
| 15 | 🟡 | **Banker's rounding**: 0.5 × ₹12.25 = ₹6.12, not ₹6.13; a fractional qty is allowed for piece items via the API | Paise-level differences vs a calculator |

What the second review found **clean**:
- Every shopkeeper handler is guarded.
- The tunnel check can't be spoofed through cloudflared.
- `/?key=` is not an open redirect.
- No unescaped HTML is left on the screens.
- The send-gap loop doesn't busy-wait.
- Emoji or empty input doesn't crash.

## Go-live order

1. **Code:** fix defects 1–3 (wrong customer) and review items 1–3 (lost messages, backups, auto-start).
2. **Accuracy:** measure speech accuracy with 50 real recordings (review item 4) and decide whether auto-send
   stays on or the button is the default.
3. **Pilot:** run the full [TEST_PLAN.md](TEST_PLAN.md) on the shop Mac, then a **one-week supervised pilot**:
   the shopkeeper checks every draft, and you read the logs daily.
4. **Early weeks:** SQLite, Python 3.12, locked dependencies, monitoring and the remaining 🟠 items
   during the pilot.
