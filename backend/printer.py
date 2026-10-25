"""Print a bill's receipt on the Mac.

Set PRINTER in .env:
  (empty)        save the PDF in backend/printed/ only
  preview        save it and open it in Preview (good for a demo with no printer)
  <printer name> send it to that printer with `lp`. List names with:  lpstat -p
  remote         cloud server: queue it; the print helper on the shop Mac (print_agent/) prints it

PRINT_PAPER picks the page:
  a4       the 80mm receipt at native size, centred on A4 (inkjet/laser like HP Smart Tank)
  receipt  the 80mm page itself (thermal printer)

print_bill returns (mode, detail): "printed" + CUPS job id, "preview", "saved",
or "failed" + lp's error output.
"""
import logging
import re
import subprocess

import pdf_gen
import store

log = logging.getLogger("easybill.printer")

PRINTED_DIR = store.BACKEND_DIR / "printed"


def print_bill(bill):
    if store.PRINTER == "remote":  # cloud server: the shop's print helper picks it up
        store.set_print_status(bill["bill_id"], "queued")
        log.info("Queued %s for the shop printer", bill["bill_id"])
        return "queued", None

    PRINTED_DIR.mkdir(exist_ok=True)
    path = PRINTED_DIR / f"EasyBill_{bill['bill_id']}.pdf"

    if store.PRINTER == "preview":
        path.write_bytes(pdf_gen.build_pdf(bill))
        command = ["open", str(path)]
        mode = "preview"
    elif store.PRINTER:
        if store.PRINT_PAPER == "a4":
            path.write_bytes(pdf_gen.build_a4_pdf(bill))
            # media=A4 with no scaling: the user's CUPS default fit-to-page can't enlarge it
            command = ["lp", "-d", store.PRINTER, "-o", "media=A4", "-o", "fit-to-page=false", str(path)]
        else:
            path.write_bytes(pdf_gen.build_pdf(bill))
            command = ["lp", "-d", store.PRINTER, "-o", "fit-to-page", str(path)]
        mode = "printed"
    else:
        path.write_bytes(pdf_gen.build_pdf(bill))
        log.info("Saved %s (no PRINTER set)", path.name)
        return "saved", None

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        stderr = (getattr(error, "stderr", "") or str(error)).strip()
        log.error("Printing %s failed: %s", path.name, stderr)
        return "failed", stderr
    job = re.search(r"request id is (\S+)", result.stdout or "")
    detail = job.group(1) if job else None  # lp: "request id is HP_Smart_Tank_510_series-37 (1 file(s))"
    log.info("Printed %s with %s (job %s)", path.name, command[0], detail or "?")
    return mode, detail
