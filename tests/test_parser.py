"""Offline parser, phone numbers and intent detection. No Gemini."""
import pytest

import parser as bill_parser


def items(text):
    return {entry["item_id"]: entry["qty"] for entry in bill_parser.parse_locally(text)}


# ---------------------------------------------------------------- items and quantities

@pytest.mark.parametrize("text, expected", [
    ("രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ", {"rice": 2, "soap": 1, "milk": 0.5}),
    ("2 kg rice, 1 kg sugar, 1 litre coconut oil, 6 eggs", {"rice": 2, "sugar": 1, "coconut_oil": 1, "eggs": 6}),
    ("അര കിലോ പഞ്ചസാര, ഒരു പാക്കറ്റ് ചായപ്പൊടി, മൂന്ന് മുട്ട, ഒരു കിലോ ഉള്ളി",
     {"sugar": 0.5, "tea_powder": 1, "eggs": 3, "onion": 1}),
    ("രണ്ട് കിലോ അരി ഒരു സോപ്പ് അര ലിറ്റർ പാൽ", {"rice": 2, "soap": 1, "milk": 0.5}),  # no commas
    ("2 കിലോ അരിയും സോപ്പും പാലും", {"rice": 2, "soap": 1, "milk": 1}),  # "ഉം" forms
    ("rice 2 kg, sugar half kg", {"rice": 2, "sugar": 0.5}),  # quantity after the item
    ("500 gram dal, one dozen eggs", {"dal": 0.5, "eggs": 12}),
    ("randu kilo ari, oru soppu, ara litre paal, biskut", {"rice": 2, "soap": 1, "milk": 0.5, "biscuits": 1}),
    ("ഒന്നര കിലോ ഉരുളക്കിഴങ്ങ്", {"potato": 1.5}),
    ("200 g pacha milagu, 200 g thakkali", {"green_chilli": 0.2, "tomato": 0.2}),
    ("1 litre sunflower oil, 1 litre oil", {"sunflower_oil": 1, "coconut_oil": 1}),
    ("2 soap, 1 soap", {"soap": 3}),  # duplicates are added up
])
def test_offline_parser(text, expected):
    assert items(text) == expected


def test_grams_of_a_packet_item_is_one_packet():
    assert items("250 gram tea powder") == {"tea_powder": 1}


def test_unknown_words_bill_nothing():
    assert items("1 kg chicken") == {}
    assert items("hello how are you") == {}


def test_gemini_items_are_cleaned():
    cleaned = bill_parser.clean_items([
        {"item_id": "soap", "qty": 2.4},          # pieces are whole numbers
        {"item_id": "tea_powder", "qty": 0.01},   # never below 1 packet
        {"item_id": "rice", "qty": 1.25},          # kg keeps decimals
        {"item_id": "gold", "qty": 1},             # not in the price list
        {"item_id": "milk", "qty": -1},            # no negative quantities
        {"item_id": "sugar", "qty": 0},
    ])
    assert {entry["item_id"]: entry["qty"] for entry in cleaned} == {"soap": 2, "tea_powder": 1, "rice": 1.25}


def test_gemini_can_never_set_a_price():
    """Even if the model invents a price, only item_id and qty survive."""
    cleaned = bill_parser.clean_items([{"item_id": "rice", "qty": 2, "price": 1}])
    assert cleaned == [{"item_id": "rice", "qty": 2}]


def test_unknown_items_get_a_second_chance_locally():
    found, unknown = bill_parser.rescue_unknown([], ["200 gram thakkali", "1 kg chicken", "number", "send it on whatsapp"])
    assert {entry["item_id"] for entry in found} == {"tomato"}
    assert unknown == ["1 kg chicken"]


# ---------------------------------------------------------------- phone numbers

@pytest.mark.parametrize("text", [
    "7034191969", "70341 91969", "+91 70341-91969", "07034191969", "91 7034191969",
    "2 kg rice, 1 soap, send it on whatsapp the number is 7034191969",
])
def test_phone_numbers_become_91_prefixed(text):
    _, phone = bill_parser.split_phone(text)
    assert phone == "917034191969"


@pytest.mark.parametrize("text", ["934191969", "12345", "2 kg rice", "5034191969"])
def test_not_a_mobile_number(text):
    assert bill_parser.split_phone(text)[1] is None


def test_phone_number_is_never_a_quantity():
    rest, phone = bill_parser.split_phone("രണ്ട് കിലോ അരി 9847012345")
    assert phone == "919847012345"
    assert items(rest) == {"rice": 2}


# ---------------------------------------------------------------- intents

@pytest.mark.parametrize("text", ["ok", "ശരി", "ശരി ആണ്", "sheri", "yes", "OK!"])
def test_confirm(text):
    assert bill_parser.is_confirm(text)


@pytest.mark.parametrize("text", ["ശരി, അരി രണ്ട് കിലോ", "ok 2 kg rice", "2 kg rice"])
def test_not_confirm(text):
    assert not bill_parser.is_confirm(text)


@pytest.mark.parametrize("text", ["cancel", "വേണ്ട", "stop"])
def test_cancel(text):
    assert bill_parser.is_cancel(text)


@pytest.mark.parametrize("text", ["ഇന്നത്തെ ആകെ", "sales report", "today's total", "കണക്ക് കാണിക്കൂ"])
def test_day_summary(text):
    assert bill_parser.is_summary(text)


@pytest.mark.parametrize("text", [
    "2 kg rice, 1 soap, half litre milk, 6 eggs, 1 bread, 2 salt, 1 kg sugar total",  # long order
])
def test_long_order_is_not_a_summary(text):
    assert not bill_parser.is_summary(text)


@pytest.mark.parametrize("text", ["add items bread 45 piece", "/add soap 40", "പുതിയ സാധനം ചേർക്കൂ", "new items"])
def test_add_stock(text):
    assert bill_parser.is_add_items(text)


@pytest.mark.parametrize("text", ["ഇതൊന്ന് print ചെയ്യൂ", "print EB-0013", "reprint"])
def test_print(text):
    assert bill_parser.wants_print(text)


@pytest.mark.parametrize("text, bill_id", [
    ("send EB-0042 to whatsapp", "EB-0042"), ("eb 42 print", "EB-0042"), ("EB42", "EB-0042"), ("2 kg rice", None),
])
def test_bill_id(text, bill_id):
    assert bill_parser.bill_id_in(text) == bill_id


# ---------------------------------------------------------------- ordering words that are NOT intents

@pytest.mark.parametrize("text", [
    "add 2 kg rice and 1 soap",     # English "add" in an order
    "1 kg rice total",             # "total" inside a short order
    "2 soap, stock illa",           # "stock" inside an order
])
def test_order_words_do_not_trigger_other_intents(text):
    """A shopkeeper's order must not be mistaken for 'add stock' or 'day report'."""
    assert not bill_parser.is_add_items(text), "order treated as adding stock to the price list"
    assert not bill_parser.is_summary(text), "order treated as a day-end report request"
