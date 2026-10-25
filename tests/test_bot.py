"""Telegram conversations end to end, with Telegram, Gemini and WhatsApp faked.

`fake_wa.linked = False` is the normal case for these tests: WhatsApp is not connected,
so the bot must fall back to the "Send on WhatsApp" button instead of sending.
"""
import store


def test_greeting_needs_no_gemini(chat, no_gemini_needed):
    assert "കിങ്കരൻ" in chat.say("hi")


# ---------------------------------------------------------------- draft -> confirm -> bill

def test_order_makes_a_draft_not_a_bill(chat, fake_wa):
    reply = chat.say("രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ")
    assert "Total: ₹178.00" in reply
    assert chat.buttons == ["confirm:bill:1", "discard:bill:1"]
    assert store.latest_bill() is None, "a bill was created before the shopkeeper confirmed"


def test_ok_button_makes_and_prints_the_bill(chat, fake_wa, isolated_data):
    chat.say("2 kg rice, 1 soap")
    chat.press("confirm:bill")
    bill = store.latest_bill()
    assert bill["bill_id"] == "EB-0001" and bill["total"] == 150
    assert chat.documents == ["EasyBill_EB-0001.pdf"]
    assert (isolated_data / "printed" / "EasyBill_EB-0001.pdf").exists()


def test_saying_ok_confirms_too(chat, fake_wa):
    chat.say("2 kg rice")
    chat.say("ശരി")
    assert store.latest_bill()["total"] == 110


def test_confirm_twice_makes_one_bill(chat, fake_wa):
    chat.say("2 kg rice")
    chat.press("confirm:bill")
    assert "already used" in chat.press("confirm:bill:1")
    assert store.latest_bill()["bill_id"] == "EB-0001"


def test_discard_makes_no_bill(chat, fake_wa):
    chat.say("2 kg rice")
    chat.press("discard:bill")
    assert store.latest_bill() is None


def test_a_new_order_replaces_the_open_draft(chat, fake_wa):
    chat.say("2 kg rice")
    chat.say("1 soap")
    chat.say("ok")
    bill = store.latest_bill()
    assert [line["item_id"] for line in bill["items"]] == ["soap"]


def test_not_in_price_list_is_reported_not_billed(chat, fake_gemini, fake_wa):
    fake_gemini.replies.append({"items": [{"item_id": "rice", "qty": 2}], "unknown": ["1 kg chicken"]})
    reply = chat.say("2 kg rice, 1 kg chicken")
    assert "Not in price list" in reply and "chicken" in reply and "Total: ₹110.00" in reply


def test_nonsense_is_not_understood(chat, fake_wa):
    assert "couldn't find any items" in chat.say("blah blah")


# ---------------------------------------------------------------- voice notes

def test_voice_note_order(chat, fake_gemini, fake_wa):
    reply = chat.voice({"transcript": "രണ്ട് കിലോ അരി", "items": [{"item_id": "rice", "qty": 2}], "unknown": [], "phone": ""}, fake_gemini)
    assert "Total: ₹110.00" in reply


def test_voice_ok_confirms(chat, fake_gemini, fake_wa):
    chat.say("2 kg rice")
    chat.voice({"transcript": "ശരി", "items": [], "unknown": [], "phone": ""}, fake_gemini)
    assert store.latest_bill() is not None


def test_voice_failure_asks_to_type(chat, fake_gemini, fake_wa):
    assert "type the items" in chat.voice(RuntimeError("Gemini down"), fake_gemini)


# ---------------------------------------------------------------- phone numbers with WhatsApp NOT connected

def test_number_in_order_gives_whatsapp_button(chat, fake_wa):
    chat.say("2 kg rice, 1 soap, send it on whatsapp the number is 7034191969")
    chat.press("confirm:bill")
    assert store.latest_bill()["customer_phone"] == "917034191969"
    assert any(button.startswith("https://wa.me/917034191969") for button in chat.buttons)
    assert fake_wa.sent == []


def test_number_while_draft_is_open(chat, fake_wa):
    chat.say("2 kg rice")
    assert "Noted +91 70341 91969" in chat.say("7034191969")
    chat.press("confirm:bill")
    assert store.latest_bill()["customer_phone"] == "917034191969"


def test_number_after_the_bill(chat, fake_wa):
    chat.say("2 kg rice")
    chat.press("confirm:bill")
    chat.say("70341 91969")
    assert store.latest_bill()["customer_phone"] == "917034191969"


def test_number_for_a_named_bill(chat, fake_wa):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    chat.say("1 soap"); chat.press("confirm:bill")
    chat.say("send EB-0001 to whatsapp 7034191969")
    assert store.get_bill("EB-0001")["customer_phone"] == "917034191969"
    assert store.get_bill("EB-0002")["customer_phone"] is None


def test_number_before_any_bill_is_kept_for_it(chat, fake_wa):
    assert "noted for the next bill" in chat.say("7034191969")
    assert "For +91 70341 91969" in chat.say("2 kg rice")
    chat.press("confirm:bill")
    assert store.latest_bill()["customer_phone"] == "917034191969"


def test_misheard_short_number(chat, fake_wa):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    assert "not a 10-digit mobile" in chat.say("934191969")


# ---------------------------------------------------------------- WhatsApp linked (faked): countdown and sending

def test_auto_send_after_countdown(chat, fake_wa):
    fake_wa.linked = True
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")
    assert chat.buttons == ["cancel:EB-0001"]
    chat.wait()
    assert fake_wa.sent == [("EB-0001", "917034191969")]
    assert store.get_bill("EB-0001")["delivered_via"] == "whatsapp"
    assert "sent to +91 70341 91969" in chat.replies[-1]


def test_cancel_stops_the_send(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 5)
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")
    chat.press("cancel:EB-0001")
    chat.wait()
    assert fake_wa.sent == [] and store.get_bill("EB-0001")["send_status"] == "cancelled"


def test_saying_venda_stops_the_send(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 5)
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")
    chat.say("വേണ്ട")
    assert fake_wa.sent == [] and store.get_bill("EB-0001")["send_status"] == "cancelled"


def test_corrected_number_replaces_pending_send(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 0.2)
    chat.say("2 kg rice 8044191969"); chat.press("confirm:bill")
    chat.say("7034191969")
    chat.wait(0.5)
    assert fake_wa.sent == [("EB-0001", "917034191969")]


def test_number_not_on_whatsapp_falls_back_to_button(chat, fake_wa):
    fake_wa.linked, fake_wa.error = True, "This number is not on WhatsApp"
    chat.say("2 kg rice 9999999999"); chat.press("confirm:bill")
    chat.wait()
    assert "not on WhatsApp" in chat.replies[-1]
    assert store.get_bill("EB-0001")["send_status"] == "failed"


def test_auto_send_switched_off(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND", False)
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")
    chat.wait()
    assert fake_wa.sent == [] and any("Auto-send paused" in reply for reply in chat.replies)


def test_hourly_limit(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "WA_MAX_PER_HOUR", 1)
    chat.say("1 soap 7034191969"); chat.press("confirm:bill"); chat.wait()
    chat.say("1 salt 9847012345"); chat.press("confirm:bill"); chat.wait()
    assert len(fake_wa.sent) == 1 and "limit of 1 sends per hour" in "\n".join(chat.replies)


def test_resend_last_bill(chat, fake_wa):
    fake_wa.linked = True
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill"); chat.wait()
    chat.say("whatsapp-il ayakkoo"); chat.wait(1.5)  # sends keep a minimum gap (+1 s margin)
    assert fake_wa.sent == [("EB-0001", "917034191969"), ("EB-0001", "917034191969")]


# ---------------------------------------------------------------- reprint, report, customer, stock

def test_reprint_by_voice_words(chat, fake_wa, monkeypatch):
    printed = []
    monkeypatch.setattr("printer.print_bill", lambda bill: printed.append(bill["bill_id"]) or ("saved", None))
    chat.say("2 kg rice"); chat.press("confirm:bill")
    chat.say("ഇതൊന്ന് print ചെയ്യൂ")
    assert printed == ["EB-0001", "EB-0001"]


def test_day_report(chat, fake_wa, no_gemini_needed):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    assert "Bills: 1" in chat.say("ഇന്നത്തെ ആകെ")
    assert chat.documents[-1].startswith("EasyBill_report_")


def test_customer_gets_bill_with_start_link(chat, fake_wa):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    bill = store.get_bill("EB-0001")
    customer = type(chat)(user_id=2)
    customer.context.args = [f"EB-0001_{bill['share_key']}"]  # what the counter QR sends
    customer.run(__import__("bot").start(customer.update(), customer.context))
    assert customer.documents == ["EasyBill_EB-0001.pdf"]
    assert store.get_bill("EB-0001")["delivered_via"] == "telegram"
    customer.close()


def test_add_stock_by_text(chat, fake_gemini, fake_wa):
    fake_gemini.replies.append({"items": [{"name_en": "Brown Bread", "name_ml": "ബ്രൗൺ ബ്രെഡ്", "price": 50, "unit": "packet"}]})
    assert "Brown Bread" in chat.say("add items brown bread 50 packet")
    chat.press("confirm:items")
    assert store.INVENTORY_BY_ID["brown_bread"]["price"] == 50
    reply = chat.say("2 brown bread")  # offline parser must know the new item right away
    assert "Total: ₹100.00" in reply


# ---------------------------------------------------------------- only the shopkeeper

def test_customer_cannot_bill_report_or_change_prices(customer, fake_gemini, fake_wa):
    for text in ["2 kg rice", "sales report", "/add rice 1 kg", "7034191969", "ഇതൊന്ന് print ചെയ്യൂ"]:
        assert "Scan the QR at the shop counter" in customer.say(text)
    assert store.latest_bill() is None and store.INVENTORY_BY_ID["rice"]["price"] == 55
    assert fake_gemini.calls == []  # strangers don't use up the Gemini quota either


def test_customer_cannot_press_shopkeeper_buttons(chat, customer, fake_wa):
    chat.say("2 kg rice")
    customer.buttons = chat.buttons
    customer.press("confirm:bill")
    assert store.latest_bill() is None


def test_customer_cannot_fetch_a_bill_by_guessing_its_number(chat, customer, fake_wa):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    for arg in ["EB-0001", "EB-0001_wrongkey"]:
        customer.context.args = [arg]
        customer.run(__import__("bot").start(customer.update(), customer.context))
    assert customer.documents == []


def test_first_run_tells_the_owner_their_id(customer, monkeypatch):
    monkeypatch.setattr(store, "SHOPKEEPER_IDS", set())
    assert "SHOPKEEPER_IDS=2" in customer.say("2 kg rice")


def test_myid(customer):
    customer.run(__import__("bot").my_id(customer.update(), customer.context))
    assert customer.replies[-1] == "Your Telegram ID: 2"


def test_stale_countdowns_fail_on_restart(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 30)
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")
    assert store.get_bill("EB-0001")["send_status"] == "pending"
    assert store.fail_stale_sends() == ["EB-0001"]  # what the server does when it starts again
    assert store.get_bill("EB-0001")["send_status"] == "failed"
