"""Behaviour a released product must have. Each test describes the correct behaviour;
the ones that fail today are known bugs to fix before release."""
import math

from fastapi.testclient import TestClient

import main
import store


def test_api_rejects_nan_quantity():
    """A NaN quantity must be refused, not saved into bills.json (it breaks the counter screen)."""
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    response = client.post("/api/bills", content='{"items": [{"item_id": "rice", "qty": NaN}]}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code in (400, 422)
    latest = store.latest_bill()
    assert latest is None or not math.isnan(latest["total"])


def test_number_before_new_items_does_not_go_to_previous_customer(chat, fake_wa):
    """Bill A is done. For the next customer the shopkeeper says the number first:
    it must NOT overwrite bill A's customer (A's bill would go to customer B)."""
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")       # bill A for customer A
    chat.say("9847012345")                                             # customer B's number, said first
    assert store.get_bill("EB-0001")["customer_phone"] == "917034191969"


def test_second_bill_does_not_cancel_first_bills_whatsapp(chat, fake_wa, monkeypatch):
    """Two customers back to back: both must get their bill."""
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 0.3)
    chat.say("2 kg rice 7034191969"); chat.press("confirm:bill")       # A, counting down
    chat.say("1 soap 9847012345"); chat.press("confirm:bill")          # B, during A's countdown
    chat.wait(2.5)
    assert sorted(fake_wa.sent) == [("EB-0001", "917034191969"), ("EB-0002", "919847012345")]


def test_ok_on_an_old_draft_does_not_bill_the_new_draft(chat, fake_wa):
    """OK pressed under draft A must not bill draft B's items."""
    chat.say("2 kg rice")                       # draft A
    old_ok = chat.buttons[0]
    chat.say("1 soap")                          # draft B replaces it
    chat.press(old_ok)                          # shopkeeper taps OK on A's (older) message
    bill = store.latest_bill()
    assert bill is None or [line["item_id"] for line in bill["items"]] != ["soap"]


def test_updating_an_existing_item_price_does_not_duplicate_it(chat, fake_gemini, fake_wa):
    fake_gemini.replies.append({"items": [{"name_en": "Coconut Oil", "name_ml": "വെളിച്ചെണ്ണ", "price": 300, "unit": "litre"}]})
    count = len(store.INVENTORY)
    chat.say("add items coconut oil 300 litre")
    chat.press("confirm:items")
    assert len(store.INVENTORY) == count, "a second 'Coconut Oil' was added instead of updating the price"
    assert store.INVENTORY_BY_ID["coconut_oil"]["price"] == 300


def test_only_the_shopkeeper_can_change_prices(chat, fake_gemini, fake_wa, monkeypatch):
    """A customer who opened the bot from the counter QR must not be able to edit the price list."""
    monkeypatch.setattr(store, "SHOPKEEPER_IDS", {999}, raising=False)  # chat.update() uses user/chat id 1
    fake_gemini.replies.append({"items": [{"name_en": "Rice", "name_ml": "അരി", "price": 1, "unit": "kg"}]})
    chat.say("/add rice 1 kg")
    if chat.buttons == ["confirm:items", "discard:items"]:
        chat.press("confirm:items")
    assert store.INVENTORY_BY_ID["rice"]["price"] == 55
