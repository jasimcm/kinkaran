# Kinkaran — production test plan

Two layers:

- **Automated** (`python -m pytest tests -q`, 137 tests, runs on every push): logic with Telegram, Gemini and
  WhatsApp faked. The `Auto` column names the test that covers a case.
- **Manual acceptance** (this file): run on the real shop Mac, real phones, real printer, real voices.
  These catch what fakes can't: speech accuracy, printer behaviour, network loss, real WhatsApp delivery.

Run the full manual pass **before go-live**. Re-run the ★ cases after every update, and once a week.

Record each run in a copy of this table: date, tester, Pass/Fail, notes.

## 0. Setup

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| S1 ★ | First run claims the bot | Empty `SHOPKEEPER_IDS`; start; send any message | Bot replies with `SHOPKEEPER_IDS=<your id>`; nothing is billed | `test_first_run_tells_the_owner_their_id` |
| S2 | Lock takes effect | Put the id in `.env`, restart, send "hi" | Kinkaran welcome (shopkeeper version) | — |
| S3 ★ | Startup checklist | `./start.sh` | ✅ bot polling, ✅ WhatsApp linked, public address shown | — |
| S4 | `.env` is private | `ls -l .env` | `-rw-------` (run `chmod 600 .env`) | — |
| S5 | Survives reboot | `scripts/install_launchd.sh`; restart the Mac, log in | Server, WhatsApp sender and tunnel come back without typing; WhatsApp still linked | `test_launchd_agents` (files only) |

## 1. Billing by voice (real speech, real phone)

Use **10 recordings per language** from the actual shopkeeper, at counter noise. Log what was heard vs said.
Target: ≥ 9/10 bills correct without editing.

| ID | Case | Say | Expected | Auto |
|---|---|---|---|---|
| V1 ★ | Malayalam basic | "രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ" | Draft: rice 2, soap 1, milk 0.5 → ₹178 | `test_voice_note_order` (faked) |
| V2 | English | "2 kg rice, 1 kg sugar, 1 litre coconut oil, 6 eggs" | ₹478 | — |
| V3 | Manglish | "randu kilo ari, oru soppu, ara litre paal" | rice 2, soap 1, milk 0.5 | `test_offline_parser` |
| V4 | Fractions | "ഒന്നര കിലോ ഉരുളക്കിഴങ്ങ്", "കാൽ കിലോ മുളക്" | 1.5 kg; 0.25 kg | `test_offline_parser` |
| V5 | Grams | "200 ഗ്രാം പച്ചമുളക്" | 0.2 kg green chilli | `test_offline_parser` |
| V6 | Not in price list | "1 kg chicken, 2 kg rice" | Rice billed; "Not in price list: chicken" | `test_not_in_price_list_is_reported_not_billed` |
| V7 | No substitution | "1 litre sunflower oil" | Sunflower oil, never coconut oil | `test_offline_parser` |
| V8 | Long order | 12 items in one note | All 12, correct quantities | — |
| V9 | Noise | V1 with a fan, traffic and customers talking | Same as V1 or a clear "didn't understand" | — |
| V10 | Silence / cough | Empty or 1-second voice note | "Couldn't find any items" — no bill | — |
| V11 | Gemini down | Wrong `GEMINI_API_KEY`, send V1 | "Please type the items"; typed text still bills | `test_voice_failure_asks_to_type` |
| V12 | Quota exhausted | Use up the day's quota (or simulate a 429) | Same as V11, no crash | `test_parse_falls_back_when_gemini_fails` |

## 2. Draft → confirm → bill

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| D1 ★ | Nothing billed before OK | Send V1 | Draft shown; `bills.json` unchanged; counter screen unchanged | `test_order_makes_a_draft_not_a_bill` |
| D2 ★ | OK button | Tap ✅ OK | Bill EB-n, PDF in chat, receipt printed, counter screen shows it | `test_ok_button_makes_and_prints_the_bill` |
| D3 | Say "ശരി" | Draft, then voice "ശരി" | Same as D2 | `test_voice_ok_confirms` |
| D4 | Double tap | Tap OK twice quickly | Exactly one bill | `test_confirm_twice_makes_one_bill` |
| D5 | Old OK | Draft A, draft B, tap OK on A | "Message is old"; nothing billed from A | `test_ok_on_an_old_draft_does_not_bill_the_new_draft` |
| D6 | Cancel | Draft, tap Cancel / say "വേണ്ട" | No bill | `test_discard_makes_no_bill` |
| D7 | Restart mid-draft | Draft, restart server, say "ശരി" | "Nothing to confirm" (drafts don't survive restarts) — resend | — |

## 3. Customer numbers and WhatsApp

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| W1 ★ | Number in the order | "…, send it on WhatsApp, 7034191969" + OK | 10 s countdown; PDF arrives on that WhatsApp; "✅ sent" | `test_auto_send_after_countdown` (faked) |
| W2 | Number spoken in English digits | "seven zero three four…" | Correct 10 digits shown before sending | — |
| W3 | Number in Malayalam digits | "ഏഴ് പൂജ്യം മൂന്ന് നാല്…" | Correct, or "type the number" — never a wrong send | — |
| W4 ★ | Wrong number, cancel | Tap Cancel within 10 s | Nothing sent; counter shows "cancelled" | `test_cancel_stops_the_send` |
| W5 | Correction | Wrong number, then type the right one during the countdown | Only the right number receives it | `test_corrected_number_replaces_pending_send` |
| W6 ★ | Next customer's number first | Bill A sent; type B's number; then B's items + OK | A's record unchanged; B's bill goes to B | `test_number_before_new_items_does_not_go_to_previous_customer` |
| W7 | Two customers back to back | A and B confirmed within 10 s | Both receive their own bill, ≥ 20 s apart | `test_second_bill_does_not_cancel_first_bills_whatsapp` |
| W8 | Not on WhatsApp | A number with no WhatsApp | "❌ not on WhatsApp" + manual button | `test_number_not_on_whatsapp_falls_back_to_button` |
| W9 | Sender offline | Stop `whatsapp-sender` | "Send on WhatsApp" button instead of countdown | `test_number_in_order_gives_whatsapp_button` |
| W10 | Limits | 11 sends in an hour | 11th: "Auto-send paused: limit" | `test_hourly_limit` |
| W11 | Restart during countdown | Confirm, kill server within 10 s, start | Bill marked failed, counter says "Could not send" — not stuck | `test_stale_countdowns_fail_on_restart` |
| W12 | PDF link in message | Open the 📄 link on the customer phone | Receipt opens (needs `PUBLIC_URL`) | `test_tunnel_customer_pdf_link_still_works` |
| W13 ★ | Cancel while busy | Bill with a wrong number (countdown runs), immediately send a long voice note, then tap Cancel | Nothing is sent to the wrong number | `test_cancel_runs_while_a_slow_message_is_being_handled` |
| W14 ★ | Stray number expires | Send number X alone; then an order with number Y; then an order with no number | Third bill is NOT sent to X | `test_stray_number_is_used_up_by_the_next_draft` |
| W15 ★ | Number first, last bill has no customer | Bill A without a number; then B's number | Bot asks "Which bill?" [A] [🆕 Next customer]; choosing next → only B's bill gets it | `test_number_first_when_last_bill_has_no_customer_asks` |
| W16 | Two countdowns, say cancel | Two bills counting down; say "cancel" | Both stop; nothing sent | `test_cancel_stops_every_countdown` |

## 4. Printing

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| P1 ★ | Real paper | Confirm a 3-item bill | Receipt prints at 80 mm width, centred, readable, QR scannable | — |
| P2 | Long bill | 40 items | Prints completely (A4 overflow check) | `test_long_bill_still_makes_a_pdf` (PDF only) |
| P3 | Printer off | Turn printer off, confirm a bill | Bill still saved and sent; bot says print failed; prints when back on | `test_print_failure_is_reported_not_raised` |
| P4 | Out of paper | Remove paper | Same as P3; `lpstat -o` shows the queued job | — |
| P5 | Reprint | "ഇതൊന്ന് print ചെയ്യൂ" | Last bill prints again | `test_reprint_by_voice_words` |
| P6 | UPI QR | Scan the printed QR with GPay | Shop name + exact amount pre-filled | — |

## 5. Customer side

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| C1 ★ | Telegram QR | Customer scans counter QR, taps Start | Gets summary + PDF; counter "✅ Bill sent on Telegram" | `test_customer_gets_bill_with_start_link` |
| C2 | Guessing bills | Customer sends `/start EB-0001` | "Couldn't find bill" | `test_customer_cannot_fetch_a_bill_by_guessing_its_number` |
| C3 ★ | Customer can't bill | Customer types "2 kg rice", "/add rice 1", "sales report" | Only the customer welcome; nothing changes | `test_customer_cannot_bill_report_or_change_prices` |
| C4 | UPI pay | Pay ₹1 test bill with GPay | Money reaches `UPI_VPA` account (manual check) | — |

## 6. Security (from a phone on mobile data, not shop Wi-Fi)

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| X1 ★ | API closed | Open `https://<tunnel>/api/bills/latest` | 403 | `test_tunnel_cannot_use_the_api_without_the_key` |
| X2 | Docs closed | `https://<tunnel>/docs` | 403 | same |
| X3 | Key login | `https://<tunnel>/?key=<ACCESS_KEY>` then the mic page | Works; key gone from the address bar | `test_shop_phone_unlocks_the_api_with_the_key` |
| X4 | Wrong key | `/?key=wrong` | 403 | same |
| X5 | Share link | Customer PDF link | Opens; changing one letter of the key → 404 | `test_pdf_and_private_share_link` |
| X6 | Sender not public | `https://<tunnel>:3001/status` | Unreachable | — |
| X7 | Bad item name | `/add <img src=x onerror=alert(1)> 10` and confirm | Shown as text on counter screen and mic page; no popup | — |
| X8 | Web page on the shop Mac | From another site in the Mac's browser, POST to `http://127.0.0.1:8000/api/bills` and `:3001/send` | Refused **(fails today: defects 4–5)** | — |
| X9 | Odd keys | `/share/EB-0001/é`, `/?key=é` | 404 / 403, never 500 **(fails today: defect 10)** | — |

## 7. Day end and data

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| R1 ★ | Day report | "ഇന്നത്തെ ആകെ" | Bill count, total, best sellers + PDF; matches hand count | `test_day_report` |
| R2 | Price change | `/add soap 42` → confirm | Soap ₹42 on new bills; old bills unchanged | `test_old_bills_keep_their_price_after_a_price_change` |
| R3 | No duplicate items | `/add coconut oil 300 litre` | Updates coconut oil; no second entry | `test_updating_an_existing_item_price_does_not_duplicate_it` |
| R4 ★ | Backup restore | `scripts/backup.sh`; delete `backend/bills.json`; copy it back from the newest `~/KinkaranBackups/kinkaran-*`; restart | All bills and prices back | `test_backup_copies_both_files_and_keeps_the_newest` (copy only) |
| R6 | Nightly backup runs | Next morning: `ls ~/KinkaranBackups` | A backup from 23:30; at most 30 kept | `test_launchd_agents` (schedule) |
| R5 | Midnight | Bill at 23:59 and 00:01 | Appear in the right day's report | — |

## 8. Failure and recovery

| ID | Case | Steps | Expected | Auto |
|---|---|---|---|---|
| F1 ★ | Internet drops / restart | Stop the server (or Wi-Fi), send a voice note from the phone, start again | Bot answers the note after it is back (kept up to 24 h) | `test_bot_uses_cancel_first_and_keeps_messages_from_downtime` |
| F2 | Server crash | `kill` the server PID | launchd restarts it within ~10 s; counter screen recovers | — |
| F3 | Power cut | Pull the plug, power on | Auto-login, services start, no corrupted JSON | — |
| F4 | Disk full | (simulate) | Bill save fails loudly, not silently | — |
| F5 | Tunnel restarts | Restart cloudflared | `PUBLIC_URL` updated by `start.sh`; old PDF links die (expected with quick tunnels) | — |

## 9. Load

| ID | Case | Expected | Notes |
|---|---|---|---|
| L1 | 100 bills in an hour | No slowdown visible on the counter screen | Measured: fine up to ~5,000 bills in `bills.json` |
| L2 | 10,000+ bills on file | `create_bill` ≈ 0.45 s, counter poll ≈ 85 ms | Measured; at 36,500 bills (≈ a year): 1.7 s / 0.36 s — **move to SQLite before that** |
