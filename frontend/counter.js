// Counter screen on the Mac: shows the newest bill (from Telegram, WhatsApp or the mic page)
// with the UPI QR and a QR that sends the customer the bill. Checks the server every 2 seconds.

const UNIT_LABEL = { kg: "kg", litre: "L", piece: "pc", packet: "pkt" };
const $ = (id) => document.getElementById(id);
let shownBillId = null;
let shownPhone = null;
let shownDelivered = null;
let shownSend = null;
let countdownTimer = null;

// Item names can be added by photo or voice, so never put them into HTML unescaped.
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function rupees(value) {
  return "₹" + Number(value).toFixed(2);
}

function drawQr(elementId, text, fallback) {
  const element = $(elementId);
  element.innerHTML = "";
  if (!text) {
    element.innerHTML = `<p class="qr-fallback">${escapeHtml(fallback)}</p>`;
  } else if (window.QRCode) {
    new QRCode(element, { text, width: 512, height: 512, correctLevel: QRCode.CorrectLevel.M });
  } else {
    element.innerHTML = `<p class="qr-fallback">${escapeHtml(text)}</p>`;
  }
}

function showBill(bill) {
  $("waiting").classList.add("hidden");
  $("bill").classList.remove("hidden");
  $("bill-id").textContent = bill.bill_id;
  $("channel").textContent = { telegram: "Telegram voice", whatsapp: "WhatsApp voice" }[bill.channel] || "Mic / web";
  const phone = bill.customer_phone ? `   ·   📲 ${formatPhone(bill.customer_phone)}` : "";
  $("heard").textContent = (bill.transcript ? `🎙️ "${bill.transcript}"` : "") + phone;
  $("rows").innerHTML = bill.items
    .map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.name_ml)}</strong><br><small>${escapeHtml(item.name_en)}</small></td>
        <td>${item.qty} ${UNIT_LABEL[item.unit]}</td>
        <td>${rupees(item.line_total)}</td>
      </tr>`)
    .join("");
  $("total").textContent = rupees(bill.total);
  $("pdf-link").href = `/api/bills/${bill.bill_id}/pdf`;
  drawQr("qr-upi", bill.upi_link);
  const app = customerApp(bill);
  $("customer-qr-title").innerHTML = app === "WhatsApp"
    ? "Bill on WhatsApp<br><small>വാട്സാപ്പിൽ ബിൽ</small>"
    : "Bill on Telegram<br><small>ടെലിഗ്രാമിൽ ബിൽ</small>";
  drawQr("qr-customer", app === "WhatsApp" ? bill.whatsapp_link : bill.telegram_link,
    "Set BOT_USERNAME in .env");
}

function formatPhone(phone) {
  return `+${phone.slice(0, 2)} ${phone.slice(2, 7)} ${phone.slice(7)}`;
}

// The WhatsApp auto-send: a big number and countdown, so the shopkeeper can check it from a distance.
function showSend(bill) {
  const element = $("send-status");
  clearInterval(countdownTimer);
  const status = bill.send_status;
  element.classList.toggle("hidden", !status || status === "sent");  // "sent" shows in the delivery line
  if (!status || status === "sent") return;
  const number = `<span class="send-number">${formatPhone(bill.customer_phone)}</span>`;
  element.className = "status " + ({ pending: "sending", cancelled: "cancelled", failed: "failed" }[status]);
  if (status === "pending") {
    const tick = () => {
      const left = Math.max(0, Math.ceil(bill.send_at - Date.now() / 1000));
      element.innerHTML = `📲 Sending on WhatsApp in ${left} s…${number}<small>നമ്പർ ശരിയാണോ? Cancel in Telegram if wrong</small>`;
    };
    tick();
    countdownTimer = setInterval(tick, 500);
  } else if (status === "cancelled") {
    element.innerHTML = `✋ Not sent (cancelled)${number}`;
  } else {
    element.innerHTML = `❌ Could not send on WhatsApp${number}`;
  }
}

// WhatsApp bills get the WhatsApp QR; everything else uses Telegram when it is set up.
function customerApp(bill) {
  if (bill.whatsapp_link && (bill.channel === "whatsapp" || !bill.telegram_link)) return "WhatsApp";
  return "Telegram";
}

function showDelivery(bill) {
  const element = $("delivery");
  const app = bill.delivered_via === "whatsapp" ? "WhatsApp" : "Telegram";
  const action = customerApp(bill) === "WhatsApp" ? "tap send" : "tap Start";
  element.className = "status " + (bill.delivered ? "sent" : "waiting");
  element.innerHTML = bill.delivered
    ? `✅ Bill sent on ${app}<br><small>ബിൽ ${app}-ൽ അയച്ചു</small>`
    : `⏳ Customer: scan the QR and ${action}<br><small>QR സ്കാൻ ചെയ്യുക</small>`;
}

async function refresh() {
  try {
    const response = await fetch("/api/bills/latest");
    const bill = await response.json();
    $("error").classList.add("hidden");
    if (!bill) return;
    if (bill.bill_id !== shownBillId || bill.customer_phone !== shownPhone) {  // the number can come later
      shownBillId = bill.bill_id;
      shownPhone = bill.customer_phone;
      shownDelivered = null;
      shownSend = null;
      showBill(bill);
    }
    if (`${bill.send_status}:${bill.send_at}` !== shownSend) {
      shownSend = `${bill.send_status}:${bill.send_at}`;
      showSend(bill);
    }
    if (bill.delivered !== shownDelivered) {
      shownDelivered = bill.delivered;
      showDelivery(bill);
    }
  } catch (error) {
    $("error").textContent = "Cannot reach the server / സെർവർ ലഭ്യമല്ല";
    $("error").classList.remove("hidden");
  }
}

async function init() {
  try {
    const config = await (await fetch("/api/config")).json();
    $("shop-name").textContent = config.shop_name;
    const example = `"രണ്ട് കിലോ അരി, ഒരു സോപ്പ്"`;
    $("waiting-help").textContent = config.telegram_enabled
      ? `Send a voice note to @${config.bot_username} on Telegram, e.g. ${example}.`
      : config.whatsapp_number
        ? `Send a WhatsApp voice note to +${config.whatsapp_number}, e.g. ${example}.`
        : "Set up the Telegram bot (see README). Bills from the mic page also show here.";
  } catch (error) {
    // refresh() shows the error
  }
  $("print").onclick = async () => {
    if (shownBillId) await fetch(`/api/bills/${shownBillId}/print`, { method: "POST" });
  };
  refresh();
  setInterval(refresh, 2000);
}

init();
