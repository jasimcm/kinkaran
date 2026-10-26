"""Telegram bot (python-telegram-bot v21), run with long polling inside the FastAPI process.
Long polling means the Mac asks Telegram for new messages: no webhook or tunnel needed.

Shopkeeper: sends a voice note (or types the items) to the bot
  -> Gemini transcribes and picks items -> prices from inventory.json
  -> bill saved, printed on the Mac, shown on the counter screen -> bot replies with summary + PDF.
Customer: scans https://t.me/<bot>?start=<BillID>_<key> on the counter screen and taps Start
  -> Telegram sends "/start EB-0042_<key>" -> bot replies with summary + PDF.
Bots cannot message people who never started a chat, which is why we use this deep link.
The key is the bill's random share key, so nobody can fetch other bills by guessing numbers.

Only Telegram users listed in SHOPKEEPER_IDS can bill, print, see reports or change prices.
Everyone else can only open their own bill with the link above (and /myid shows their id).

If the shopkeeper says a phone number and whatsapp-sender/ is linked, the bill is sent to that
WhatsApp automatically after a countdown (AUTO_SEND_SECONDS) unless the shopkeeper cancels.
Otherwise the bot gives a "Send on WhatsApp" button that opens the shopkeeper's WhatsApp.
"""
import asyncio
import functools
import logging
import math
import re
import time
from datetime import datetime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

import parser as bill_parser
import pdf_gen
import printer
import store
import wa_sender

log = logging.getLogger("easybill.bot")

WELCOME = (
    "നമസ്കാരം! 🙏 ഞാൻ കിങ്കരൻ, {shop}.\n"
    "മുതലാളി പറഞ്ഞാ മതി, ബാക്കി കിങ്കരൻ നോക്കിക്കോളും 💪\n\n"
    "🧾 Customers: scan the Telegram QR at the shop counter to get your bill here.\n"
    "കടയിലെ QR കോഡ് സ്കാൻ ചെയ്താൽ നിങ്ങളുടെ ബിൽ ഇവിടെ ലഭിക്കും.\n\n"
    "🎙️ Shopkeeper: send the items as a voice note or text, e.g. \"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്\".\n"
    "📊 \"ഇന്നത്തെ ആകെ\" / \"sales report\" → today's bills, total and best sellers."
)
TRY_AGAIN = (
    "ക്ഷമിക്കണം, ഒരു പിശക് സംഭവിച്ചു. ഒന്നുകൂടി അയക്കാമോ?\n"
    "Sorry, something went wrong (slow network?). Please send it again."
)
BAD_NUMBER = (
    "📲 I heard \"{heard}\", which is not a 10-digit mobile number.\n"
    "നമ്പർ വ്യക്തമായില്ല. ദയവായി നമ്പർ ടൈപ്പ് ചെയ്യൂ. Please type the number."
)
NOT_UNDERSTOOD = (
    "ക്ഷമിക്കണം, സാധനങ്ങൾ മനസ്സിലായില്ല. വീണ്ടും പറയാമോ?\n"
    "Sorry, I couldn't find any items in that. Please try again.\n\nI heard: \"{heard}\""
)
CUSTOMER_WELCOME = (
    "നമസ്കാരം! 🙏 This is the billing assistant of {shop}.\n"
    "കടയിലെ QR കോഡ് സ്കാൻ ചെയ്താൽ നിങ്ങളുടെ ബിൽ ഇവിടെ ലഭിക്കും.\n"
    "Scan the QR at the shop counter to get your bill here."
)
NOT_SET_UP = (
    "⚙️ Shopkeeper setup: no shopkeeper is set yet.\n"
    "Add this line to .env and restart:\nSHOPKEEPER_IDS={user_id}"
)


def is_shopkeeper(update):
    user = update.effective_user
    return user is not None and user.id in store.SHOPKEEPER_IDS


def shopkeeper_only(handler):
    """Billing, printing, reports and price changes are for the shopkeeper only.
    Customers reach this same bot through the counter QR, so everyone else is turned away."""
    @functools.wraps(handler)
    async def guarded(update, context):
        if is_shopkeeper(update):
            return await handler(update, context)
        user_id = update.effective_user.id if update.effective_user else "?"
        log.warning("Ignored a shopkeeper action from Telegram user %s", user_id)
        if update.callback_query is not None:
            await update.callback_query.answer("Only the shopkeeper can do this.")
            return
        if store.SHOPKEEPER_IDS:
            await update.effective_message.reply_text(CUSTOMER_WELCOME.format(shop=store.SHOP_NAME))
        else:  # first run: tell the owner how to claim the bot
            await update.effective_message.reply_text(NOT_SET_UP.format(user_id=user_id))
    return guarded


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myid: shows your Telegram id, to put in SHOPKEEPER_IDS."""
    await update.effective_message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def send_bill(message, bill):
    await message.reply_text(store.bill_summary(bill))
    await message.reply_document(
        document=pdf_gen.build_pdf(bill),
        filename=f"EasyBill_{bill['bill_id']}.pdf",
        caption=f"{store.SHOP_NAME} · {bill['bill_id']}",
    )


async def send_day_summary(message):
    """The day-end report as text plus a PDF when there is at least one bill."""
    summary = store.day_summary()
    await message.reply_text(store.day_summary_text())
    if summary["bill_count"]:
        await message.reply_document(
            document=pdf_gen.build_summary_pdf(summary),
            filename=f"EasyBill_report_{summary['date']}.pdf",
            caption=f"📊 {store.SHOP_NAME} · {summary['date']}",
        )


# ---------------------------------------------------------------- Customer

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        if is_shopkeeper(update):
            await update.message.reply_text(WELCOME.format(shop=store.SHOP_NAME))
        elif store.SHOPKEEPER_IDS:
            await update.message.reply_text(CUSTOMER_WELCOME.format(shop=store.SHOP_NAME))
        else:
            await update.message.reply_text(NOT_SET_UP.format(user_id=update.effective_user.id))
        return

    bill_id, _, key = context.args[0].partition("_")  # "EB-0042_<key>" from the counter QR
    bill_id = bill_id.upper()
    bill = store.bill_for_customer(bill_id, key)
    if bill is None:
        await update.message.reply_text(
            f"ക്ഷമിക്കണം, {bill_id} എന്ന ബിൽ കണ്ടെത്താനായില്ല.\n"
            f"Sorry, we could not find bill {bill_id}. Please ask the shopkeeper to show the QR again."
        )
        return

    await send_bill(update.message, bill)
    store.mark_delivered(bill_id, "telegram")
    log.info("Sent %s to chat %s", bill_id, update.effective_chat.id)


# ---------------------------------------------------------------- Shopkeeper

STALE_BUTTON = "This message is old or already used. Use the newest message. പുതിയ മെസേജ് ഉപയോഗിക്കൂ."

NEXT_PHONE_SECONDS = 300  # a number said before the items is kept this long for the next draft


def remember_next_phone(context, phone):
    context.chat_data["next_phone"] = {"phone": phone, "at": time.time()}


def take_next_phone(context):
    """The number noted for the next customer, if it is still fresh. It is removed either way:
    one noted number belongs to exactly one bill, so it can't drift onto a later customer."""
    noted = context.chat_data.pop("next_phone", None)
    if noted and time.time() - noted["at"] <= NEXT_PHONE_SECONDS:
        return noted["phone"]
    return None


def new_draft_id(context):
    """Every draft gets a number, put in its buttons, so OK on an old message can't act on a newer one."""
    context.chat_data["draft_seq"] = context.chat_data.get("draft_seq", 0) + 1
    return context.chat_data["draft_seq"]


def take_pending(context, key, query):
    """Pop chat_data[key] only if the pressed button ("confirm:bill:7") belongs to it."""
    pending = context.chat_data.get(key)
    parts = query.data.split(":")
    if pending is None or len(parts) < 3 or str(pending["id"]) != parts[2]:
        return None
    return context.chat_data.pop(key)


def items_preview(items):
    """What the photo/voice note was read as, before anything is added to the price list."""
    rows = ["📋 Found these items — check them:", ""]
    for item in items:
        old = store.INVENTORY_BY_ID.get(item["id"])
        tag = f" (was ₹{old['price']:g})" if old else " 🆕"
        rows.append(f"• {item['name_en']} / {item['name_ml']} — ₹{item['price']:g} per {item['unit']}{tag}")
    rows += ["", "⚠️ These become your SELLING prices. From a supplier bill, check you aren't using the cost price."]
    return "\n".join(rows)


async def offer_items(message, context, items):
    """Preview extracted items and let the shopkeeper confirm before they hit inventory."""
    draft_id = new_draft_id(context)
    context.chat_data["pending_items"] = {"id": draft_id, "items": items}
    await message.reply_text(
        items_preview(items) + "\n\nശരിയാണോ? Add them to the price list?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Add all", callback_data=f"confirm:items:{draft_id}"),
            InlineKeyboardButton("✋ Cancel", callback_data=f"discard:items:{draft_id}"),
        ]]),
    )


@shopkeeper_only
async def confirm_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pending = take_pending(context, "pending_items", query)
    await query.answer()
    if pending is None:
        await query.edit_message_text(STALE_BUTTON)
        return
    added, updated = [], []
    for entry in pending["items"]:
        item, existed = store.add_item(entry)
        (updated if existed else added).append(item["name_en"])
    # the offline parser must see the new names right away, before the next bill
    bill_parser.ALIASES = bill_parser.build_alias_table()
    bill_parser.MAX_ALIAS_WORDS = max(len(a.split()) for a in bill_parser.ALIASES)
    done = ", ".join(added + [f"{name} (price updated)" for name in updated])
    log.info("Price list updated: %s", done)
    await query.edit_message_text(f"✅ Price list updated: {done}\nYou can bill them right away.")


@shopkeeper_only
async def discard_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if take_pending(context, "pending_items", query) is None:
        await query.answer()
        await query.edit_message_text(STALE_BUTTON)
        return
    await query.answer("Cancelled")
    await query.edit_message_text("✋ Cancelled. Nothing was added.")


@shopkeeper_only
async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo of a purchase bill / price list -> extract items -> confirm -> inventory."""
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        file = await update.message.photo[-1].get_file()  # largest size
        data = bytes(await file.download_as_bytearray())
        items = await asyncio.to_thread(bill_parser.parse_photo, data, "image/jpeg")
    except Exception as error:
        log.error("Bill photo failed: %s", error)
        await update.message.reply_text(TRY_AGAIN)
        return
    if not items:
        await update.message.reply_text(
            "ഫോട്ടോയിൽ സാധനങ്ങളും വിലയും കണ്ടെത്താനായില്ല.\n"
            "Couldn't read items and prices from that photo. Try a clearer one."
        )
        return
    await offer_items(update.message, context, items)


async def add_items_text(update, context, text):
    """Typed "add items: bread 45 piece, soap 40" — or /add with the same text."""
    if not store.GEMINI_API_KEY:
        await update.message.reply_text("Adding items needs GEMINI_API_KEY (to read the list).")
        return
    try:
        items = await asyncio.to_thread(bill_parser.parse_items_text, text)
    except Exception as error:
        log.error("Item list parse failed: %s", error)
        await update.message.reply_text(TRY_AGAIN)
        return
    if not items:
        await update.message.reply_text(
            "സാധനങ്ങളും വിലയും മനസ്സിലായില്ല.\n"
            "I need items with prices, e.g. \"add items bread 45 piece, soap 40\" — or send a photo of the bill."
        )
        return
    await offer_items(update.message, context, items)


@shopkeeper_only
async def add_items_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_items_text(update, context, " ".join(context.args))


@shopkeeper_only
async def voice_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    audio = update.message.voice or update.message.audio
    await update.message.reply_text("🎙️ കേൾക്കുന്നു… Listening…")
    await update.message.chat.send_action(ChatAction.TYPING)
    data = await download_voice(audio)
    try:
        # Gemini is a slow, blocking call: run it in a thread so the web app keeps responding.
        transcript, items, phone, unknown = await asyncio.to_thread(
            bill_parser.parse_audio, data, audio.mime_type or "audio/ogg"
        )
    except Exception as error:
        log.error("Voice note failed: %s", error)
        await update.message.reply_text(
            "ക്ഷമിക്കണം, വോയ്സ് മനസ്സിലാക്കാനായില്ല. ടൈപ്പ് ചെയ്ത് അയക്കാമോ?\n"
            "Sorry, I couldn't process the voice note. Please type the items instead."
        )
        return
    if not items and not phone and bill_parser.is_cancel(transcript):
        await cancel_by_message(update, context)
        return
    if not items and not phone and bill_parser.is_confirm(transcript):
        await confirm_by_message(update, context)
        return
    if not items and not phone and bill_parser.is_summary(transcript):
        await send_day_summary(update.message)
        return
    if not items and not phone and bill_parser.is_add_items(transcript):
        try:  # re-listen to the same audio with the stock-list prompt
            new_items = await asyncio.to_thread(
                bill_parser.parse_items_audio, data, audio.mime_type or "audio/ogg"
            )
        except Exception as error:
            log.error("Stock voice note failed: %s", error)
            await update.message.reply_text(TRY_AGAIN)
            return
        if new_items:
            await offer_items(update.message, context, new_items)
        else:
            await update.message.reply_text(
                "സാധനങ്ങളും വിലയും മനസ്സിലായില്ല.\n"
                "Say the items with prices, e.g. \"bread 45 rupees, soap 40\" — or send a photo of the bill."
            )
        return
    await make_bill(update, context, transcript, items, phone, unknown, "🎙️")


async def download_voice(audio, attempts=2):
    """Download a voice note from Telegram, trying once more if the network is slow."""
    for attempt in range(1, attempts + 1):
        try:
            file = await audio.get_file()
            return bytes(await file.download_as_bytearray())
        except NetworkError as error:  # TimedOut is a NetworkError too
            log.warning("Voice note download failed (attempt %d): %s", attempt, error)
            if attempt == attempts:
                raise


@shopkeeper_only
async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if bill_parser.is_greeting(text):  # don't spend a Gemini request on "hi"
        await update.message.reply_text(WELCOME.format(shop=store.SHOP_NAME))
        return
    if bill_parser.is_cancel(text):
        await cancel_by_message(update, context)
        return
    if bill_parser.is_confirm(text):  # "ശരി" / "ok" confirms the open draft
        await confirm_by_message(update, context)
        return
    if bill_parser.is_summary(text):  # "ഇന്നത്തെ ആകെ", "sales report": no Gemini needed
        await send_day_summary(update.message)
        return
    if bill_parser.is_add_items(text):  # "add items bread 45 piece" / "പുതിയ സാധനം ചേർക്കൂ"
        await add_items_text(update, context, text)
        return
    text_without_phone, phone = bill_parser.split_phone(text)
    if re.search(r"[^\W\d_]", text_without_phone):  # has letters, so maybe items
        items, _, unknown = await asyncio.to_thread(bill_parser.parse, text_without_phone)
    else:  # only a (phone) number: no need to ask Gemini
        items, unknown = [], []
    await make_bill(update, context, text, items, phone, unknown, "📝")


async def make_bill(update, context, transcript, items, phone, unknown, icon):
    not_in_list = ""
    if unknown:
        not_in_list = "\n\n⚠️ വിലവിവരപ്പട്ടികയിൽ ഇല്ല / Not in price list (not billed):\n• " + "\n• ".join(unknown)
    if not items:
        digits = re.sub(r"\D", "", transcript)
        bill_id = bill_parser.bill_id_in(transcript)
        if phone and bill_id is None and context.chat_data.get("pending_draft"):  # number for the open draft
            context.chat_data["pending_draft"]["customer_phone"] = phone
            await update.message.reply_text(
                f"📲 Noted {store.format_phone(phone)} for this bill. Tap ✅ OK to make it."
            )
        elif phone:  # a number: it belongs to the named bill, else this chat's last bill
            await attach_phone(update, context, phone, bill_id)
        elif bill_parser.wants_print(transcript):  # "ഇതൊന്ന് print ചെയ്യൂ" / "print EB-0013"
            await print_again(update, context, bill_id)
        elif bill_id or bill_parser.wants_whatsapp(transcript):  # "send EB-0042 on WhatsApp"
            await resend_bill(update, context, bill_id)
        elif len(digits) >= 6:  # sounded like a number, but not a valid 10-digit mobile
            await update.message.reply_text(BAD_NUMBER.format(heard=digits))
        else:
            await update.message.reply_text(NOT_UNDERSTOOD.format(heard=transcript) + not_in_list)
        return
    lines, total = store.price_items(items)
    noted = take_next_phone(context)  # always used up by this draft, even if it has its own number
    phone = phone or noted
    draft_id = new_draft_id(context)
    context.chat_data["pending_draft"] = {  # nothing is billed until ✅ OK is tapped
        "id": draft_id, "lines": lines, "total": total, "transcript": transcript, "customer_phone": phone,
    }
    log.info("Draft for chat %s: %d items, ₹%.2f", update.effective_chat.id, len(lines), total)
    for_customer = f"\n📲 For {store.format_phone(phone)}" if phone else ""
    await update.message.reply_text(
        f"{icon} \"{transcript}\"\n\n{draft_text(lines, total)}{for_customer}{not_in_list}\n\n"
        "ശരിയാണോ? Correct? Tap OK to make the bill.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ OK — bill it", callback_data=f"confirm:bill:{draft_id}"),
            InlineKeyboardButton("✋ Cancel", callback_data=f"discard:bill:{draft_id}"),
        ]]),
    )


def draft_text(lines, total):
    """The preview the shopkeeper checks before the bill exists."""
    rows = [f"🧾 {store.SHOP_NAME} — check the items:", ""]
    for item in lines:
        unit = store.UNIT_LABEL.get(item["unit"], item["unit"])
        rows.append(f"• {item['name_en']} × {item['qty']:g} {unit} = ₹{item['line_total']:.2f}")
    rows.append(f"\nTotal: ₹{total:.2f}")
    return "\n".join(rows)


async def finish_bill(message, context, draft):
    """The draft was confirmed: now create, print, send the PDF and offer delivery."""
    bill = store.create_bill(
        draft["lines"], draft["total"], channel="telegram",
        transcript=draft["transcript"], customer_phone=draft["customer_phone"],
    )
    context.chat_data["last_bill_id"] = bill["bill_id"]
    await asyncio.to_thread(printer.print_bill, bill)
    log.info("Telegram bill %s for ₹%.2f (customer %s)", bill["bill_id"], bill["total"], bill["customer_phone"] or "-")
    await message.reply_text(store.bill_summary(bill))
    await message.reply_document(
        document=pdf_gen.build_pdf(bill),
        filename=f"EasyBill_{bill['bill_id']}.pdf",
        caption=f"🖨️ Printed · {bill['bill_id']}",
    )
    await deliver_to_customer(message, context, bill)


@shopkeeper_only
async def confirm_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = take_pending(context, "pending_draft", query)
    await query.answer()
    if draft is None:  # already billed, or a newer draft replaced this one
        await query.edit_message_text(STALE_BUTTON)
        return
    await query.edit_message_text(query.message.text + "\n\n✅ Confirmed")
    await finish_bill(query.message, context, draft)


@shopkeeper_only
async def discard_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if take_pending(context, "pending_draft", query) is None:
        await query.answer()
        await query.edit_message_text(STALE_BUTTON)
        return
    await query.answer("Cancelled")
    await query.edit_message_text("✋ Discarded. ബിൽ ഉണ്ടായില്ല.\nSend the items again.")


async def confirm_by_message(update, context):
    """The shopkeeper typed or said "ശരി" / "ok" instead of tapping the button."""
    draft = context.chat_data.pop("pending_draft", None)
    if draft is None:
        await update.message.reply_text("Nothing to confirm. ഒന്നും കാത്തിരിക്കുന്നില്ല.")
        return
    await finish_bill(update.message, context, draft)


ASK_WINDOW_SECONDS = 600  # a bill older than this is never a candidate for a lone number


async def attach_phone(update, context, phone, bill_id=None):
    """A message with only a phone number, and no draft open. It goes to:
    - the bill named in the message ("EB-0042 98470 12345"), or
    - the last bill while its WhatsApp countdown runs (correcting a misheard number), or
    - if the last bill is recent and has no customer yet: ASK whether the number is for that
      bill or for the next customer, or else
    - the next bill (noted for 5 minutes).
    It never guesses: a wrong guess sends one customer's bill to another."""
    if bill_id is None:
        last_id = context.chat_data.get("last_bill_id")
        if last_id is None:  # e.g. after a server restart: use the newest Telegram bill
            latest = store.latest_bill()
            last_id = latest["bill_id"] if latest and latest["channel"] == "telegram" else None
        last = store.get_bill(last_id) if last_id else None
        if last is not None and last_id in PENDING:
            bill_id = last_id
        elif last is not None and not last.get("customer_phone") and is_recent(last):
            await ask_which_bill(update.message, last, phone)
            return
        else:
            await note_for_next_bill(update.message, context, phone)
            return
    if store.get_bill(bill_id) is None:
        await update.message.reply_text(f"Couldn't find bill {bill_id}. {bill_id} കണ്ടെത്താനായില്ല.")
        return
    bill = store.set_customer_phone(bill_id, phone)
    context.chat_data["last_bill_id"] = bill_id
    log.info("Customer %s added to %s", phone, bill_id)
    await deliver_to_customer(update.message, context, bill)


def is_recent(bill):
    age = time.time() - datetime.fromisoformat(bill["created_at"]).timestamp()
    return age <= ASK_WINDOW_SECONDS


async def note_for_next_bill(message, context, phone):
    remember_next_phone(context, phone)
    await message.reply_text(
        f"📲 {store.format_phone(phone)}: noted for the next bill. ഇനി സാധനങ്ങൾ പറയൂ.\n"
        f"(Send the items within {NEXT_PHONE_SECONDS // 60} minutes.)"
    )


async def ask_which_bill(message, bill, phone):
    """The number could be for the bill just made (customer told it late) or for the next customer."""
    await message.reply_text(
        f"📲 {store.format_phone(phone)} — ഏത് ബില്ലിന്? Which bill is this number for?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{bill['bill_id']} (₹{bill['total']:.2f})", callback_data=f"phone:{bill['bill_id']}:{phone}"),
            InlineKeyboardButton("🆕 Next customer", callback_data=f"phone:next:{phone}"),
        ]]),
    )


@shopkeeper_only
async def phone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The answer to ask_which_bill."""
    query = update.callback_query
    _, target, phone = query.data.split(":")
    await query.answer()
    if target == "next":
        await query.edit_message_text(f"📲 {store.format_phone(phone)} → next bill")
        await note_for_next_bill(query.message, context, phone)
        return
    bill = store.get_bill(target)
    if bill is None or bill.get("customer_phone"):  # meanwhile it got a number: don't overwrite
        await query.edit_message_text(STALE_BUTTON)
        return
    bill = store.set_customer_phone(target, phone)
    context.chat_data["last_bill_id"] = target
    log.info("Customer %s added to %s", phone, target)
    await query.edit_message_text(f"📲 {store.format_phone(phone)} → {target}")
    await deliver_to_customer(query.message, context, bill)


async def resend_bill(update, context, bill_id=None):
    """"Send it on WhatsApp" with no new number: resend the named/last bill to its saved
    customer, or ask which number if the bill has none."""
    if bill_id is None:
        bill_id = context.chat_data.get("last_bill_id")
    if bill_id is not None:
        bill = store.get_bill(bill_id)
    else:
        latest = store.latest_bill()
        bill = latest if latest and latest["channel"] == "telegram" else None
    if bill is None:
        await update.message.reply_text("Couldn't find that bill. ആ ബിൽ കണ്ടെത്താനായില്ല.")
        return
    if not bill.get("customer_phone"):
        context.chat_data["last_bill_id"] = bill["bill_id"]
        await update.message.reply_text(
            f"{bill['bill_id']}: ആരുടെ നമ്പറിലേക്ക്? Which number?\n"
            'Say or type it, e.g. "98470 12345".'
        )
        return
    await deliver_to_customer(update.message, context, bill)


async def deliver_to_customer(message, context, bill):
    """Auto-send on WhatsApp if a number was given and the sender is linked; otherwise offer links."""
    note = None
    if bill.get("customer_phone"):
        paused = auto_send_paused()
        if not paused and await wa_sender.is_ready():
            await start_countdown(message, context, bill)
            return
        if paused:
            note = f"⏸️ Auto-send paused: {paused}. This protects the WhatsApp number from a ban."
    await send_customer_options(message, bill, note)


# ---------------------------------------------------------------- WhatsApp auto-send with countdown

PENDING = {}  # bill_id -> (countdown task, the Telegram status message)
_last_send_started = 0.0  # time.time() of the latest WhatsApp send, for the minimum gap


def auto_send_paused():
    """Why auto-send must not run now (switch off or a limit reached), or None if it may."""
    if not store.AUTO_SEND:
        return "AUTO_SEND is off in .env"
    now, times = time.time(), store.whatsapp_send_times()
    if sum(sent > now - 3600 for sent in times) >= store.WA_MAX_PER_HOUR:
        return f"the limit of {store.WA_MAX_PER_HOUR} sends per hour is reached"
    if sum(sent > now - 86400 for sent in times) >= store.WA_MAX_PER_DAY:
        return f"the limit of {store.WA_MAX_PER_DAY} sends per day is reached"
    return None


def seconds_until_allowed():
    """Keep at least WA_MIN_GAP_SECONDS between two WhatsApp sends (+1 s: saved send times are
    whole seconds). Counts sends saved in bills.json and the one just started in this process."""
    if store.WA_MIN_GAP_SECONDS <= 0:
        return 0
    times = store.whatsapp_send_times()
    last = max(times[-1] if times else 0, _last_send_started)
    return max(0, math.ceil(store.WA_MIN_GAP_SECONDS + 1 - (time.time() - last))) if last else 0


async def start_countdown(message, context, bill):
    bill_id = bill["bill_id"]
    old_status = cancel_send(bill_id)  # a corrected number for THIS bill replaces its countdown
    if old_status:                     # (other bills' countdowns keep running)
        await old_status.edit_text("↪️ Number changed: not sent to this one. See below.")
    seconds = max(store.AUTO_SEND_SECONDS, seconds_until_allowed())
    phone = store.format_phone(bill["customer_phone"])
    status = await message.reply_text(
        f"📲 {bill_id} → {phone}\n"
        f"⏳ Sending on WhatsApp in {seconds} seconds…\n\n"
        "നമ്പർ തെറ്റാണെങ്കിൽ Cancel അമർത്തുക, അല്ലെങ്കിൽ \"വേണ്ട\" എന്ന് പറയുക.\n"
        "Wrong number? Tap Cancel or say \"cancel\". To fix it, send the right number.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✋ Cancel", callback_data=f"cancel:{bill_id}")]]),
    )
    store.set_send_status(bill_id, "pending", time.time() + seconds)
    context.chat_data["pending_send"] = bill_id
    PENDING[bill_id] = (asyncio.create_task(send_after(bill_id, seconds, status)), status)


async def send_after(bill_id, seconds, status):
    global _last_send_started
    await asyncio.sleep(seconds)  # cancel_send() stops the task here
    while seconds_until_allowed():  # two countdowns ending together still send a gap apart
        await asyncio.sleep(seconds_until_allowed())
    _last_send_started = time.time()  # no await between the check and this line
    PENDING.pop(bill_id, None)
    try:
        await send_now(bill_id, status)
    except Exception as error:  # a background task has no error handler: log it, don't lose it
        log.exception("Auto-send of %s failed", bill_id)
        store.set_send_status(bill_id, "failed")
        try:
            await status.edit_text(f"❌ {bill_id} not sent on WhatsApp: {error}")
        except Exception:
            pass


async def send_now(bill_id, status):
    bill = store.get_bill(bill_id)
    phone = store.format_phone(bill["customer_phone"])
    try:
        await wa_sender.send_bill(bill)
    except wa_sender.SendError as error:
        store.set_send_status(bill_id, "failed")
        log.warning("WhatsApp send of %s failed: %s", bill_id, error)
        fallback = store.whatsapp_share_link(bill)
        await status.edit_text(
            f"❌ {bill_id} not sent to {phone}: {error}.\nTap below to send it from your own WhatsApp.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📲 Send on WhatsApp", url=fallback)]]),
        )
        return
    store.set_send_status(bill_id, "sent")
    store.mark_delivered(bill_id, "whatsapp")
    log.info("Sent %s to %s on WhatsApp", bill_id, bill["customer_phone"])
    await status.edit_text(f"✅ {bill_id} sent to {phone} on WhatsApp\nബിൽ വാട്സാപ്പിൽ അയച്ചു")


def cancel_send(bill_id):
    """Stop a countdown. Returns its Telegram status message, or None if nothing was pending."""
    pending = PENDING.pop(bill_id, None)
    if pending is None:
        return None
    task, status = pending
    task.cancel()
    store.set_send_status(bill_id, "cancelled")
    log.info("Cancelled WhatsApp send of %s", bill_id)
    return status


CANCELLED = "✋ {bill_id} not sent. റദ്ദാക്കി.\nSend the right number to send it again."


@shopkeeper_only
async def cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bill_id = query.data.split(":", 1)[1]
    status = cancel_send(bill_id)
    await query.answer("Cancelled" if status else "Already sent")
    if status:
        await query.edit_message_text(CANCELLED.format(bill_id=bill_id))


@shopkeeper_only
async def day_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/summary or /report: today's bills, total and best sellers."""
    await send_day_summary(update.message)


async def cancel_by_message(update, context):
    """The shopkeeper typed or said "cancel" / "വേണ്ട". Stops EVERY WhatsApp countdown (safer than
    guessing which one was meant), else discards the open draft; also forgets a noted number."""
    stopped = []
    for bill_id in list(PENDING):
        status = cancel_send(bill_id)
        if status:
            stopped.append(bill_id)
            await status.edit_text(CANCELLED.format(bill_id=bill_id))
    forgot = context.chat_data.pop("next_phone", None) is not None
    if stopped:
        await update.message.reply_text(f"✋ Stopped: {', '.join(stopped)}. Nothing was sent.")
    elif context.chat_data.pop("pending_draft", None) is not None:
        await update.message.reply_text("✋ Discarded. ബിൽ ഉണ്ടായില്ല.")
    elif forgot:
        await update.message.reply_text("✋ Forgot the noted number.")
    else:
        await update.message.reply_text("Nothing to cancel. ഒന്നും അയക്കാനില്ല.")


async def print_again(update, context, bill_id=None):
    """"ഇതൊന്ന് print ചെയ്യൂ" — print the named bill, else this chat's last (or newest) bill."""
    if bill_id is None:
        bill_id = context.chat_data.get("last_bill_id")
    bill = store.get_bill(bill_id) if bill_id is not None else store.latest_bill()
    if bill is None:
        await update.message.reply_text("പ്രിന്റ് ചെയ്യാന്‍ ഒരു ബില്ലുമില്ല. No bill to print yet.")
        return
    mode, detail = await asyncio.to_thread(printer.print_bill, bill)
    reply = {
        "printed": f"🖨️ {bill['bill_id']} sent to {store.PRINTER} (job {detail}). പ്രിന്റ് ആവുകയാണ്.",
        "preview": f"🖨️ {bill['bill_id']} opened in Preview.",
        "saved": f"🖨️ {bill['bill_id']} saved (no printer set).",
        "queued": f"🖨️ {bill['bill_id']} sent to the shop printer. കടയിലെ പ്രിന്ററിലേക്ക് അയച്ചു.",
    }.get(mode, f"❌ {bill['bill_id']} print failed: {detail}")
    await update.message.reply_text(reply)


async def send_customer_options(message, bill, note=None):
    """Tell the shopkeeper how to get the bill to the customer."""
    lines = [note] if note else []
    telegram_link = store.telegram_link(bill)
    if telegram_link:
        lines.append(f"Customer can scan the QR on the counter screen, or forward:\n{telegram_link}")
    whatsapp_link = store.whatsapp_share_link(bill)
    if not whatsapp_link:
        lines.append("💡 Send the customer's 10-digit phone number (say it or type it) to send this bill on WhatsApp.")
        await message.reply_text("\n\n".join(lines))
        return

    phone = store.format_phone(bill["customer_phone"])
    lines.insert(0, f"📲 {bill['bill_id']} → {phone}\n"
                    "നമ്പർ ശരിയാണോ? Check the number! If it's wrong, just type the right one.")
    if not store.PUBLIC_URL:
        lines.append("(Set PUBLIC_URL in .env to include a PDF link in the WhatsApp message.)")
    button = InlineKeyboardMarkup([[InlineKeyboardButton(f"📲 Send on WhatsApp to {phone}", url=whatsapp_link)]])
    try:
        await message.reply_text("\n\n".join(lines), reply_markup=button)
    except BadRequest as error:  # e.g. Telegram refuses a very long button link
        log.warning("WhatsApp button refused (%s); sending the link as text", error)
        await message.reply_text("\n\n".join(lines + [whatsapp_link]))


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """Any error in a handler: log it and tell the user, so the bot never goes silent."""
    log.error("Error while handling a Telegram message: %r", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(TRY_AGAIN)
        except NetworkError:
            pass


# ---------------------------------------------------------------- Start / stop

APP = None  # the running Telegram Application, for messages the server sends on its own


async def notify_shopkeepers(text):
    """Message every shopkeeper (e.g. "EB-0042 didn't print"). Never raises: a failed alert must not
    break the request that caused it."""
    if APP is None:
        log.warning("Not sent (bot not running): %s", text)
        return
    for user_id in store.SHOPKEEPER_IDS:
        try:
            await APP.bot.send_message(user_id, text)
        except Exception as error:
            log.error("Could not alert shopkeeper %s: %s", user_id, error)


def is_cancel_update(update):
    """A Cancel tap, or a typed "cancel" / "വേണ്ട" (not voice: that needs Gemini first)."""
    if not isinstance(update, Update):
        return False
    if update.callback_query is not None:
        return (update.callback_query.data or "").startswith("cancel:")
    message = update.message
    return message is not None and bool(message.text) and bill_parser.is_cancel(message.text)


class CancelFirst(BaseUpdateProcessor):
    """Handle messages one at a time, in the order they arrive, EXCEPT cancels, which run at once.

    A WhatsApp countdown runs on its own clock. If Cancel waited in line behind a voice note
    being transcribed (up to 40 s) or a bill being printed, the bill would go to the wrong
    number before the Cancel was even read."""

    def __init__(self):
        super().__init__(max_concurrent_updates=64)
        self._in_order = None  # made inside the running event loop (Python 3.9 binds locks to a loop)

    async def do_process_update(self, update, coroutine):
        if is_cancel_update(update):
            await coroutine
            return
        if self._in_order is None:
            self._in_order = asyncio.Lock()  # first come, first served: keeps message order
        async with self._in_order:
            await coroutine

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

async def start_bot():
    """Start long polling. Returns the Application, or None if no token is configured."""
    if not store.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set: Telegram bot is disabled.")
        return None
    app = (
        Application.builder()
        .token(store.TELEGRAM_BOT_TOKEN)
        .connect_timeout(15)  # defaults are 5 s, too short for slow shop/stage networks
        .read_timeout(30)
        .write_timeout(30)
        .media_write_timeout(60)
        .concurrent_updates(CancelFirst())
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler(["summary", "report"], day_summary_command))
    app.add_handler(CommandHandler("add", add_items_command))
    app.add_handler(CallbackQueryHandler(cancel_button, pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(phone_choice, pattern=r"^phone:"))
    app.add_handler(CallbackQueryHandler(confirm_button, pattern=r"^confirm:bill"))
    app.add_handler(CallbackQueryHandler(discard_button, pattern=r"^discard:bill"))
    app.add_handler(CallbackQueryHandler(confirm_items, pattern=r"^confirm:items"))
    app.add_handler(CallbackQueryHandler(discard_items, pattern=r"^discard:items"))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_note))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_error_handler(on_error)
    try:
        await app.initialize()
        await app.start()
        await app.bot.set_my_commands([  # the "/" menu Telegram shows while typing
            BotCommand("start", "Welcome"),
            BotCommand("myid", "Show my Telegram ID (for SHOPKEEPER_IDS)"),
            BotCommand("summary", "ഇന്നത്തെ കണക്ക് · Today's report"),
            BotCommand("report", "Same as /summary"),
            BotCommand("add", "Add items: /add bread 45 piece, soap 40"),
        ])
        # Keep messages that arrived while the server was down (Telegram holds them for 24 h):
        # a voice note sent during a restart or power cut is answered when the server is back.
        await app.updater.start_polling(drop_pending_updates=False)
    except Exception:
        await shutdown_quietly(app)  # a half-started bot must not linger before the next try
        raise
    log.info("Telegram bot @%s is polling.", store.BOT_USERNAME or "?")
    global APP
    APP = app
    if not store.SHOPKEEPER_IDS:
        log.warning("SHOPKEEPER_IDS is empty: nobody can bill yet. Send /myid to the bot and add it to .env.")
    return app


async def shutdown_quietly(app):
    """Undo whatever part of start_bot() succeeded. Errors here don't matter: we're giving up anyway."""
    for step in (
        app.updater.stop if app.updater and app.updater.running else None,
        app.stop if app.running else None,
        app.shutdown,
    ):
        if step is not None:
            try:
                await step()
            except Exception:
                pass


async def keep_starting_bot(on_started, first_wait=5, longest_wait=60):
    """Start the bot, and if that fails (no internet yet: the Mac just woke up or logged in), try
    again after 5 s, 10 s, 20 s… up to once a minute, until it works. The server never runs
    without the bot just because the network was slow to come up."""
    wait = first_wait
    while True:
        try:
            on_started(await start_bot())
            return
        except Exception as error:  # a timeout, no DNS, Telegram down…
            log.error("Could not start Telegram bot (%s); trying again in %d s", error, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, longest_wait)


async def stop_bot(app):
    global APP
    APP = None
    if app is None:
        return
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
