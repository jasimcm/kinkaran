// Easy Bill frontend: speak -> bill -> QR codes. Plain JavaScript, no build step.

const DEMO_SENTENCES = [
  "രണ്ട് കിലോ അരി, ഒരു സോപ്പ്, അര ലിറ്റർ പാൽ",
  "2 kg rice, 1 kg sugar, 1 litre coconut oil, 6 eggs",
  "അര കിലോ പഞ്ചസാര, ഒരു പാക്കറ്റ് ചായപ്പൊടി, മൂന്ന് മുട്ട, ഒരു കിലോ ഉള്ളി",
];
const UNIT_LABEL = { kg: "kg", litre: "L", piece: "pc", packet: "pkt" };

const state = {
  lang: "ml-IN",
  inventory: {},     // id -> inventory item
  items: [],         // [{item_id, qty}]
  demoIndex: 0,
  pollTimer: null,
  recognition: null,
  listening: false,
};

const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------ helpers

function show(id, visible) {
  $(id).classList.toggle("hidden", !visible);
}

function showError(message) {
  $("error").textContent = message;
  show("error", Boolean(message));
}

// Item names can be added by photo or voice, so never put them into HTML unescaped.
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function rupees(value) {
  return "₹" + Number(value).toFixed(2);
}

function round(value, places = 3) {
  const f = 10 ** places;
  return Math.round(value * f) / f;
}

async function api(path, body) {
  const options = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Server error ${response.status}`);
  }
  return response.json();
}

// ------------------------------------------------------------ startup

async function init() {
  try {
    const [inventory, config] = await Promise.all([api("/api/inventory"), api("/api/config")]);
    inventory.forEach((item) => (state.inventory[item.id] = item));
    $("shop-name").textContent = config.shop_name;
    $("add-select").innerHTML = inventory
      .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name_ml)} · ${escapeHtml(item.name_en)} (₹${item.price}/${UNIT_LABEL[item.unit]})</option>`)
      .join("");
  } catch (error) {
    showError("Cannot reach the server. Is it running? / സെർവർ ലഭ്യമല്ല");
  }
  setupSpeech();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

// ------------------------------------------------------------ speech

function setupSpeech() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $("mic").disabled = true;
    show("no-speech", true);
    $("mic-status").textContent = "";
    return;
  }

  const recognition = new Recognition();
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = (event) => {
    let text = "";
    for (const result of event.results) text += result[0].transcript;
    $("transcript").value = text;
  };
  recognition.onerror = (event) => {
    const messages = {
      "not-allowed": "Microphone permission denied. The page must be opened over HTTPS. / മൈക്ക് അനുമതി ഇല്ല",
      "no-speech": "Didn't hear anything. Try again. / ഒന്നും കേട്ടില്ല, വീണ്ടും പറയൂ",
      network: "Speech service needs internet. Type the items or use Demo. / ഇന്റർനെറ്റ് ആവശ്യമാണ്",
    };
    showError(messages[event.error] || `Mic error: ${event.error}`);
  };
  recognition.onend = () => {
    setListening(false);
    if ($("transcript").value.trim()) makeBill();
  };

  state.recognition = recognition;
}

function setListening(on) {
  state.listening = on;
  $("mic").classList.toggle("listening", on);
  $("mic-status").innerHTML = on
    ? "Listening… speak now<br>കേൾക്കുന്നു… പറയൂ"
    : "Tap the mic and speak the items<br>മൈക്ക് അമർത്തി സാധനങ്ങൾ പറയുക";
}

function toggleMic() {
  if (!state.recognition) return;
  if (state.listening) {
    state.recognition.stop();
    return;
  }
  showError("");
  $("transcript").value = "";
  state.recognition.lang = state.lang;
  try {
    state.recognition.start();
    setListening(true);
  } catch (error) {
    showError("Mic is busy, try again. / വീണ്ടും ശ്രമിക്കുക");
  }
}

// ------------------------------------------------------------ bill

async function makeBill() {
  const transcript = $("transcript").value.trim();
  if (!transcript) {
    showError("Speak or type the items first. / ആദ്യം സാധനങ്ങൾ പറയുക");
    return;
  }
  showError("");
  $("make-bill").disabled = true;
  try {
    const result = await api("/api/parse", { transcript });
    state.items = result.items.map((item) => ({ item_id: item.item_id, qty: item.qty }));
    $("source").textContent = result.source === "gemini" ? "AI · Gemini" : "Offline parser";
    if (result.unknown.length) {
      showError(`Not in price list (not billed) / പട്ടികയിൽ ഇല്ല: ${result.unknown.join(", ")}`);
    }
    renderBill();
    show("step-bill", true);
    $("step-bill").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    showError(error.message);
  } finally {
    $("make-bill").disabled = false;
  }
}

function renderBill() {
  const container = $("bill-items");
  container.innerHTML = "";
  let total = 0;

  state.items.forEach((entry, index) => {
    const item = state.inventory[entry.item_id];
    const lineTotal = round(entry.qty * item.price, 2);
    total += lineTotal;

    const row = document.createElement("div");
    row.className = "bill-item";
    row.innerHTML = `
      <div class="item-name">${escapeHtml(item.name_ml)}<small>${escapeHtml(item.name_en)} · ₹${item.price}/${UNIT_LABEL[item.unit]}</small></div>
      <div class="item-total">${rupees(lineTotal)}</div>
      <div class="item-controls">
        <button class="qty-btn" data-action="minus" aria-label="Less">−</button>
        <input class="qty-input" type="number" inputmode="decimal" min="0" step="any" value="${entry.qty}" aria-label="Quantity">
        <button class="qty-btn" data-action="plus" aria-label="More">+</button>
        <span class="unit">${UNIT_LABEL[item.unit]}</span>
        <button class="del-btn" data-action="delete" aria-label="Delete">✕</button>
      </div>`;

    const step = item.unit === "kg" || item.unit === "litre" ? 0.5 : 1;
    row.querySelector('[data-action="minus"]').onclick = () => changeQty(index, Math.max(step, entry.qty - step));
    row.querySelector('[data-action="plus"]').onclick = () => changeQty(index, entry.qty + step);
    row.querySelector('[data-action="delete"]').onclick = () => {
      state.items.splice(index, 1);
      renderBill();
    };
    row.querySelector(".qty-input").onchange = (event) => {
      const qty = parseFloat(event.target.value);
      if (qty > 0) changeQty(index, qty);
      else renderBill();
    };
    container.appendChild(row);
  });

  $("total").textContent = rupees(total);
  show("empty-bill", state.items.length === 0);
  $("confirm").disabled = state.items.length === 0;
}

function changeQty(index, qty) {
  state.items[index].qty = round(qty);
  renderBill();
}

function addItem() {
  const itemId = $("add-select").value;
  const qty = parseFloat($("add-qty").value);
  if (!itemId || !(qty > 0)) return;
  const existing = state.items.find((entry) => entry.item_id === itemId);
  if (existing) existing.qty = round(existing.qty + qty);
  else state.items.push({ item_id: itemId, qty });
  $("add-qty").value = 1;
  show("step-bill", true);
  renderBill();
}

// ------------------------------------------------------------ confirm + QR

async function confirmBill() {
  showError("");
  $("confirm").disabled = true;
  try {
    const bill = await api("/api/bills", { items: state.items });
    showPayment(bill);
  } catch (error) {
    showError(error.message);
    $("confirm").disabled = false;
  }
}

function drawQr(elementId, text, fallback) {
  const element = $(elementId);
  element.innerHTML = "";
  if (!text) {
    element.innerHTML = `<p class="qr-fallback">${escapeHtml(fallback)}</p>`;
    return;
  }
  if (window.QRCode) {
    new QRCode(element, { text, width: 512, height: 512, correctLevel: QRCode.CorrectLevel.M });
  } else {
    element.innerHTML = `<p class="qr-fallback">${escapeHtml(text)}</p>`;
  }
}

function showPayment(bill) {
  show("step-speak", false);
  show("step-bill", false);
  show("step-pay", true);
  $("pay-bill-id").textContent = bill.bill_id;
  $("pay-total").textContent = rupees(bill.total);
  $("pdf-link").href = `/api/bills/${bill.bill_id}/pdf`;
  drawQr("qr-upi", bill.upi_link);
  drawQr("qr-whatsapp", bill.whatsapp_link);
  drawQr("qr-telegram", bill.telegram_link, "Telegram bot not configured (set BOT_USERNAME in .env)");
  show("card-whatsapp", Boolean(bill.whatsapp_link));
  show("card-telegram", Boolean(bill.telegram_link) || !bill.whatsapp_link);
  setDelivered(false);
  window.scrollTo({ top: 0, behavior: "smooth" });

  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const status = await api(`/api/bills/${bill.bill_id}`);
      if (status.delivered) {
        setDelivered(true, status.delivered_via);
        clearInterval(state.pollTimer);
      }
    } catch (error) {
      // keep polling; the network on stage can be flaky
    }
  }, 2500);
}

function setDelivered(sent, via) {
  const element = $("delivery");
  const app = via === "whatsapp" ? "WhatsApp" : "Telegram";
  element.className = "status " + (sent ? "sent" : "waiting");
  element.innerHTML = sent
    ? `✅ PDF sent on ${app}<br><small>ബിൽ ${app}-ൽ അയച്ചു</small>`
    : "⏳ Waiting for customer to scan the QR…<br><small>കസ്റ്റമർ സ്കാൻ ചെയ്യാൻ കാത്തിരിക്കുന്നു</small>";
}

// ------------------------------------------------------------ reset + demo

function newBill() {
  clearInterval(state.pollTimer);
  if (state.listening) state.recognition.stop();
  state.items = [];
  $("transcript").value = "";
  $("bill-items").innerHTML = "";
  showError("");
  show("step-speak", true);
  show("step-bill", false);
  show("step-pay", false);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function demo() {
  newBill();
  $("transcript").value = DEMO_SENTENCES[state.demoIndex];
  state.demoIndex = (state.demoIndex + 1) % DEMO_SENTENCES.length;
  makeBill();
}

// ------------------------------------------------------------ wiring

document.querySelectorAll(".lang-btn").forEach((button) => {
  button.onclick = () => {
    state.lang = button.dataset.lang;
    document.querySelectorAll(".lang-btn").forEach((b) => b.classList.toggle("active", b === button));
  };
});
$("mic").onclick = toggleMic;
$("make-bill").onclick = makeBill;
$("demo").onclick = demo;
$("add-btn").onclick = addItem;
$("confirm").onclick = confirmBill;
$("new-bill").onclick = newBill;
$("new-bill-2").onclick = newBill;

init();
