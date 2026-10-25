"""Cloud mode: the remote print queue, the shop Mac's print helper, and the Oracle deploy files.

The print-helper tests run the real server (uvicorn, on a free port) and the real helper over HTTP.
Only `lp` is faked: a tiny script that records what it was asked to print."""
import asyncio
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import uvicorn
from fastapi.testclient import TestClient

import bot
import main
import store

ROOT = Path(__file__).resolve().parent.parent


def load_agent():
    spec = importlib.util.spec_from_file_location("print_agent", ROOT / "print_agent" / "print_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_agent()


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(store, "PRINTER", "remote")


def make_bill(items=None):
    return store.create_bill(*store.price_items(items or [{"item_id": "rice", "qty": 2}]), channel="telegram")


# ---------------------------------------------------------------- server: the queue

def test_remote_printer_queues_instead_of_printing(remote, chat, fake_wa):
    chat.say("2 kg rice")
    chat.press("confirm:bill")
    assert [bill["bill_id"] for bill in store.print_queue()] == ["EB-0001"]
    assert store.get_bill("EB-0001")["print_status"] == "queued"


def test_reprint_says_sent_to_the_shop_printer(remote, chat, fake_wa):
    chat.say("2 kg rice"); chat.press("confirm:bill")
    assert "sent to the shop printer" in chat.say("print EB-0001")


def test_queue_api_and_a4_pdf(remote):
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    bill = make_bill()
    client.post(f"/api/bills/{bill['bill_id']}/print")
    queue = client.get("/api/print-queue").json()["bills"]
    assert [entry["bill_id"] for entry in queue] == ["EB-0001"] and queue[0]["queued_at"]
    a4 = client.get("/api/bills/EB-0001/pdf?paper=a4").content
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in a4
    assert client.get("/api/bills/EB-0001/pdf?paper=letter").status_code == 422
    assert client.post("/api/bills/EB-0001/printed", json={"ok": True, "detail": "HP-7"}).status_code == 200
    assert client.get("/api/print-queue").json()["bills"] == []
    assert store.get_bill("EB-0001")["print_status"] == "printed"


def test_queue_needs_the_key_from_outside(remote):
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    tunnel = {"x-forwarded-for": "203.0.113.9"}  # Caddy / cloudflared add this
    assert client.get("/api/print-queue", headers=tunnel).status_code == 403
    assert client.post("/api/bills/EB-0001/printed", json={"ok": True}, headers=tunnel).status_code == 403


def test_failed_print_alerts_the_shopkeeper(remote, monkeypatch):
    fake_app = MagicMock()
    fake_app.bot.send_message = AsyncMock()
    monkeypatch.setattr(bot, "APP", fake_app)
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    make_bill()
    client.post("/api/bills/EB-0001/print")
    client.post("/api/bills/EB-0001/printed", json={"ok": False, "detail": "printer is offline"})
    assert store.get_bill("EB-0001")["print_status"] == "failed"
    user_id, text = fake_app.bot.send_message.await_args.args
    assert user_id == 1 and "EB-0001 didn't print" in text and "printer is offline" in text


def test_non_ascii_keys_get_403_and_404_not_500():
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    make_bill()
    assert client.get("/?key=é", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    assert client.get("/share/EB-0001/é").status_code == 404


# ---------------------------------------------------------------- the print helper against the real server

@pytest.fixture
def live_server(remote):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def fake_lp(tmp_path):
    """A stand-in for lp: records its arguments; FAIL_LP=1 in a file makes it fail like an offline printer."""
    calls, script = tmp_path / "lp-calls.txt", tmp_path / "lp"
    script.write_text(
        "#!/bin/bash\n"
        f'if [ -f "{tmp_path}/fail" ]; then echo "lp: printer is offline" >&2; exit 1; fi\n'
        f'echo "$@" >> "{calls}"\n'
        'echo "request id is HP_Smart_Tank-$RANDOM (1 file(s))"\n'
    )
    script.chmod(0o755)
    return {"lp": str(script), "calls": calls, "fail": tmp_path / "fail"}


def helper(live_server, fake_lp, tmp_path, monkeypatch, paper="a4"):
    for key, value in {"KINKARAN_URL": live_server, "ACCESS_KEY": "k", "PRINTER": "HP_Smart_Tank_510_series",
                       "PRINT_PAPER": paper, "LP": fake_lp["lp"], "STATE_FILE": str(tmp_path / "printed.json")}.items():
        monkeypatch.setenv(key, value)
    settings = agent.load_settings(tmp_path / "no-agent.env")
    return settings, agent.Server(settings), agent.PrintedLog(settings["STATE_FILE"])


def printed_lines(fake_lp):
    return fake_lp["calls"].read_text().splitlines() if fake_lp["calls"].exists() else []


def test_helper_prints_queued_bills_once(live_server, fake_lp, tmp_path, monkeypatch):
    settings, server, printed = helper(live_server, fake_lp, tmp_path, monkeypatch)
    for _ in range(2):
        make_bill()
    for bill_id in ("EB-0001", "EB-0002"):
        main.printer.print_bill(store.get_bill(bill_id))
    assert agent.run_once(settings, server, printed) == 2
    lines = printed_lines(fake_lp)
    assert len(lines) == 2 and all("-d HP_Smart_Tank_510_series -o media=A4 -o fit-to-page=false" in line for line in lines)
    assert store.get_bill("EB-0001")["print_status"] == "printed"
    assert store.get_bill("EB-0001")["print_detail"].startswith("HP_Smart_Tank-")
    assert agent.run_once(settings, server, printed) == 0 and len(printed_lines(fake_lp)) == 2  # nothing twice


def test_lost_report_never_prints_twice(live_server, fake_lp, tmp_path, monkeypatch):
    settings, server, printed = helper(live_server, fake_lp, tmp_path, monkeypatch)
    main.printer.print_bill(make_bill())
    real_report, calls = server.report, []

    def report_lost_once(bill_id, ok, detail=""):
        calls.append(bill_id)
        if len(calls) == 1:
            raise OSError("internet dropped")
        real_report(bill_id, ok, detail)

    server.report = report_lost_once
    with pytest.raises(OSError):
        agent.run_once(settings, server, printed)
    agent.run_once(settings, server, printed)  # next round: the bill is still queued
    assert len(printed_lines(fake_lp)) == 1
    assert store.get_bill("EB-0001")["print_status"] == "printed"


def test_reprint_prints_again(live_server, fake_lp, tmp_path, monkeypatch):
    settings, server, printed = helper(live_server, fake_lp, tmp_path, monkeypatch)
    main.printer.print_bill(make_bill())
    agent.run_once(settings, server, printed)
    time.sleep(1.1)  # queued again at a new time
    main.printer.print_bill(store.get_bill("EB-0001"))
    agent.run_once(settings, server, printed)
    assert len(printed_lines(fake_lp)) == 2


def test_printer_offline_is_reported(live_server, fake_lp, tmp_path, monkeypatch):
    settings, server, printed = helper(live_server, fake_lp, tmp_path, monkeypatch, paper="receipt")
    fake_lp["fail"].touch()
    main.printer.print_bill(make_bill())
    agent.run_once(settings, server, printed)
    bill = store.get_bill("EB-0001")
    assert bill["print_status"] == "failed" and "offline" in bill["print_detail"]
    fake_lp["fail"].unlink()
    main.printer.print_bill(bill)  # "print EB-0001" once the printer is back
    agent.run_once(settings, server, printed)
    assert store.get_bill("EB-0001")["print_status"] == "printed"
    assert "-o fit-to-page" in printed_lines(fake_lp)[0] and "media=A4" not in printed_lines(fake_lp)[0]


def test_helper_needs_its_settings(tmp_path, monkeypatch):
    for key in ("KINKARAN_URL", "ACCESS_KEY", "PRINTER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="KINKARAN_URL, ACCESS_KEY, PRINTER"):
        agent.load_settings(tmp_path / "missing.env")


# ---------------------------------------------------------------- deploy files

def unit(name):
    """systemd unit -> {key: [values]} (keys can repeat, e.g. Environment)."""
    values = {}
    for line in (ROOT / "deploy" / "oracle" / name).read_text().splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, value = line.split("=", 1)
            values.setdefault(key, []).append(value)
    return values


def test_systemd_service():
    service = unit("kinkaran.service")
    assert service["User"] == ["kinkaran"] and service["Restart"] == ["always"]
    assert service["ExecStart"] == ["/opt/kinkaran/app/.venv/bin/python backend/main.py"]
    assert "HOST=127.0.0.1" in service["Environment"]  # only Caddy can reach the app
    assert service["ReadWritePaths"] == ["/opt/kinkaran"] and service["ProtectSystem"] == ["strict"]
    assert (ROOT / "backend" / "main.py").exists()


def test_backup_timer():
    assert unit("kinkaran-backup.timer")["OnCalendar"] == ["*-*-* 23:30:00"]
    backup = unit("kinkaran-backup.service")
    assert backup["ExecStart"] == ["/bin/bash /opt/kinkaran/app/scripts/backup.sh"]
    assert "BACKUP_DIR=/opt/kinkaran/backups" in backup["Environment"]


def test_setup_script_installs_files_that_exist():
    setup = (ROOT / "deploy" / "oracle" / "setup.sh").read_text()
    for name in ("kinkaran.service", "kinkaran-backup.service", "kinkaran-backup.timer"):
        assert f'"$APP/deploy/oracle/{name}"' in setup and (ROOT / "deploy" / "oracle" / name).exists()
    assert "timedatectl set-timezone Asia/Kolkata" in setup
    assert "reverse_proxy 127.0.0.1:8000" in setup and "PORT=8000" in (ROOT / "deploy/oracle/kinkaran.service").read_text()


def test_move_script_stops_the_mac_before_starting_the_cloud():
    move = (ROOT / "deploy" / "oracle" / "move_from_mac.sh").read_text()
    stop, start = move.index("scripts/install_launchd.sh --uninstall"), move.index("sudo systemctl restart kinkaran")
    assert stop < start  # only one copy of the bot may poll Telegram
    assert "PRINTER=remote" in move and "scripts/install_print_agent.sh" in move
