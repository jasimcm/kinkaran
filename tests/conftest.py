"""Test setup: every test runs offline against temporary copies of the data files.

Nothing here talks to Telegram, WhatsApp or Gemini:
- environment variables are set BEFORE the backend is imported, so a real .env can't leak in
- bills.json, inventory.json and printed/ point at a temp folder per test
- Gemini is replaced by `fake_gemini` (returns whatever JSON the test queues)
- the WhatsApp sender is `fake_wa` (a fake linked or unlinked account)
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# Set before import: load_dotenv() never overrides variables that already exist.
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "",
    "BOT_USERNAME": "TestBot",
    "GEMINI_API_KEY": "",
    "SHOP_NAME": "Test Stores",
    "UPI_VPA": "test@upi",
    "WHATSAPP_TOKEN": "",
    "WHATSAPP_PHONE_NUMBER_ID": "",
    "WHATSAPP_NUMBER": "",
    "WHATSAPP_APP_SECRET": "",
    "PRINTER": "",
    "PUBLIC_URL": "https://test.example",
    "AUTO_SEND": "on",
    "AUTO_SEND_SECONDS": "10",
    "WA_SENDER_URL": "http://127.0.0.1:1",  # nothing listens here
})

import store  # noqa: E402
import parser as bill_parser  # noqa: E402
import printer  # noqa: E402
import wa_sender  # noqa: E402
import bot  # noqa: E402

SHOPKEEPER = 1        # the Telegram user id the fake chat uses by default
CUSTOMER = 2

ORIGINAL_INVENTORY = json.loads((ROOT / "backend" / "inventory.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Fresh bills, inventory and printed/ folder for every test."""
    inventory_file = tmp_path / "inventory.json"
    inventory_file.write_text(json.dumps(ORIGINAL_INVENTORY, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(store, "BILLS_FILE", tmp_path / "bills.json")
    monkeypatch.setattr(store, "INVENTORY_FILE", inventory_file)
    inventory = json.loads(json.dumps(ORIGINAL_INVENTORY))
    monkeypatch.setattr(store, "INVENTORY", inventory)
    monkeypatch.setattr(store, "INVENTORY_BY_ID", {item["id"]: item for item in inventory})
    monkeypatch.setattr(bill_parser, "ALIASES", bill_parser.build_alias_table())
    monkeypatch.setattr(printer, "PRINTED_DIR", tmp_path / "printed")
    monkeypatch.setattr(store, "PRINTER", "")
    monkeypatch.setattr(store, "GEMINI_API_KEY", "")
    monkeypatch.setattr(store, "AUTO_SEND", True)
    monkeypatch.setattr(store, "SHOPKEEPER_IDS", {SHOPKEEPER})
    monkeypatch.setattr(store, "ACCESS_KEY", "")
    monkeypatch.setattr(bot, "_last_send_started", 0.0)
    bot.PENDING.clear()
    yield tmp_path
    for task, _ in list(bot.PENDING.values()):
        task.cancel()
    bot.PENDING.clear()


@pytest.fixture
def fake_gemini(monkeypatch):
    """Queue the JSON replies Gemini should give; records every prompt it was sent."""
    class FakeGemini:
        def __init__(self):
            self.replies, self.calls = [], []

        def __call__(self, parts, timeout):
            self.calls.append(parts)
            if not self.replies:
                raise RuntimeError("fake Gemini: no reply queued")
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    fake = FakeGemini()
    monkeypatch.setattr(store, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(bill_parser, "ask_gemini", fake)
    return fake


@pytest.fixture
def fake_wa(monkeypatch):
    """A fake WhatsApp sender. `linked=False` means WhatsApp is not connected."""
    class FakeSender:
        def __init__(self):
            self.linked, self.sent, self.error = False, [], None

        async def is_ready(self):
            return self.linked

        async def send_bill(self, bill):
            if self.error:
                raise wa_sender.SendError(self.error)
            self.sent.append((bill["bill_id"], bill["customer_phone"]))

    fake = FakeSender()
    monkeypatch.setattr(wa_sender, "is_ready", fake.is_ready)
    monkeypatch.setattr(wa_sender, "send_bill", fake.send_bill)
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 0)
    monkeypatch.setattr(store, "WA_MIN_GAP_SECONDS", 0)
    return fake


class Chat:
    """A fake Telegram chat with the bot: send text/voice, press buttons, read replies."""

    def __init__(self, user_id=SHOPKEEPER):
        self.user_id = user_id
        self.loop = asyncio.new_event_loop()  # one loop, so countdown tasks keep running between messages
        self.context = MagicMock()
        self.context.chat_data = {}
        self.replies = []      # every text the bot sent (including edits)
        self.documents = []    # filenames of PDFs the bot sent
        self.buttons = []      # callback_data / url of the last message with buttons

    def _message(self, text=None):
        message = MagicMock()
        message.text = text
        message.reply_text = AsyncMock(side_effect=self._reply)
        message.reply_document = AsyncMock(side_effect=self._document)
        message.chat.send_action = AsyncMock()
        return message

    async def _reply(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        if reply_markup is not None:
            self.buttons = [b.callback_data or b.url for row in reply_markup.inline_keyboard for b in row]
        status = self._message(text)
        status.edit_text = AsyncMock(side_effect=self._edit)
        return status

    async def _edit(self, text, reply_markup=None, **kwargs):
        return await self._reply(text, reply_markup=reply_markup)

    async def _document(self, document, filename, **kwargs):
        assert bytes(document).startswith(b"%PDF"), "bot sent something that isn't a PDF"
        self.documents.append(filename)

    def update(self, text=None):
        update = MagicMock()
        update.message = self._message(text)
        update.effective_message = update.message
        update.effective_chat.id = self.user_id
        update.effective_user.id = self.user_id
        update.callback_query = None
        return update

    def run(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def wait(self, seconds=0.05):
        """Let background tasks (the WhatsApp countdown) run."""
        self.run(asyncio.sleep(seconds))

    def say(self, text):
        self.run(bot.text_message(self.update(text), self.context))
        return self.replies[-1] if self.replies else None

    def voice(self, gemini_reply, fake_gemini):
        """A voice note: Gemini's reply decides what was 'heard'."""
        fake_gemini.replies.append(gemini_reply)
        update = self.update()
        audio = MagicMock()
        audio.mime_type = "audio/ogg"
        file = MagicMock()
        file.download_as_bytearray = AsyncMock(return_value=bytearray(b"OggS fake audio"))
        audio.get_file = AsyncMock(return_value=file)
        update.message.voice = audio
        self.run(bot.voice_note(update, self.context))
        return self.replies[-1]

    def press(self, data):
        """Press an inline button: an exact callback_data, or the first current button starting with it."""
        if ":" in data and data.count(":") < 2 and not data.startswith("cancel:"):
            data = next((b for b in self.buttons if b and b.startswith(data + ":")), data)
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.message = self._message(self.replies[-1] if self.replies else "")
        query.edit_message_text = AsyncMock(side_effect=self._edit)
        update = MagicMock()
        update.callback_query = query
        update.effective_user.id = self.user_id
        handler = {
            "confirm:bill": bot.confirm_button, "discard:bill": bot.discard_button,
            "confirm:items": bot.confirm_items, "discard:items": bot.discard_items,
        }.get(data.rsplit(":", 1)[0] if data.count(":") == 2 else data) or bot.cancel_button
        self.run(handler(update, self.context))
        return self.replies[-1] if self.replies else None

    def close(self):
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.close()


@pytest.fixture
def chat():
    conversation = Chat()
    yield conversation
    conversation.close()


@pytest.fixture
def customer():
    """Someone who is NOT the shopkeeper (e.g. a customer who opened the bot from the counter QR)."""
    conversation = Chat(user_id=CUSTOMER)
    yield conversation
    conversation.close()


@pytest.fixture
def no_gemini_needed(monkeypatch):
    """Fail the test if anything tries to call Gemini."""
    def forbidden(*args, **kwargs):
        raise AssertionError("Gemini was called but should not have been")
    monkeypatch.setattr(bill_parser, "ask_gemini", forbidden)
