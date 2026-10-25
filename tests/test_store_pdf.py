"""Prices, bills.json, the price list, day summary and PDFs."""
import json
from concurrent.futures import ThreadPoolExecutor

import pdf_gen
import printer
import store


def test_prices_always_come_from_the_price_list():
    lines, total = store.price_items([{"item_id": "rice", "qty": 2}, {"item_id": "milk", "qty": 0.5}])
    assert [(line["price"], line["line_total"]) for line in lines] == [(55, 110.0), (56, 28.0)]
    assert total == 138.0


def test_unknown_and_zero_items_are_dropped():
    lines, total = store.price_items([{"item_id": "gold", "qty": 1}, {"item_id": "rice", "qty": 0}])
    assert lines == [] and total == 0


def test_money_is_rounded_to_paise():
    lines, total = store.price_items([{"item_id": "green_chilli", "qty": 0.333}])
    assert lines[0]["line_total"] == round(0.333 * 80, 2)
    assert total == lines[0]["line_total"]


def test_bill_ids_count_up_and_survive_reload():
    first = store.create_bill(*store.price_items([{"item_id": "soap", "qty": 1}]))
    second = store.create_bill(*store.price_items([{"item_id": "salt", "qty": 1}]))
    assert (first["bill_id"], second["bill_id"]) == ("EB-0001", "EB-0002")
    assert store.get_bill("eb-0002")["total"] == 25
    assert store.latest_bill()["bill_id"] == "EB-0002"


def test_parallel_bills_get_unique_ids():
    def make(_):
        return store.create_bill(*store.price_items([{"item_id": "soap", "qty": 1}]))["bill_id"]
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(make, range(40)))
    assert len(set(ids)) == 40
    assert len(json.loads(store.BILLS_FILE.read_text())) == 40


def test_latest_bill_after_ten_thousand():
    """Bill ids are zero-padded to 4 digits; the 10000th must still be the 'latest'."""
    bills = {f"EB-{n:04d}": {"bill_id": f"EB-{n:04d}", "created_at": "2026-10-25T10:00:00"} for n in (9999, 10000)}
    store.BILLS_FILE.write_text(json.dumps(bills))
    assert store.latest_bill()["bill_id"] == "EB-10000"


def test_customer_phone_and_delivery():
    bill = store.create_bill(*store.price_items([{"item_id": "soap", "qty": 1}]), channel="telegram")
    store.set_customer_phone(bill["bill_id"], "917034191969")
    store.mark_delivered(bill["bill_id"], "whatsapp")
    saved = store.get_bill(bill["bill_id"])
    assert saved["customer_phone"] == "917034191969"
    assert saved["delivered"] and saved["delivered_via"] == "whatsapp"


def test_whatsapp_share_link_has_bill_and_private_pdf_link():
    bill = store.create_bill(*store.price_items([{"item_id": "soap", "qty": 1}]), customer_phone="917034191969")
    link = store.whatsapp_share_link(bill)
    assert link.startswith("https://wa.me/917034191969?text=")
    assert f"https%3A//test.example/share/{bill['bill_id']}/{bill['share_key']}" in link


def test_day_summary():
    store.create_bill(*store.price_items([{"item_id": "rice", "qty": 2}, {"item_id": "soap", "qty": 1}]))
    store.create_bill(*store.price_items([{"item_id": "rice", "qty": 1}]))
    summary = store.day_summary()
    assert summary["bill_count"] == 2 and summary["total"] == 205
    assert summary["items"][0] == {"name_en": "Rice (Matta)", "name_ml": "അരി", "unit": "kg", "qty": 3, "revenue": 165}
    assert "Bills: 2" in store.day_summary_text()


def test_day_summary_with_no_bills():
    assert store.day_summary()["bill_count"] == 0
    assert "No bills yet today" in store.day_summary_text()


def test_add_new_item_to_price_list():
    item, existed = store.add_item({"id": "bread-brown", "name_en": "Brown Bread", "name_ml": "ബ്രൗൺ ബ്രെഡ്", "unit": "packet", "price": 50})
    assert not existed and store.INVENTORY_BY_ID["bread-brown"]["price"] == 50
    saved = json.loads(store.INVENTORY_FILE.read_text(encoding="utf-8"))
    assert any(entry["id"] == "bread-brown" for entry in saved)
    lines, total = store.price_items([{"item_id": "bread-brown", "qty": 2}])
    assert total == 100


def test_re_adding_an_item_updates_its_price():
    _, existed = store.add_item({"id": "soap", "name_en": "Bath Soap", "name_ml": "സോപ്പ്", "unit": "piece", "price": 42})
    assert existed and store.INVENTORY_BY_ID["soap"]["price"] == 42


def test_old_bills_keep_their_price_after_a_price_change():
    bill = store.create_bill(*store.price_items([{"item_id": "soap", "qty": 1}]))
    store.add_item({"id": "soap", "name_en": "Bath Soap", "name_ml": "സോപ്പ്", "unit": "piece", "price": 99})
    assert store.get_bill(bill["bill_id"])["total"] == 40


# ---------------------------------------------------------------- PDFs and printing

def sample_bill(count=3):
    ids = ["rice", "soap", "milk", "coconut_oil", "detergent", "turmeric", "eggs", "bread"]
    return store.create_bill(*store.price_items([{"item_id": ids[i % len(ids)], "qty": 1 + i} for i in range(count)]))


def test_receipt_pdf():
    pdf = pdf_gen.build_pdf(sample_bill())
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_a4_pdf_is_a4():
    pdf = pdf_gen.build_a4_pdf(sample_bill())
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in pdf


def test_long_bill_still_makes_a_pdf():
    bill = sample_bill(60)
    assert pdf_gen.build_pdf(bill).startswith(b"%PDF")
    assert pdf_gen.build_a4_pdf(bill).startswith(b"%PDF")


def test_summary_pdf():
    sample_bill()
    assert pdf_gen.build_summary_pdf(store.day_summary()).startswith(b"%PDF")


def test_print_without_printer_saves_the_pdf(isolated_data):
    mode, _ = printer.print_bill(sample_bill())
    assert mode == "saved"
    assert (isolated_data / "printed" / "EasyBill_EB-0001.pdf").exists()


def test_print_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(store, "PRINTER", "No_Such_Printer_XYZ")
    mode, detail = printer.print_bill(sample_bill())
    assert mode == "failed" and detail
