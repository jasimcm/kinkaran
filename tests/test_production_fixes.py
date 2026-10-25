"""Go-live fixes: wrong-customer bugs, restarts, backups, auto-start."""
import asyncio
import json
import plistlib
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Chat as TgChat, Message, Update, User

import bot
import store

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 1. Cancel is never stuck in the queue

def tg_update(update_id, text=None, callback=None):
    user = User(id=1, first_name="Shop", is_bot=False)
    if callback:
        query = CallbackQuery(id=str(update_id), from_user=user, chat_instance="x", data=callback)
        return Update(update_id, callback_query=query)
    message = Message(message_id=update_id, date=datetime.now(), chat=TgChat(1, "private"), text=text, from_user=user)
    return Update(update_id, message=message)


def test_cancel_runs_while_a_slow_message_is_being_handled():
    order = []

    async def handle(name, seconds):
        await asyncio.sleep(seconds)
        order.append(name)

    async def run():
        processor = bot.CancelFirst()
        slow = asyncio.ensure_future(processor.do_process_update(tg_update(1, text="2 kg rice"), handle("voice note", 0.3)))
        await asyncio.sleep(0.01)
        tap = asyncio.ensure_future(processor.do_process_update(tg_update(2, callback="cancel:EB-0001"), handle("cancel tap", 0)))
        typed = asyncio.ensure_future(processor.do_process_update(tg_update(3, text="വേണ്ട"), handle("typed cancel", 0)))
        await asyncio.gather(slow, tap, typed)

    asyncio.run(run())
    assert order == ["cancel tap", "typed cancel", "voice note"]


def test_other_messages_stay_in_order():
    order = []

    async def handle(name, seconds):
        await asyncio.sleep(seconds)
        order.append(name)

    async def run():
        processor = bot.CancelFirst()
        jobs = [asyncio.ensure_future(processor.do_process_update(tg_update(i, text=text), handle(text, delay)))
                for i, (text, delay) in enumerate([("2 kg rice", 0.2), ("ok", 0), ("1 soap", 0)], start=1)]
        await asyncio.gather(*jobs)

    asyncio.run(run())
    assert order == ["2 kg rice", "ok", "1 soap"]  # "ok" never overtakes the order it confirms


def test_bot_uses_cancel_first_and_keeps_messages_from_downtime(monkeypatch):
    """start_bot: the CancelFirst processor, and drop_pending_updates=False (keep messages sent while down)."""
    built = {}

    class FakeBuilder:
        def __getattr__(self, name):
            def setter(*args, **kwargs):
                built[name] = args[0] if args else None
                return self
            return setter

        def build(self):
            app = MagicMock()
            app.initialize, app.start = AsyncMock(), AsyncMock()
            app.bot.set_my_commands = AsyncMock()
            app.updater.start_polling = AsyncMock()
            built["app"] = app
            return app

    monkeypatch.setattr(store, "TELEGRAM_BOT_TOKEN", "1:test")
    monkeypatch.setattr(bot.Application, "builder", lambda: FakeBuilder())
    asyncio.run(bot.start_bot())
    assert isinstance(built["concurrent_updates"], bot.CancelFirst)
    built["app"].updater.start_polling.assert_awaited_once_with(drop_pending_updates=False)


# ---------------------------------------------------------------- 2. a stray "next customer" number never drifts

def confirm(chat, text):
    chat.say(text)
    chat.press("confirm:bill")
    return store.latest_bill()


def test_stray_number_is_used_up_by_the_next_draft(chat, fake_wa):
    """Reviewer scenario: X noted; next order has its own number Y; a later order without a number
    must NOT go to X."""
    confirm(chat, "1 soap 9847012345")               # EB-0001 for a customer
    assert "noted for the next bill" in chat.say("9999911111")
    assert confirm(chat, "2 kg rice 9847055555")["customer_phone"] == "919847055555"
    assert confirm(chat, "1 salt")["customer_phone"] is None


def test_noted_number_expires(chat, fake_wa):
    confirm(chat, "1 soap 9847012345")
    chat.say("9999911111")
    chat.context.chat_data["next_phone"]["at"] -= bot.NEXT_PHONE_SECONDS + 1
    assert confirm(chat, "1 salt")["customer_phone"] is None


def test_cancel_forgets_a_noted_number(chat, fake_wa):
    confirm(chat, "1 soap 9847012345")
    chat.say("9999911111")
    assert "Forgot the noted number" in chat.say("cancel")
    assert confirm(chat, "1 salt")["customer_phone"] is None


# ---------------------------------------------------------------- 3. a number before the items never lands on the last bill silently

def test_number_first_when_last_bill_has_no_customer_asks(chat, fake_wa):
    """Reviewer scenario: A's bill has no number (customer used the QR); B's number comes first.
    The bot must ask, and 'next customer' keeps A untouched."""
    confirm(chat, "2 kg rice")                        # A, no number
    assert "Which bill" in chat.say("9847011111")
    chat.press("phone:next")
    assert confirm(chat, "1 soap")["customer_phone"] == "919847011111"   # B
    assert store.get_bill("EB-0001")["customer_phone"] is None            # A untouched


def test_old_bill_is_never_offered(chat, fake_wa):
    confirm(chat, "2 kg rice")
    bills = json.loads(store.BILLS_FILE.read_text())
    bills["EB-0001"]["created_at"] = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    store.BILLS_FILE.write_text(json.dumps(bills))
    assert "noted for the next bill" in chat.say("9847011111")
    assert store.get_bill("EB-0001")["customer_phone"] is None


def test_choice_button_does_not_overwrite_a_number_added_meanwhile(chat, fake_wa):
    confirm(chat, "2 kg rice")
    chat.say("9847011111")
    store.set_customer_phone("EB-0001", "919999900000")  # e.g. set from another message meanwhile
    assert "already used" in chat.press("phone:EB-0001")
    assert store.get_bill("EB-0001")["customer_phone"] == "919999900000"


def test_number_during_countdown_still_corrects(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 0.3)
    confirm(chat, "2 kg rice 8044191969")
    chat.say("7034191969")
    chat.wait(1)
    assert fake_wa.sent == [("EB-0001", "917034191969")]


def test_cancel_stops_every_countdown(chat, fake_wa, monkeypatch):
    fake_wa.linked = True
    monkeypatch.setattr(store, "AUTO_SEND_SECONDS", 5)
    confirm(chat, "2 kg rice 7034191969")
    confirm(chat, "1 soap 9847012345")
    assert "Stopped: EB-0001, EB-0002" in chat.say("cancel")
    chat.wait(0.2)
    assert fake_wa.sent == []


# ---------------------------------------------------------------- backups

def run_backup(data_dir, backup_dir, keep=30):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "backup.sh")], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(data_dir.parent),
             "BACKUP_DIR": str(backup_dir), "DATA_DIR": str(data_dir), "KEEP": str(keep)},
    )


def test_backup_copies_both_files_and_keeps_the_newest(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    (data / "bills.json").write_text('{"EB-0001": {"total": 110}}')
    (data / "inventory.json").write_text('[{"id": "rice", "price": 55}]')
    for _ in range(5):
        result = run_backup(data, backups, keep=3)
        assert result.returncode == 0, result.stderr
    kept = sorted(backups.glob("kinkaran-*"))
    assert len(kept) == 3
    assert json.loads((kept[-1] / "bills.json").read_text()) == {"EB-0001": {"total": 110}}
    assert (kept[-1] / "inventory.json").exists()


def test_backup_refuses_a_broken_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bills.json").write_text('{"EB-0001": ')  # truncated
    result = run_backup(data, tmp_path / "backups")
    assert result.returncode != 0 and "not valid JSON" in result.stderr


# ---------------------------------------------------------------- launchd auto-start

def test_launchd_agents(tmp_path):
    result = subprocess.run(["bash", str(ROOT / "scripts" / "install_launchd.sh"), "--print", str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    agents = {path.stem: plistlib.loads(path.read_bytes()) for path in tmp_path.glob("*.plist")}
    assert set(agents) == {"com.kinkaran.server", "com.kinkaran.whatsapp", "com.kinkaran.tunnel", "com.kinkaran.backup"}
    for name in ("com.kinkaran.server", "com.kinkaran.whatsapp", "com.kinkaran.tunnel"):
        assert agents[name]["KeepAlive"] is True and agents[name]["RunAtLoad"] is True  # restart on crash and at login
    assert agents["com.kinkaran.server"]["ProgramArguments"][-1].endswith("backend/main.py")
    assert agents["com.kinkaran.whatsapp"]["WorkingDirectory"].endswith("whatsapp-sender")  # keeps its WhatsApp login
    assert agents["com.kinkaran.tunnel"]["WorkingDirectory"] == str(ROOT)
    assert agents["com.kinkaran.backup"]["StartCalendarInterval"] == {"Hour": 23, "Minute": 30}
    assert "KeepAlive" not in agents["com.kinkaran.backup"]
