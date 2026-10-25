"""Turn a spoken transcript (or a WhatsApp voice note) into [{item_id, qty}].

1. Try Gemini (if GEMINI_API_KEY is set). Gemini only picks item ids and quantities.
2. If that fails, use the local parser: alias dictionary + number words + fuzzy matching.
Prices are never decided here; store.price_items() looks them up in inventory.json.
"""
import base64
import json
import logging
import math
import re

import httpx
from rapidfuzz import fuzz, process

import store

log = logging.getLogger("easybill.parser")

# ---------------------------------------------------------------- Gemini

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RULES = """You are a billing assistant for a small grocery shop in Kerala.
The shopkeeper says a list of items in Malayalam, English or Manglish.
Match each item to ONE item_id from the inventory below, but ONLY if it is the same product.
Never substitute a similar product: sunflower oil is not coconut oil, green chilli (പച്ചമുളക്)
is not chilli powder, tomato is not potato. Put anything you cannot match in "unknown".
Quantity is in the item's unit: for kg/litre items convert grams to kg and ml to litre
("അര"/"half" = 0.5). For piece/packet items the quantity is a whole count; if only a weight
is said for a packet item, use 1. If no quantity is said, use 1. Never include prices.
The shopkeeper may also say the customer's phone number: it is never an item quantity.
Instructions like "send it on WhatsApp", "the number is", "വാട്സാപ്പിൽ അയക്കൂ" are not items:
leave them out of both "items" and "unknown".

Inventory (item_id | English | Malayalam | unit | aliases):
"""

TEXT_TASK = """
Transcript: {transcript}

Reply ONLY with JSON like {{"items": [{{"item_id": "rice", "qty": 2}}], "unknown": ["sunflower oil 1 litre"]}}."""

AUDIO_TASK = """
The attached audio is the shopkeeper speaking.
Reply ONLY with JSON like {"transcript": "<the exact words you heard, in the script spoken>",
"items": [{"item_id": "rice", "qty": 2}], "unknown": ["<items said that are not in the inventory>"],
"phone": "<the customer's phone number as digits if one was said, else empty>"}.
Phone numbers: Indian mobiles have exactly 10 digits and start with 6, 7, 8 or 9. Listen to every
digit in order. Digits may be said one by one, in pairs or groups ("seventy" = 70, "thirty four" = 34,
"double one" = 11, "triple nine" = 999), in English or in Malayalam (പൂജ്യം 0, ഒന്ന് 1, രണ്ട് 2,
മൂന്ന് 3, നാല് 4, അഞ്ച് 5, ആറ് 6, ഏഴ് 7, എട്ട് 8, ഒമ്പത് 9). In "transcript", write numbers as digits."""


def inventory_text():
    return "\n".join(
        f"{i['id']} | {i['name_en']} | {i['name_ml']} | {i['unit']} | {', '.join(i['aliases'])}"
        for i in store.INVENTORY
    )


def ask_gemini(parts, timeout):
    """Send prompt parts to Gemini and return the JSON it replies with."""
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    response = httpx.post(
        GEMINI_URL.format(model=store.GEMINI_MODEL),
        headers={"x-goog-api-key": store.GEMINI_API_KEY},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])


def clean_items(data):
    """Keep only known item ids with a positive quantity. Pieces and packets are whole numbers."""
    items = []
    for entry in data:
        item_id = str(entry.get("item_id", ""))
        try:
            qty = float(entry.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if item_id not in store.INVENTORY_BY_ID or not math.isfinite(qty) or qty <= 0:
            continue
        if store.INVENTORY_BY_ID[item_id]["unit"] in ("piece", "packet"):
            qty = max(1, round(qty))
        items.append({"item_id": item_id, "qty": qty})
    return merge_duplicates(items)


def clean_unknown(data):
    return [str(name).strip() for name in data if str(name).strip()]


def rescue_unknown(items, unknown):
    """Gemini sometimes calls a listed item "unknown". Give each one a second try with the
    local parser; only what still doesn't match is reported. Drops words like "number"."""
    still_unknown = []
    for name in unknown:
        found = parse_locally(name)
        if found:
            items = merge_duplicates(items + found)
        elif not INSTRUCTION.search(name) and name.lower() not in FILLER_WORDS:
            still_unknown.append(name)
    return items, still_unknown


# Words that mean "send it to this number", not an item ("send it on WhatsApp", "നമ്പർ")
INSTRUCTION = re.compile(r"whats\s*app|വാട്സ്?ആ?പ്പ്|send|അയക്ക|number|നമ്പർ|phone|mobile|ഫോൺ", re.IGNORECASE)


def parse_with_gemini(transcript):
    """Returns (items, unknown)."""
    prompt = RULES + inventory_text() + TEXT_TASK.format(transcript=transcript)
    data = ask_gemini([{"text": prompt}], timeout=10)
    return rescue_unknown(clean_items(data.get("items", [])), clean_unknown(data.get("unknown", [])))


ITEM_TASK = """
This is a photo of a purchase bill or price list for a small shop in Kerala.
Extract every line item with its per-unit price.
Reply ONLY with JSON like {"items":[{"name_en":"Bread","name_ml":"ബ്രെഡ്","price":45,"unit":"piece"}]}
unit is one of kg, litre, piece, packet — use piece if unsure. price is per unit in rupees;
if a line is a total for a quantity (e.g. 2 kg for 110), divide to get the per-unit price.
name_ml is the Malayalam name; translate common grocery names, else reuse name_en."""

AUDIO_ITEMS_TASK = """
The attached audio (or the text below) is the shopkeeper listing NEW stock items with their
prices, in Malayalam, English or Manglish (e.g. "ബ്രെഡ് 45 രൂപ, സോപ്പ് 40 രൂപ").
Reply ONLY with JSON like {"items":[{"name_en":"Bread","name_ml":"ബ്രെഡ്","price":45,"unit":"piece"}]}
unit is one of kg, litre, piece, packet — use piece if unsure. price is per unit in rupees.
name_ml is the Malayalam name; translate common grocery names, else reuse name_en."""

NEW_ITEM_UNITS = {"kg", "litre", "piece", "packet"}


def existing_item_id(name_en, name_ml):
    """The price-list item with this exact name or alias ("Coconut Oil" -> coconut_oil), or None.
    Re-adding an item must update its price, not create a second "Coconut Oil"."""
    for name in (name_en, name_ml):
        if name and name.lower() in ALIASES:
            return ALIASES[name.lower()]
    return None


def new_item_id(name_en):
    """An id in the same style as inventory.json (coconut_oil); Malayalam-only names get item_<code>."""
    item_id = re.sub(r"[^a-z0-9]+", "_", name_en.lower()).strip("_")
    if not item_id:
        item_id = "item_" + base64.b32encode(name_en.encode()).decode().lower().rstrip("=")[:8]
    return item_id


def clean_new_items(data):
    """Keep only priced rows: a name, a positive price, a known unit. Builds the item id."""
    items = []
    for entry in data:
        name_en = str(entry.get("name_en", "")).strip()
        try:
            price = float(entry.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not name_en or not math.isfinite(price) or price <= 0:
            continue
        name_ml = str(entry.get("name_ml", "")).strip()
        item_id = existing_item_id(name_en, name_ml) or new_item_id(name_en)
        unit = str(entry.get("unit", "piece")).lower()
        items.append({
            "id": item_id,
            "name_en": name_en,
            "name_ml": name_ml or name_en,
            "price": round(price, 2),
            "unit": unit if unit in NEW_ITEM_UNITS else "piece",
        })
    return items


def parse_photo(image, mime_type):
    """Photo of a purchase bill / price list -> [{id, name_en, name_ml, price, unit}]."""
    parts = [
        {"inline_data": {"mime_type": mime_type.split(";")[0], "data": base64.b64encode(image).decode()}},
        {"text": ITEM_TASK},
    ]
    data = ask_gemini(parts, timeout=40)
    return clean_new_items(data.get("items", []))


def parse_items_audio(audio, mime_type):
    """Voice note listing new items with prices -> same shape as parse_photo."""
    parts = [
        {"inline_data": {"mime_type": mime_type.split(";")[0], "data": base64.b64encode(audio).decode()}},
        {"text": AUDIO_ITEMS_TASK},
    ]
    data = ask_gemini(parts, timeout=40)
    return clean_new_items(data.get("items", []))


def parse_items_text(text):
    """Typed "add items bread 45 piece, soap 40" -> same shape as parse_photo."""
    data = ask_gemini([{"text": AUDIO_ITEMS_TASK + "\n\nText: " + text}], timeout=15)
    return clean_new_items(data.get("items", []))


# "add items", "new items", "പുതിയ സാധനം ചേർക്കൂ", "സാധനങ്ങൾ എഴുതി വെക്കൂ", "price list".
# Not a bare "add" or "stock": "add 2 kg rice" and "2 soap, stock illa" are orders.
ADD_ITEMS = re.compile(
    r"^/add\b|\badd\s+(?:new\s+)?(?:items?|stock|to\s+(?:the\s+)?price[\s-]?list)\b|\bnew\s+(?:items?|stock)\b|"
    r"\bprice[\s-]?list\b|^stock\s+(?:update|add)|"
    r"സാധന\w*\s*ചേർ|സാധന\w*\s*എഴുതി|പുതിയ\s*സാധന|പുതിയ\s*സ്റ്റോക്ക്|വില\w*\s*ചേർ",
    re.IGNORECASE,
)


def is_add_items(text):
    """"പുതിയ സാധനങ്ങൾ ചേർക്കൂ" / "add items" — new stock for the price list, not a bill."""
    return bool(ADD_ITEMS.search(text.strip()))


def parse_audio(audio, mime_type):
    """Voice note -> (transcript, items, phone, unknown). Gemini listens to the audio directly.
    If Gemini hears words but finds no items, the local parser tries the transcript."""
    if not store.GEMINI_API_KEY:
        raise RuntimeError("Voice notes need GEMINI_API_KEY in .env")
    parts = [
        {"inline_data": {"mime_type": mime_type.split(";")[0], "data": base64.b64encode(audio).decode()}},
        {"text": RULES + inventory_text() + AUDIO_TASK},
    ]
    data = ask_gemini(parts, timeout=40)
    transcript = str(data.get("transcript", "")).strip()
    text_without_phone, phone_in_text = split_phone(transcript)
    phone = normalize_phone(str(data.get("phone") or "")) or phone_in_text
    items, unknown = rescue_unknown(clean_items(data.get("items", [])), clean_unknown(data.get("unknown", [])))
    if not items and transcript:
        items = parse_locally(text_without_phone)
    return transcript, items, phone, unknown


# ---------------------------------------------------------------- Local parser

NUMBER_WORDS = {
    # Malayalam
    "കാൽ": 0.25, "അര": 0.5, "ഒന്ന്": 1, "ഒന്നു": 1, "ഒരു": 1, "ഒരെണ്ണം": 1, "ഒന്നര": 1.5,
    "രണ്ട്": 2, "രണ്ടു": 2, "രണ്ടെണ്ണം": 2, "രണ്ടര": 2.5, "മൂന്ന്": 3, "മൂന്നു": 3, "മൂന്നെണ്ണം": 3,
    "നാല്": 4, "നാലു": 4, "അഞ്ച്": 5, "അഞ്ചു": 5, "ആറ്": 6, "ആറു": 6, "ഏഴ്": 7, "ഏഴു": 7,
    "എട്ട്": 8, "എട്ടു": 8, "ഒമ്പത്": 9, "ഒൻപത്": 9, "പത്ത്": 10, "പത്തു": 10, "പന്ത്രണ്ട്": 12,
    "ഇരുപത്": 20, "അമ്പത്": 50, "നൂറ്": 100, "ഇരുനൂറ്": 200, "ഇരുനൂറ്റമ്പത്": 250, "അഞ്ഞൂറ്": 500,
    # Manglish
    "kaal": 0.25, "ara": 0.5, "onnu": 1, "oru": 1, "onnara": 1.5, "randu": 2, "rand": 2,
    "randara": 2.5, "moonnu": 3, "munnu": 3, "naalu": 4, "nalu": 4, "anju": 5, "aaru": 6,
    "ezhu": 7, "ettu": 8, "ombathu": 9, "pathu": 10,
    # English
    "quarter": 0.25, "half": 0.5, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}

# Unit word -> multiplier applied to the spoken number (grams become kg, ml become litre).
UNIT_WORDS = {
    "kg": 1, "kgs": 1, "kilo": 1, "kilos": 1, "kilogram": 1, "kilograms": 1, "കിലോ": 1, "കിലോഗ്രാം": 1,
    "l": 1, "ltr": 1, "litre": 1, "litres": 1, "liter": 1, "liters": 1, "ലിറ്റർ": 1, "ലിറ്റര്": 1,
    "g": 0.001, "gm": 0.001, "gms": 0.001, "gram": 0.001, "grams": 0.001, "ഗ്രാം": 0.001,
    "ml": 0.001, "മില്ലി": 0.001,
    "dozen": 12, "ഡസൻ": 12,
}

# Counting words we simply ignore ("രണ്ട് എണ്ണം", "one packet", ...).
FILLER_WORDS = {
    "packet", "packets", "pack", "piece", "pieces", "pcs", "pc", "nos", "number", "ennam",
    "എണ്ണം", "പാക്കറ്റ്", "പായ്ക്കറ്റ്", "കവർ", "cover", "of", "the", "some", "venam", "വേണം",
}

SEPARATORS = re.compile(r",|;|\n|(?<!\d)\.(?!\d)|\band\b|\bpinne\b|പിന്നെ|ഒപ്പം|\bplus\b", re.IGNORECASE)


def ml_conjunctive(word):
    """Malayalam adds 'ഉം' (and) to words: സോപ്പ് -> സോപ്പും, മുട്ട -> മുട്ടയും, പഴം -> പഴവും."""
    if not re.search(r"[ഀ-ൿ]", word):
        return []
    if word.endswith("്"):
        return [word[:-1] + "ും"]
    if word.endswith("ം"):
        return [word[:-1] + "വും"]
    if word.endswith("ൽ"):
        return [word[:-1] + "ലും"]
    if word.endswith("ൾ"):
        return [word[:-1] + "ളും"]
    return [word + "യും", word + "വും"]


def build_alias_table():
    """Map every alias (and its Malayalam 'and' form) to an item id."""
    table = {}
    for item in store.INVENTORY:
        for alias in item["aliases"] + [item["name_en"]]:
            alias = alias.lower()
            table[alias] = item["id"]
            words = alias.split()
            for form in ml_conjunctive(words[-1]):
                table[" ".join(words[:-1] + [form])] = item["id"]
    return table


ALIASES = build_alias_table()
MAX_ALIAS_WORDS = max(len(a.split()) for a in ALIASES)


def tokenize(text):
    text = text.lower().replace("½", " 0.5 ")
    text = re.sub(r"(\d)([^\d\s.])", r"\1 \2", text)  # "2kg" -> "2 kg"
    text = re.sub(r"[^\w\s.ഀ-ൿ]", " ", text)
    return [t.strip(".") for t in text.split() if t.strip(".")]


def read_number(token):
    if re.fullmatch(r"\d+(\.\d+)?", token):
        return float(token)
    return NUMBER_WORDS.get(token)


def match_item(tokens, i):
    """Find an item starting at tokens[i]. Returns (item_id, words_used) or (None, 0)."""
    # 1. Exact alias, longest phrase first ("coconut oil" before "oil").
    for n in range(min(MAX_ALIAS_WORDS, len(tokens) - i), 0, -1):
        phrase = " ".join(tokens[i:i + n])
        if phrase in ALIASES:
            return ALIASES[phrase], n

    token = tokens[i]
    if len(token) < 3:
        return None, 0

    # 2. Malayalam word with a suffix, e.g. "അരിയും" starts with "അരി". Longest alias wins.
    best = None
    for alias, item_id in ALIASES.items():
        stem = alias.rstrip("്")
        if " " not in alias and len(stem) >= 3 and token.startswith(stem):
            if best is None or len(stem) > len(best[0]):
                best = (stem, item_id)
    if best:
        return best[1], 1

    # 3. Fuzzy match for spelling/recognition mistakes ("biskut", "panjasaara").
    if i + 1 < len(tokens):
        two = token + " " + tokens[i + 1]
        found = process.extractOne(two, ALIASES.keys(), scorer=fuzz.ratio, score_cutoff=88)
        if found and " " in found[0]:
            return ALIASES[found[0]], 2
    if len(token) >= 4:
        found = process.extractOne(token, ALIASES.keys(), scorer=fuzz.ratio, score_cutoff=80)
        if found:
            return ALIASES[found[0]], 1
    return None, 0


def parse_chunk(tokens):
    """Read tokens left to right. A number/unit before an item belongs to that item.
    A number left over at the end belongs to the last item ("rice 2 kg")."""
    results = []
    qty, multiplier = None, 1.0
    last_had_qty = True
    i = 0
    while i < len(tokens):
        token = tokens[i]
        number = read_number(token)
        if number is not None:
            # "ഒന്നര" style is one word, but "1 അര" means 1.5
            qty = qty + number if (qty is not None and number == 0.5) else number
            i += 1
            continue
        if token in UNIT_WORDS:
            multiplier = UNIT_WORDS[token]
            i += 1
            continue
        if token in FILLER_WORDS:
            i += 1
            continue
        item_id, used = match_item(tokens, i)
        if item_id:
            results.append({"item_id": item_id, "qty": make_qty(item_id, qty, multiplier)})
            last_had_qty = qty is not None
            qty, multiplier = None, 1.0
            i += used
            continue
        i += 1

    if results and qty is not None and not last_had_qty:
        results[-1]["qty"] = make_qty(results[-1]["item_id"], qty, multiplier)
    return results


def make_qty(item_id, qty, multiplier):
    unit = store.INVENTORY_BY_ID[item_id]["unit"]
    if qty is None:
        qty = 1
    if multiplier < 1 and unit in ("piece", "packet"):
        return 1  # "250 gram tea powder" -> 1 packet
    return round(qty * multiplier, 3)


def merge_duplicates(items):
    merged = {}
    for entry in items:
        merged[entry["item_id"]] = merged.get(entry["item_id"], 0) + entry["qty"]
    return [{"item_id": k, "qty": round(v, 3)} for k, v in merged.items()]


def parse_locally(transcript):
    items = []
    for chunk in SEPARATORS.split(transcript):
        items.extend(parse_chunk(tokenize(chunk)))
    return merge_duplicates(items)


# ---------------------------------------------------------------- Entry point

GREETINGS = {"hi", "hai", "hello", "hey", "help", "start", "namaskaram", "നമസ്കാരം", "ഹായ്"}


def is_greeting(text):
    return text.strip(" !.").lower() in GREETINGS


CANCEL_WORDS = {"cancel", "stop", "no", "don't send", "dont send", "venda", "വേണ്ട", "ക്യാൻസൽ", "നിർത്തൂ", "അയക്കണ്ട"}


def is_cancel(text):
    return text.strip(" !.").lower() in CANCEL_WORDS


CONFIRM_WORDS = {
    "ok", "okay", "yes", "correct", "confirm", "sure", "done",
    "sheri", "sari", "shari", "seri", "aanu", "aan", "aakoo", "cheyyu", "cheyya",
    "ശരി", "ശെരി", "ശരിയാണ്", "ആണ്", "ആണല്ലോ", "ആ", "ആക്കൂ", "ആക്ക്", "ചെയ്യൂ", "തന്നെ",
}


def is_confirm(text):
    """"ശരി" / "ശരി ആണ്" / "ok" — confirms the open bill draft. Every word must be a
    confirmation word (max 3), so "ശരി, അരി രണ്ട് കിലോ" still starts a new draft."""
    words = text.strip(" !.").lower().split()
    return 0 < len(words) <= 3 and all(word in CONFIRM_WORDS for word in words)


# Day-end report asks: "ഇന്നത്തെ ആകെ", "sales report", "today's total", "കണക്ക് കാണിക്കൂ"
SUMMARY_WORDS = re.compile(
    r"ആകെ|വിറ്റുവരവ്|കണക്ക്|റിപ്പോർട്ട|റിപോർട്ട|സംഗ്രഹം|\breport\b|\bsummary\b|\bsales\b"
    r"|\bcollection\b|\btotal\b|day[\s-]?end|\baake\b|\bkanakk\b",
    re.IGNORECASE,
)


def is_summary(text):
    """A short ask for the day's report, not an order: at most 6 words, no numbers and no
    price-list items, so "1 kg rice total" or "അരി ആകെ 5 കിലോ" still make a bill."""
    return (
        len(text.split()) <= 6
        and bool(SUMMARY_WORDS.search(text))
        and not re.search(r"\d", text)
        and not parse_locally(text)
    )


def wants_whatsapp(text):
    """An ask to (re)send a bill on WhatsApp: "whatsapp-il ayakkoo", "send it on whatsapp".
    Checked only when no items were found, so "2 kg rice, send on whatsapp" still makes a bill."""
    return bool(INSTRUCTION.search(text))


# "ഇതൊന്ന് print ചെയ്യൂ" / "print the bill" / "EB-0013 print cheyyu" — reprint a bill
PRINT_WORDS = re.compile(r"\bprint\b|\breprint\b|പ്രിന്റ്|പ്രിൻറ്|അച്ചടി|പ്രിന്റടിക്കൂ", re.IGNORECASE)


def wants_print(text):
    """An ask to print a bill again: "ഇതൊന്ന് print ചെയ്യൂ", "ee bill print aki tharo".
    Checked only when no items were found."""
    return bool(PRINT_WORDS.search(text))


# A bill id said or typed later: "send EB-0042 to WhatsApp" (also "eb 0042", "EB42")
BILL_ID = re.compile(r"\be\s*b[\s.-]?0*(\d+)\b", re.IGNORECASE)


def bill_id_in(text):
    """The bill id mentioned in a message, normalised to EB-0042, or None."""
    match = BILL_ID.search(text)
    return f"EB-{int(match.group(1)):04d}" if match else None


# An Indian mobile number, optionally with +91 / 0 and spaces or dashes: "98470 12345", "+91-9847012345"
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9](?:[\s-]?\d){9}(?!\d)")


def normalize_phone(text):
    """Return the number as 91XXXXXXXXXX (what wa.me needs), or None if it isn't an Indian mobile."""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return "91" + digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def split_phone(text):
    """Take the customer's phone number out of a message so it isn't read as a quantity.
    Returns (text without the number, phone or None)."""
    match = PHONE.search(text)
    if not match:
        return text, None
    return (text[:match.start()] + " " + text[match.end():]).strip(), normalize_phone(match.group())


def parse(transcript):
    """Returns (items, source, unknown). source is "gemini" or "local";
    unknown lists things said that are not in the price list (Gemini only)."""
    if store.GEMINI_API_KEY:
        try:
            items, unknown = parse_with_gemini(transcript)
            if items or unknown:
                return items, "gemini", unknown
        except Exception as error:  # network, quota, bad JSON... fall back silently
            log.warning("Gemini failed, using local parser: %s", error)
    return parse_locally(transcript), "local", []
