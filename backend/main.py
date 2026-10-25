"""Easy Bill backend: FastAPI API + static PWA + Telegram bot + WhatsApp webhook in one process.

Run:  python backend/main.py   (then open http://localhost:8000)
"""
import json
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so "import store" works from anywhere

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import bot
import parser as bill_parser
import pdf_gen
import printer
import store
import whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its request logs contain the bot token
log = logging.getLogger("easybill")


@asynccontextmanager
async def lifespan(app):
    stale = store.fail_stale_sends()
    if stale:
        log.warning("WhatsApp countdowns lost in the restart, marked failed: %s", ", ".join(stale))
    telegram_app = None
    try:
        telegram_app = await bot.start_bot()
    except Exception as error:  # a bad token should not stop the web app
        log.error("Could not start Telegram bot: %s", error)
    yield
    await bot.stop_bot(telegram_app)


app = FastAPI(title="Easy Bill", lifespan=lifespan)


# ---------------------------------------------------------------- Who may use the API
#
# On this Mac (localhost) everything works without a key: the counter screen, scripts.
# Through the public tunnel, only customer links (/share/…) and Meta's webhook are open;
# the API needs ACCESS_KEY. Open https://<tunnel>/?key=<ACCESS_KEY> once on the shop phone
# and it remembers the key in a cookie.

PROTECTED = ("/api/", "/docs", "/redoc", "/openapi.json")
TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-real-ip")  # added by the tunnel
KEY_COOKIE = "easybill_key"


def is_local(request):
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and not any(header in request.headers for header in TUNNEL_HEADERS)


def has_key(request, key):
    return bool(store.ACCESS_KEY and key and secrets.compare_digest(key, store.ACCESS_KEY))


@app.middleware("http")
async def access_control(request: Request, call_next):
    key_in_url = request.query_params.get("key")
    if key_in_url is not None and not request.url.path.startswith(PROTECTED):
        if not has_key(request, key_in_url):
            return JSONResponse({"detail": "Wrong key"}, status_code=403)
        response = RedirectResponse(request.url.path, status_code=303)  # drop the key from the address bar
        response.set_cookie(KEY_COOKIE, key_in_url, max_age=365 * 86400, httponly=True,
                            samesite="strict", secure=not is_local(request))
        return response
    if (request.url.path.startswith(PROTECTED) and not is_local(request)
            and not has_key(request, request.cookies.get(KEY_COOKIE) or request.headers.get("x-access-key"))):
        return JSONResponse({"detail": "Not allowed from outside this Mac without the access key"}, status_code=403)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    """Say what was wrong without echoing the input back (a NaN can't even be sent as JSON)."""
    problems = [{"loc": list(problem["loc"]), "msg": problem["msg"]} for problem in error.errors()]
    return JSONResponse({"detail": problems}, status_code=422)


class ParseRequest(BaseModel):
    transcript: str = Field(max_length=2000)


class BillItem(BaseModel):
    item_id: str = Field(max_length=100)
    qty: float = Field(gt=0, le=10000, allow_inf_nan=False)  # NaN/inf would break bills.json


class BillRequest(BaseModel):
    items: List[BillItem] = Field(max_length=200)


@app.get("/api/inventory")
def inventory():
    return store.INVENTORY


@app.get("/api/config")
def config():
    return {
        "shop_name": store.SHOP_NAME,
        "telegram_enabled": bool(store.TELEGRAM_BOT_TOKEN and store.BOT_USERNAME),
        "bot_username": store.BOT_USERNAME,
        "gemini_enabled": bool(store.GEMINI_API_KEY),
        "whatsapp_enabled": whatsapp.enabled(),
        "whatsapp_number": store.WHATSAPP_NUMBER,
    }


@app.post("/api/parse")
def parse(request: ParseRequest):
    items, source, unknown = bill_parser.parse(request.transcript)
    lines, total = store.price_items(items)
    return {"items": lines, "total": total, "source": source, "unknown": unknown}


@app.post("/api/bills")
def create_bill(request: BillRequest):
    for entry in request.items:
        if entry.item_id not in store.INVENTORY_BY_ID:
            raise HTTPException(400, f"Unknown item: {entry.item_id}")
    lines, total = store.price_items([entry.model_dump() for entry in request.items])
    if not lines:
        raise HTTPException(400, "The bill has no items.")
    bill = store.create_bill(lines, total)
    printer.print_bill(bill)
    return with_links(bill)


def with_links(bill):
    """The bill plus the links the QR codes need (without the PDF share key)."""
    return {
        **{key: value for key, value in bill.items() if key != "share_key"},
        "upi_link": store.upi_link(bill),
        "telegram_link": store.telegram_link(bill),
        "whatsapp_link": store.whatsapp_link(bill),
    }


def find_bill(bill_id):
    bill = store.get_bill(bill_id)
    if bill is None:
        raise HTTPException(404, "Bill not found")
    return bill


@app.get("/api/bills/latest")
def latest_bill():
    """Used by the counter screen to show each new bill (web or WhatsApp). null if none yet."""
    bill = store.latest_bill()
    return with_links(bill) if bill else None


@app.get("/api/summary")
def day_summary():
    """Day-end report: today's bill count, revenue and per-item totals."""
    return store.day_summary()


@app.get("/api/bills/{bill_id}")
def bill_status(bill_id: str):
    return with_links(find_bill(bill_id))


@app.get("/share/{bill_id}/{share_key}")
def shared_pdf(bill_id: str, share_key: str):
    """The PDF link sent to customers on WhatsApp. Needs the bill's random key."""
    bill = store.get_bill(bill_id)
    if bill is None or not secrets.compare_digest(bill.get("share_key", ""), share_key):
        raise HTTPException(404, "Bill not found")
    return bill_pdf(bill_id)


@app.post("/api/bills/{bill_id}/print")
def print_again(bill_id: str):
    mode, detail = printer.print_bill(find_bill(bill_id))  # detail: CUPS job id, or the error
    return {"printed": mode in ("printed", "preview"), "mode": mode, "detail": detail}


@app.get("/api/bills/{bill_id}/pdf")
def bill_pdf(bill_id: str):
    bill = find_bill(bill_id)
    return Response(
        pdf_gen.build_pdf(bill),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="EasyBill_{bill["bill_id"]}.pdf"'},
    )


# ---------------------------------------------------------------- WhatsApp webhook

@app.get("/webhook/whatsapp")
def whatsapp_verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    """Meta calls this once when you click "Verify and save" in the dashboard."""
    if mode == "subscribe" and token == store.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "Verify token does not match WHATSAPP_VERIFY_TOKEN")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    """Meta sends every incoming message here. Reply 200 at once; the work runs in the background."""
    if not whatsapp.enabled():  # the WhatsApp Cloud API bot is not set up: ignore
        return {"ok": True}
    body = await request.body()
    if not whatsapp.signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Bad signature")
    background.add_task(whatsapp.handle_webhook, json.loads(body))
    return {"ok": True}


# The PWA (index.html, app.js, ...) is served from the same server, so one tunnel URL is enough.
app.mount("/", StaticFiles(directory=store.FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
