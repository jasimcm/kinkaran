"""HTTP API (what the counter screen, mic page and tunnel expose)."""
import pytest
from fastapi.testclient import TestClient

import main
import store


@pytest.fixture
def client():
    return TestClient(main.app, client=("127.0.0.1", 50000))  # on this Mac; the bot is not started


def make_bill(client, items=None):
    response = client.post("/api/bills", json={"items": items or [{"item_id": "rice", "qty": 2}, {"item_id": "soap", "qty": 1}]})
    assert response.status_code == 200, response.text
    return response.json()


def test_screens_are_served(client):
    for path in ["/", "/counter.html", "/manifest.json", "/app.js", "/counter.js"]:
        assert client.get(path).status_code == 200, path


def test_inventory_and_config(client):
    assert len(client.get("/api/inventory").json()) == len(store.INVENTORY)
    config = client.get("/api/config").json()
    assert config["shop_name"] == "Test Stores" and config["bot_username"] == "TestBot"


def test_parse_offline(client):
    result = client.post("/api/parse", json={"transcript": "രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ"}).json()
    assert result["total"] == 178 and result["source"] == "local"


def test_parse_uses_gemini_when_available(client, fake_gemini):
    fake_gemini.replies.append({"items": [{"item_id": "rice", "qty": 2}], "unknown": ["1 kg chicken"]})
    result = client.post("/api/parse", json={"transcript": "2 kg rice, 1 kg chicken"}).json()
    assert result["source"] == "gemini" and result["total"] == 110 and result["unknown"] == ["1 kg chicken"]


def test_parse_falls_back_when_gemini_fails(client, fake_gemini):
    fake_gemini.replies.append(RuntimeError("quota exceeded"))
    result = client.post("/api/parse", json={"transcript": "2 kg rice"}).json()
    assert result["source"] == "local" and result["total"] == 110


def test_create_bill_prices_on_the_server(client):
    bill = make_bill(client)
    assert bill["bill_id"] == "EB-0001" and bill["total"] == 150
    assert bill["upi_link"].startswith("upi://pay?pa=test@upi") and "am=150.00" in bill["upi_link"]
    key = store.get_bill("EB-0001")["share_key"]
    assert bill["telegram_link"] == f"https://t.me/TestBot?start=EB-0001_{key}"
    assert "share_key" not in bill


def test_create_bill_rejects_bad_input(client):
    assert client.post("/api/bills", json={"items": [{"item_id": "gold", "qty": 1}]}).status_code == 400
    assert client.post("/api/bills", json={"items": []}).status_code == 400
    assert client.post("/api/bills", json={"items": [{"item_id": "rice", "qty": -2}]}).status_code == 422
    assert client.post("/api/bills", json={"items": [{"item_id": "rice", "qty": 1e9}]}).status_code == 422


def test_latest_and_status(client):
    assert client.get("/api/bills/latest").json() is None
    make_bill(client)
    assert client.get("/api/bills/latest").json()["bill_id"] == "EB-0001"
    status = client.get("/api/bills/EB-0001").json()
    assert status["delivered"] is False and "share_key" not in status
    assert client.get("/api/bills/EB-9999").status_code == 404


def test_pdf_and_private_share_link(client):
    make_bill(client)
    assert client.get("/api/bills/EB-0001/pdf").headers["content-type"] == "application/pdf"
    key = store.get_bill("EB-0001")["share_key"]
    assert client.get(f"/share/EB-0001/{key}").headers["content-type"] == "application/pdf"
    assert client.get("/share/EB-0001/wrong-key").status_code == 404


def test_print_again(client):
    make_bill(client)
    assert client.post("/api/bills/EB-0001/print").json()["mode"] == "saved"


def test_day_summary(client):
    make_bill(client)
    make_bill(client, [{"item_id": "milk", "qty": 1}])
    summary = client.get("/api/summary").json()
    assert summary["bill_count"] == 2 and summary["total"] == 206


def test_whatsapp_webhook_verification(client, monkeypatch):
    monkeypatch.setattr(store, "WHATSAPP_VERIFY_TOKEN", "secret-word")
    ok = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "secret-word", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_whatsapp_webhook_ignored_when_not_configured(client):
    """WhatsApp Cloud API is not connected: incoming webhooks are accepted and ignored."""
    response = client.post("/webhook/whatsapp", json={"entry": []})
    assert response.status_code == 200


# ---------------------------------------------------------------- through the public tunnel

TUNNEL = {"cf-connecting-ip": "203.0.113.9"}  # cloudflared adds this header


def test_tunnel_cannot_use_the_api_without_the_key(client):
    make_bill(client)
    for method, path in [("get", "/api/bills/latest"), ("get", "/api/bills/EB-0001"), ("get", "/api/bills/EB-0001/pdf"),
                         ("get", "/api/summary"), ("post", "/api/bills/EB-0001/print"), ("get", "/docs")]:
        assert getattr(client, method)(path, headers=TUNNEL).status_code == 403, path
    response = client.post("/api/bills", json={"items": [{"item_id": "rice", "qty": 1}]}, headers=TUNNEL)
    assert response.status_code == 403 and store.latest_bill()["bill_id"] == "EB-0001"


def test_tunnel_customer_pdf_link_still_works(client):
    make_bill(client)
    key = store.get_bill("EB-0001")["share_key"]
    assert client.get(f"/share/EB-0001/{key}", headers=TUNNEL).status_code == 200


def test_shop_phone_unlocks_the_api_with_the_key(client, monkeypatch):
    monkeypatch.setattr(store, "ACCESS_KEY", "s3cret-key")
    assert client.get("/?key=wrong", headers=TUNNEL).status_code == 403
    login = client.get("/?key=s3cret-key", headers=TUNNEL, follow_redirects=False)
    assert login.status_code == 303 and "easybill_key" in login.headers["set-cookie"]
    client.cookies.set("easybill_key", "s3cret-key")
    assert client.get("/api/summary", headers=TUNNEL).status_code == 200


def test_screens_load_through_the_tunnel(client):
    assert client.get("/counter.html", headers=TUNNEL).status_code == 200  # but their data needs the key
