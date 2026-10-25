"""Client for whatsapp-sender/ (Node, runs on this Mac), which sends bills from a linked WhatsApp."""
import base64

import httpx

import pdf_gen
import store


class SendError(Exception):
    """The bill could not be sent; the message says why, for the shopkeeper."""


async def is_ready():
    """True if the sender is running and a WhatsApp account is linked."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{store.WA_SENDER_URL}/status")
        return response.json().get("connected", False)
    except (httpx.HTTPError, ValueError):
        return False


async def send_bill(bill):
    text = store.bill_summary(bill)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{store.WA_SENDER_URL}/send", json={
                "phone": bill["customer_phone"],
                "text": text,
                "pdf_base64": base64.b64encode(pdf_gen.build_pdf(bill)).decode(),
                "filename": f"EasyBill_{bill['bill_id']}.pdf",
            })
    except httpx.HTTPError:
        raise SendError("the WhatsApp sender is not running")
    if response.status_code != 200:
        try:
            reason = response.json().get("error")
        except ValueError:  # not JSON (e.g. a proxy error page)
            reason = None
        raise SendError(reason or f"error {response.status_code}")
