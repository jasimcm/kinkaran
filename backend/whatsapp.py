"""WhatsApp bot using Meta's WhatsApp Cloud API (free test number).

Shopkeeper: sends a voice note (or types the items) to the shop's WhatsApp number
  -> Meta calls our webhook -> we download the audio -> Gemini transcribes and picks items
  -> prices from inventory.json -> bill saved, printed on the Mac, shown on the counter screen
  -> bot replies with the summary and the PDF.
Customer: scans the WhatsApp QR on the counter screen, which sends "BILL EB-0042"
  -> bot replies with the summary and the PDF.

Like Telegram, WhatsApp only lets a business reply to people who messaged it first
(these replies are free). With Meta's test number, every phone must also be added
to the allowed list in the Meta dashboard (max 5).
"""
import asyncio
import hashlib
import hmac
import logging
import re

import httpx

import parser as bill_parser
import pdf_gen
import printer
import store

log = logging.getLogger("easybill.whatsapp")

GRAPH_URL = f"https://graph.facebook.com/{store.WHATSAPP_API_VERSION}"
# "BILL EB-0042 <key>" from the counter QR; the key stops people fetching bills by guessing numbers
BILL_REQUEST = re.compile(r"\bBILL\s*(EB-\d+)(?:\s+([A-Za-z0-9_-]+))?", re.IGNORECASE)
CUSTOMER_HELP = (
    "നമസ്കാരം! 🙏 {shop}\n"
    "Customers: scan the WhatsApp QR at the counter to get your bill."
)

HELP = (
    "നമസ്കാരം! 🙏 {shop}\n\n"
    "🎙️ സാധനങ്ങൾ വോയ്സ് മെസേജ് ആയി അയക്കൂ, ഉദാ: \"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്\".\n"
    "Send the items as a voice note or text, e.g. \"2 kg rice, 1 soap\".\n\n"
    "Customers: scan the WhatsApp QR at the counter to get your bill."
)
NOT_UNDERSTOOD = (
    "ക്ഷമിക്കണം, സാധനങ്ങൾ മനസ്സിലായില്ല. വീണ്ടും പറയാമോ?\n"
    "Sorry, I couldn't find any items in that. Please try again.\n\nI heard: \"{heard}\""
)

_seen_message_ids = set()  # Meta can deliver the same webhook twice


def enabled():
    """The Cloud API bot needs the app secret too: without it anyone could forge webhook calls."""
    return bool(store.WHATSAPP_TOKEN and store.WHATSAPP_PHONE_NUMBER_ID and store.WHATSAPP_APP_SECRET)


def is_shopkeeper(sender):
    return sender in store.WHATSAPP_SHOPKEEPERS


def signature_ok(body, signature_header):
    """Check Meta's X-Hub-Signature-256 header. No app secret = nothing is trusted."""
    if not store.WHATSAPP_APP_SECRET:
        return False
    expected = "sha256=" + hmac.new(store.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


def auth_header():
    return {"Authorization": f"Bearer {store.WHATSAPP_TOKEN}"}


# ---------------------------------------------------------------- Graph API calls

async def download_media(media_id):
    """Returns (bytes, mime_type) of a voice note."""
    async with httpx.AsyncClient(timeout=30) as client:
        info = await client.get(f"{GRAPH_URL}/{media_id}", headers=auth_header())
        info.raise_for_status()
        media = await client.get(info.json()["url"], headers=auth_header())
        media.raise_for_status()
    return media.content, info.json().get("mime_type", "audio/ogg")


async def post_message(to, message):
    body = {"messaging_product": "whatsapp", "to": to, **message}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{GRAPH_URL}/{store.WHATSAPP_PHONE_NUMBER_ID}/messages", headers=auth_header(), json=body
        )
    if response.status_code >= 400:
        # Most common: the number is not in the test number's allowed list.
        log.error("Sending to %s failed: %s", to, response.text)
    response.raise_for_status()


async def send_text(to, text):
    await post_message(to, {"type": "text", "text": {"body": text, "preview_url": False}})


async def send_document(to, pdf, filename, caption):
    async with httpx.AsyncClient(timeout=30) as client:
        upload = await client.post(
            f"{GRAPH_URL}/{store.WHATSAPP_PHONE_NUMBER_ID}/media",
            headers=auth_header(),
            data={"messaging_product": "whatsapp", "type": "application/pdf"},
            files={"file": (filename, pdf, "application/pdf")},
        )
        upload.raise_for_status()
    await post_message(to, {
        "type": "document",
        "document": {"id": upload.json()["id"], "filename": filename, "caption": caption},
    })


async def send_pdf(to, bill):
    await send_document(
        to, pdf_gen.build_pdf(bill), f"EasyBill_{bill['bill_id']}.pdf", f"{store.SHOP_NAME} · {bill['bill_id']}"
    )


async def send_day_summary(to):
    """The day-end report as text plus a PDF when there is at least one bill."""
    summary = store.day_summary()
    await send_text(to, store.day_summary_text())
    if summary["bill_count"]:
        await send_document(
            to, pdf_gen.build_summary_pdf(summary),
            f"EasyBill_report_{summary['date']}.pdf", f"{store.SHOP_NAME} · {summary['date']}",
        )


# ---------------------------------------------------------------- Message handling

async def handle_webhook(payload):
    """Called in the background for every webhook POST. Status updates (sent/read) are ignored."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if message["id"] in _seen_message_ids:
                    continue
                _seen_message_ids.add(message["id"])
                try:
                    await handle_message(message)
                except Exception:
                    log.exception("Could not handle WhatsApp message from %s", message.get("from"))
                    await send_text(message["from"], "ക്ഷമിക്കണം, ഒരു പിശക് സംഭവിച്ചു. Sorry, something went wrong. Please try again.")


async def handle_message(message):
    sender = message["from"]
    kind = message.get("type")

    if kind == "text":
        text = message["text"]["body"]
        request = BILL_REQUEST.search(text)
        if request:
            await send_bill_to_customer(sender, request.group(1).upper(), request.group(2))
            return
        if not is_shopkeeper(sender):  # only the shop's own numbers can bill or see reports
            await send_text(sender, CUSTOMER_HELP.format(shop=store.SHOP_NAME))
            return
        if bill_parser.is_greeting(text):  # don't spend a Gemini request on "hi"
            await send_text(sender, HELP.format(shop=store.SHOP_NAME))
            return
        if bill_parser.is_summary(text):
            await send_day_summary(sender)
            return
        transcript = text
        text_without_phone, _ = bill_parser.split_phone(text)
        items, _, _ = await asyncio.to_thread(bill_parser.parse, text_without_phone)
    elif kind == "audio":
        if not is_shopkeeper(sender):
            await send_text(sender, CUSTOMER_HELP.format(shop=store.SHOP_NAME))
            return
        audio, mime_type = await download_media(message["audio"]["id"])
        transcript, items, _, _ = await asyncio.to_thread(bill_parser.parse_audio, audio, mime_type)
    else:
        await send_text(sender, HELP.format(shop=store.SHOP_NAME) if is_shopkeeper(sender) else CUSTOMER_HELP.format(shop=store.SHOP_NAME))
        return

    if not items:
        if bill_parser.is_summary(transcript):  # a voice note asking for the day report
            await send_day_summary(sender)
        else:
            await send_text(sender, NOT_UNDERSTOOD.format(heard=transcript))
        return

    lines, total = store.price_items(items)
    bill = store.create_bill(lines, total, channel="whatsapp", transcript=transcript)
    await asyncio.to_thread(printer.print_bill, bill)
    log.info("WhatsApp bill %s for ₹%.2f from %s", bill["bill_id"], total, sender)

    heard = "🎙️" if kind == "audio" else "📝"
    await send_text(sender, f"{heard} \"{transcript}\"\n\n{store.bill_summary(bill)}\n\n🖨️ Printed. Customer can scan the WhatsApp QR on the counter screen.")
    await send_pdf(sender, bill)


async def send_bill_to_customer(to, bill_id, key):
    bill = store.bill_for_customer(bill_id, key)
    if bill is None:
        await send_text(to, f"ക്ഷമിക്കണം, {bill_id} എന്ന ബിൽ കണ്ടെത്താനായില്ല.\nSorry, we could not find bill {bill_id}.")
        return
    await send_text(to, store.bill_summary(bill))
    await send_pdf(to, bill)
    store.mark_delivered(bill_id, "whatsapp")
    log.info("Sent %s to customer %s on WhatsApp", bill_id, to)
