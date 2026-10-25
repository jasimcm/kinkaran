#!/usr/bin/env python3
"""Kinkaran print helper for the shop Mac.

When Kinkaran runs in the cloud (PRINTER=remote), bills wait in a print queue on the server.
This helper asks for the queue every few seconds, prints each bill on the shop printer with `lp`,
and reports back, so a failed print reaches the shopkeeper on Telegram.

Only the Python standard library is used, so the Mac's built-in python3 runs it. Settings come
from print_agent/agent.env (see agent.env.example):

    KINKARAN_URL=https://myshop.duckdns.org
    ACCESS_KEY=<same as the server's ACCESS_KEY>
    PRINTER=HP_Smart_Tank_510_series      (lpstat -p)
    PRINT_PAPER=a4                        (a4 or receipt)

    python3 print_agent/print_agent.py           run forever (launchd does this)
    python3 print_agent/print_agent.py --check   test the server, the key and the printer, then exit
    python3 print_agent/print_agent.py --once    print what is queued now, then exit
"""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("print-agent")


def load_settings(path=HERE / "agent.env"):
    settings = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if match and not line.lstrip().startswith("#"):
                settings[match.group(1)] = match.group(2)
    for key in ("KINKARAN_URL", "ACCESS_KEY", "PRINTER", "PRINT_PAPER", "POLL_SECONDS", "LP", "STATE_FILE"):
        if os.environ.get(key):  # the environment wins (handy for tests)
            settings[key] = os.environ[key]
    settings.setdefault("PRINT_PAPER", "a4")
    settings.setdefault("POLL_SECONDS", "3")
    settings.setdefault("LP", "lp")
    settings.setdefault("STATE_FILE", str(HERE / "printed.json"))
    missing = [key for key in ("KINKARAN_URL", "ACCESS_KEY", "PRINTER") if not settings.get(key)]
    if missing:
        raise SystemExit(f"Missing in agent.env: {', '.join(missing)}")
    settings["KINKARAN_URL"] = settings["KINKARAN_URL"].rstrip("/")
    return settings


class Server:
    def __init__(self, settings):
        self.url, self.key = settings["KINKARAN_URL"], settings["ACCESS_KEY"]

    def _request(self, path, data=None):
        request = urllib.request.Request(self.url + path, data=data, headers={"x-access-key": self.key})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    def queue(self):
        return json.loads(self._request("/api/print-queue"))["bills"]

    def pdf(self, bill_id, paper):
        return self._request(f"/api/bills/{bill_id}/pdf?paper={paper}")

    def report(self, bill_id, ok, detail=""):
        self._request(f"/api/bills/{bill_id}/printed", json.dumps({"ok": ok, "detail": detail[:500]}).encode())


def lp_command(settings, path):
    """Same options as backend/printer.py, so cloud bills look exactly like the Mac's."""
    if settings["PRINT_PAPER"] == "a4":
        return [settings["LP"], "-d", settings["PRINTER"], "-o", "media=A4", "-o", "fit-to-page=false", str(path)]
    return [settings["LP"], "-d", settings["PRINTER"], "-o", "fit-to-page", str(path)]


def print_pdf(settings, bill_id, pdf):
    """Returns (ok, detail): the CUPS job id, or lp's error."""
    with tempfile.NamedTemporaryFile(prefix=f"Kinkaran_{bill_id}_", suffix=".pdf", delete=False) as file:
        file.write(pdf)
    try:
        result = subprocess.run(lp_command(settings, file.name), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    finally:
        os.unlink(file.name)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or f"lp exit {result.returncode}").strip()
    job = re.search(r"request id is (\S+)", result.stdout or "")
    return True, job.group(1) if job else ""


class PrintedLog:
    """Print requests already printed, by bill and the time it was queued. If the server didn't get
    our 'printed' report (internet dropped), the request is still queued next time: report again,
    but never print it twice. A reprint is queued at a new time, so it does print."""

    def __init__(self, path):
        self.path = Path(path)
        self.done = json.loads(self.path.read_text()) if self.path.exists() else {}

    def __contains__(self, request):
        return request in self.done

    def add(self, request, detail):
        self.done[request] = detail
        if len(self.done) > 500:  # keep the newest 500
            self.done = dict(list(self.done.items())[-500:])
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done))
        tmp.replace(self.path)


def run_once(settings, server, printed):
    """Print everything queued now. Returns how many bills were handled."""
    queue = server.queue()
    for entry in queue:
        bill_id = entry["bill_id"]
        request = f"{bill_id}@{entry.get('queued_at')}"
        if request in printed:  # printed before, but the report didn't arrive
            server.report(bill_id, True, printed.done[request])
            continue
        ok, detail = print_pdf(settings, bill_id, server.pdf(bill_id, settings["PRINT_PAPER"]))
        if ok:
            printed.add(request, detail)
            log.info("Printed %s (job %s)", bill_id, detail or "?")
        else:
            log.error("Printing %s failed: %s", bill_id, detail)
        server.report(bill_id, ok, detail)
    return len(queue)


def check(settings, server):
    """--check: is everything in place? Prints one line per check."""
    ok = True
    try:
        waiting = len(server.queue())
        print(f"✅ Server and access key OK ({settings['KINKARAN_URL']}): {waiting} bill(s) waiting")
    except urllib.error.HTTPError as error:
        ok = False
        print(f"❌ Server answered {error.code}" + (": wrong ACCESS_KEY?" if error.code == 403 else ""))
    except (urllib.error.URLError, OSError) as error:
        ok = False
        print(f"❌ Can't reach {settings['KINKARAN_URL']}: {error}")
    status = subprocess.run(["lpstat", "-p", settings["PRINTER"]], capture_output=True, text=True)
    if status.returncode == 0:
        print(f"✅ Printer: {status.stdout.strip()}")
    else:
        ok = False
        print(f"❌ Printer {settings['PRINTER']!r} not found. Names: lpstat -p")
    return ok


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    settings = load_settings()
    server, printed = Server(settings), PrintedLog(settings["STATE_FILE"])
    if "--check" in argv:
        return 0 if check(settings, server) else 1
    if "--once" in argv:
        run_once(settings, server, printed)
        return 0
    log.info("Printing bills from %s on %s (%s paper)", settings["KINKARAN_URL"], settings["PRINTER"], settings["PRINT_PAPER"])
    wait = float(settings["POLL_SECONDS"])
    while True:
        try:
            run_once(settings, server, printed)
            wait = float(settings["POLL_SECONDS"])
        except (urllib.error.URLError, OSError, ValueError) as error:  # offline, server restarting…
            wait = min(wait * 2, 60)
            log.warning("Server not reachable (%s); trying again in %.0f s", error, wait)
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
