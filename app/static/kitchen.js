// Kitchen: the owner's order screen. One card per order with the bill split,
// everything needed to book Porter, and one big button for the next step.
// Every button that changes an order also messages the customer on WhatsApp.
(function () {
  const TOKEN_KEY = "admin_token";
  const SOUND_KEY = "kitchen_sound";
  const app = document.getElementById("app");
  let token = null, data = null, tab = "now", seen = null, soundOn = false, audio = null;
  let view = "home", focusOid = null, showMore = false;  // home | together | focus | later | done
  let shopItems = null, shopChanges = [], shopQ = "", editId = null;  // Shop tab: dishes (loaded when opened) and the search text
  const shopDraft = { name: "", price: "", reason: "", onlyToday: true };
  const plated = new Set();  // "Cook together" ticks, kept until reload
  const drafts = {};  // per-order Porter link/fare typed but not yet sent
  // /kitchen/o/46 (the Open order button on the owner's WhatsApp alert) jumps to #46.
  let focusId = +((/^\/kitchen\/o\/(\d+)/.exec(location.pathname) || [])[1]) || null;

  try { token = localStorage.getItem(TOKEN_KEY); soundOn = localStorage.getItem(SOUND_KEY) === "1"; } catch (_) {}

  // ---------- helpers
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => +v || 0;
  const rs = (v) => { const n = Math.round(num(v) * 100) / 100; return "₹" + (Number.isInteger(n) ? n : n.toFixed(2)); };
  const utc = (s) => s ? new Date(s.replace(" ", "T") + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? "" : "Z")) : null;
  const time = (d) => d ? d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "";
  const day = (d) => d ? d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" }) : "";
  const istDay = (d) => d ? d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }) : "";
  const ago = (d) => { if (!d) return ""; const m = Math.round((Date.now() - d) / 60000); return m < 1 ? "just now" : m < 60 ? m + " min ago" : Math.floor(m / 60) + " h " + (m % 60) + " min ago"; };
  const phone10 = (p) => String(p || "").replace(/\D/g, "");
  const waNum = (p) => { const d = phone10(p); return d.length === 10 ? "91" + d : d; };
  const prettyPhone = (p) => { const d = phone10(p); return d.length === 10 ? d.slice(0, 5) + " " + d.slice(5) : "+" + d; };
  const splitAddr = (a) => { const m = /\s*\(Landmark:\s*(.*?)\)\s*$/i.exec(a || ""); return m ? [a.slice(0, m.index), m[1]] : [a || "", ""]; };
  const WINDOWS = { lunch: "Lunch (12:00–2:30)", dinner: "Dinner (6:30–9:30)" };

  function toast(msg, ms) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), ms || 3500);
  }

  async function api(url, opts) {
    opts = opts || {};
    const res = await fetch(url, { ...opts, headers: { "X-Admin-Token": token, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (res.status === 401) { try { localStorage.removeItem(TOKEN_KEY); } catch (_) {} token = null; renderLogin("That password didn't work. Try again."); throw new Error("auth"); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Something went wrong. Try again.");
    return body;
  }

  // ---------- sound (phones only allow it after a tap)
  function unlockSound() {
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (_) {}
  }
  function ring() {
    if (navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 400]);
    if (!soundOn || !audio) return;
    [0, .22, .44, .9, 1.12, 1.34, 1.8, 2.02, 2.24].forEach((t, i) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.connect(g); g.connect(audio.destination); o.type = "sine"; o.frequency.value = i % 3 === 2 ? 988 : 784;
      const at = audio.currentTime + t;
      g.gain.setValueAtTime(.0001, at); g.gain.exponentialRampToValueAtTime(.35, at + .02); g.gain.exponentialRampToValueAtTime(.0001, at + .26);
      o.start(at); o.stop(at + .3);
    });
  }

  // ---------- grouping
  const needsMoney = (o) => (o.payment_method === "upi" && !o.paid && o.status !== "cancelled" && o.status !== "delivered") ? 1 : 0;
  const OPEN = ["new", "preparing", "ready", "out_for_delivery"];
  function isLater(o) {
    if (!o.scheduled_at || o.status !== "new") return false;
    return istDay(utc(o.scheduled_at)) > istDay(new Date());
  }
  function groups() {
    const all = (data && data.orders) || [];
    const today = istDay(new Date());
    const now = all.filter((o) => OPEN.includes(o.status) && !isLater(o));
    const later = all.filter((o) => OPEN.includes(o.status) && isLater(o));
    const done = all.filter((o) => !OPEN.includes(o.status) && istDay(utc(o.created_at)) === today);
    const rank = { new: 0, preparing: 1, ready: 2, out_for_delivery: 3 };
    now.sort((a, b) => (needsMoney(b) - needsMoney(a)) || rank[a.status] - rank[b.status] || a.id - b.id);
    later.sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
    return { now, later, done };
  }

  // ---------- views
  function renderLogin(err) {
    app.innerHTML = `<form class="login" id="login">
      <h1>Kitchen</h1>
      <div class="hint">Enter the kitchen password once. This phone remembers it.</div>
      <input id="pw" type="password" autocomplete="current-password" placeholder="Kitchen password" required>
      ${err ? `<div class="flag">${esc(err)}</div>` : ""}
      <button class="big" type="submit">Open the kitchen</button>
    </form>`;
    document.getElementById("login").addEventListener("submit", (e) => {
      e.preventDefault();
      token = document.getElementById("pw").value.trim();
      try { localStorage.setItem(TOKEN_KEY, token); } catch (_) {}
      unlockSound(); start();
    });
  }

  function summary(g) {
    const today = istDay(new Date());
    const todays = ((data && data.orders) || []).filter((o) => o.status !== "cancelled" && istDay(utc(o.created_at)) === today);
    const sales = todays.reduce((s, o) => s + num(o.total), 0);
    return `${todays.length} order${todays.length === 1 ? "" : "s"} today · ${rs(Math.round(sales))}` + (g.now.length ? ` · ${g.now.length} open` : "");
  }

  // ---------- multi-order views
  const clock = () => new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" });
  // The time something is due: the booked time, or placed time plus the usual wait.
  function dueOf(o) {
    if (o.scheduled_at) return utc(o.scheduled_at);
    const placed = utc(o.created_at) || new Date();
    return new Date(placed.getTime() + (o.order_type === "pickup" ? 25 : 45) * 60000);
  }
  const summaryItems = (o) => (o.items || []).map((i) => `${num(i.qty)} × ${i.name}`).join(", ");
  const areaOf = (o) => { const [addr] = splitAddr(o.delivery_address); const parts = addr.split(",").map((x) => x.trim()).filter(Boolean); return parts.length > 2 ? parts[parts.length - 2] : (parts[1] || parts[0] || ""); };
  const STATE_RANK = { new: 0, preparing: 1, ready: 2, out_for_delivery: 3 };
  function queue() {
    // Most urgent first: orders waiting to be accepted, then cooking, then the rest; by due time within each.
    return groups().now.slice().sort((a, b) => (STATE_RANK[a.status] - STATE_RANK[b.status]) || (dueOf(a) - dueOf(b)) || a.id - b.id);
  }
  const rowSub = (o) => {
    const st = { new: "New", preparing: "Cooking", ready: o.order_type === "pickup" ? "Ready for pickup" : "Ready, needs a rider", out_for_delivery: "On the way" }[o.status];
    return `${st} · ${o.order_type === "pickup" ? "pickup" : "delivery"} ${time(dueOf(o))}${needsMoney(o) ? " · 💰 check payment" : ""}`;
  };

  function banner(q) {
    const top = q[0];
    const fresh = q.filter((o) => o.status === "new" && !(view === "home" && top && o.id === top.id));
    if (!fresh.length) return "";
    const o = fresh[fresh.length - 1];
    return `<div class="newban" role="status"><div class="nb-t" data-open="${o.id}">
        <div class="nb-k">NEW · #${o.id} · ${o.order_type === "pickup" ? "PICKUP" : "DELIVERY"} ${time(dueOf(o))}${fresh.length > 1 ? ` · +${fresh.length - 1} more` : ""}</div>
        <div class="nb-i">${esc(summaryItems(o))}</div></div>
      <button class="nb-b" data-accept="${o.id}">Accept</button></div>`;
  }

  function topbar(g, title, back) {
    const n = g.now.length;
    return `<header class="top"><div class="top-row">
        ${back ? `<button class="back" id="back" aria-label="Back">‹</button>` : `<span class="clock" id="clock">${clock()}</span>`}
        ${title ? `<span class="ttl">${title}</span>` : `<span class="on">${n} order${n === 1 ? "" : "s"} on</span>`}
        <button class="sound ${soundOn ? "on" : "off"}" id="sound" aria-label="${soundOn ? "Sound on" : "Sound off"}">${soundOn ? "🔔" : "🔕 Sound off"}</button>
      </div></header>`;
  }

  const offPos = (o) => (o.items || []).some((i) => String(i.item_id || "").startsWith("sp-"));
  const offPosNote = (o) => offPos(o) ? `<div class="nu-note">⭐ Has a special that is not on Petpooja. Ring it up on the POS yourself.</div>` : "";

  function nextUpCard(o) {
    const note = (o.instructions || "").trim();
    const itemNotes = (o.items || []).filter((i) => i.note).map((i) => `${esc(i.name)}: ${esc(i.note)}`);
    const meta = [o.payment_method === "upi" ? (o.paid ? "Paid" : "UPI not confirmed") : "Cash", o.order_type === "delivery" ? areaOf(o) : "Pickup", `due ${time(dueOf(o))}`].filter(Boolean).join(" · ");
    const color = o.status === "new" ? "#E8A33D" : o.status === "preparing" ? "#0E7A45" : "#1B5FA8";
    return `<article class="nu" id="o-${o.id}" style="--ring:${color}">
      <div class="nu-h" style="background:${color}"><span>#${o.id} · ${{ new: "NEW", preparing: "COOKING", ready: "READY", out_for_delivery: "ON THE WAY" }[o.status]}</span><span>${o.order_type === "pickup" ? "PICKUP" : "DELIVERY"}</span></div>
      <div class="nu-b">
        <div class="nu-items">${(o.items || []).map((i) => `${num(i.qty)} × ${esc(i.name)}`).join("<br>")}</div>
        ${offPosNote(o)}
        ${note || itemNotes.length ? `<div class="nu-note">${[esc(note), ...itemNotes].filter(Boolean).join("<br>")}</div>` : ""}
        <div class="nu-meta">${esc(meta)}</div>
        ${payNow(o)}
        ${nextStep(o)}
        <button class="more" data-open="${o.id}">All details${o.order_type === "delivery" && ["preparing", "ready"].includes(o.status) && !(o.sr_tracking_url || o.sr_courier) ? ": book the rider ›" : " ›"}</button>
      </div></article>`;
  }

  function closedStrip() {
    const st = data && data.store;
    if (!st || st.is_open) return "";
    const back = st.turn_on_time ? new Date(String(st.turn_on_time).includes("T") ? st.turn_on_time + "+05:30" : st.turn_on_time.replace(" ", "T") + "+05:30") : null;
    const when = back && !isNaN(back) ? ` Opens again ${day(back)} ${time(back)}.` : " Stays closed until you open it.";
    return `<button class="shutstrip" data-view="shop">🔴 Online orders are OFF.${when} Tap to change.</button>`;
  }

  function homeView(g) {
    const q = queue(), top = q[0];
    if (!top) return `${banner(q)}${closedStrip()}<main class="list"><div class="empty">No orders right now.<br>New ones appear here by themselves.</div></main>${bottomBar(g)}`;
    const rest = q.slice(1), shown = showMore ? rest : rest.slice(0, 2);
    const unpaid = rest.filter(needsMoney);
    return `${banner(q)}<main class="home">
      ${closedStrip()}
      ${unpaid.length ? `<button class="paystrip" data-open="${unpaid[0].id}">💰 ${unpaid.length} other payment${unpaid.length > 1 ? "s" : ""} to check. Tap to open #${unpaid[0].id}.</button>` : ""}
      <div class="lane">DO THIS NEXT</div>${nextUpCard(top)}
      ${rest.length ? `<div class="lane dim">THEN</div><div class="rows">${shown.map((o) => `<button class="rowc" data-open="${o.id}">
          <div><div class="rc-t">#${o.id} · ${esc(summaryItems(o))}</div><div class="rc-s">${esc(rowSub(o))}</div></div>
          <span class="rc-time ${dueOf(o) - Date.now() < 15 * 60000 ? "soon" : ""}">${time(dueOf(o))}</span></button>`).join("")}
        ${rest.length > shown.length ? `<button class="rowc more-rows" id="showmore">+ ${rest.length - shown.length} more</button>` : ""}</div>` : ""}
      </main>${bottomBar(g)}`;
  }
  function bottomBar(g) {
    const closed = data && data.store && !data.store.is_open;
    return `<div class="bbar four"><button data-view="together">Cook together</button><button data-view="later">Later (${g.later.length})</button><button data-view="done">Done (${g.done.length})</button><button data-view="shop" class="${closed ? "shut" : ""}">${closed ? "🔴 Closed" : "Shop"}</button></div>`;
  }

  // Same dish across every active order, as a prep list.
  function togetherView(g) {
    const act = queue().filter((o) => ["new", "preparing"].includes(o.status));
    const map = new Map();
    act.forEach((o) => (o.items || []).forEach((i) => {
      const k = i.name; const e = map.get(k) || { name: k, qty: 0, chips: [] };
      e.qty += num(i.qty); e.chips.push({ id: o.id, qty: num(i.qty), note: i.note || "" });
      const on = (o.instructions || "").trim(); if (on && !e.chips.some((c) => c.id === o.id && c.order)) e.chips.push({ id: o.id, order: true, note: on });
      map.set(k, e);
    }));
    const list = [...map.values()].sort((a, b) => b.qty - a.qty);
    return `<main class="home"><div class="tg-h">Same dish across all active orders. Tap when plated.</div>
      ${list.length ? list.map((e) => `<button class="tg ${plated.has(e.name) ? "done" : ""}" data-plate="${esc(e.name)}">
        <div class="tg-top"><span class="tg-q">${e.qty} ×</span><span class="tg-n">${esc(e.name)}</span><span class="tg-c">${plated.has(e.name) ? "✓" : ""}</span></div>
        <div class="tg-chips">${e.chips.map((c) => c.note ? `<span class="chip flagnote">#${c.id} ${esc(c.note)}</span>` : `<span class="chip">#${c.id} ×${c.qty}</span>`).join("")}</div></button>`).join("") : `<div class="empty">Nothing is being cooked right now.</div>`}
      </main>`;
  }

  function priceEditor(d) {
    return `<div class="sh-edit"><div class="sh-n">${esc(d.name)}<span>Base price ${rs(d.original)}${d.original_half ? ` · half ${rs(d.original_half)}` : ""}</span></div>
      <label class="sh-l">Price<input class="sh-in" id="pe-price" inputmode="decimal" value="${d.price}"></label>
      ${d.half ? `<label class="sh-l">Half portion<input class="sh-in" id="pe-half" inputmode="decimal" value="${d.half}"></label>` : ""}
      <div class="sh-btns"><button class="sh-b" data-pe="cancel">Cancel</button><button class="sh-b save" data-pe="save" data-id="${esc(d.id)}">Save price</button></div>
      ${d.price !== d.original || (d.half && d.half !== d.original_half) ? `<button class="sh-x wide" data-pe="reset" data-id="${esc(d.id)}">Back to ${rs(d.original)}</button>` : ""}
    </div>`;
  }

  function shopView() {
    const st = (data && data.store) || { is_open: true };
    const back = st.turn_on_time ? new Date(String(st.turn_on_time).includes("T") ? st.turn_on_time + "+05:30" : st.turn_on_time.replace(" ", "T") + "+05:30") : null;
    const status = st.is_open
      ? `<div class="sh-state on">🟢 Taking online orders</div>
         <input class="sh-in" data-shop="reason" placeholder="Message for customers (optional)" value="${esc(shopDraft.reason)}">
         <div class="sh-btns">
           <button class="sh-b" data-close="hours:1">Close for 1 hour</button>
           <button class="sh-b" data-close="hours:2">Close for 2 hours</button>
           <button class="sh-b" data-close="today">Close for today</button>
           <button class="sh-b" data-close="manual">Close until I open</button>
         </div>`
      : `<div class="sh-state off">🔴 Online orders are OFF</div>
         <div class="sh-sub">${esc((st.reason || "").replace(/([^.!?])$/, "$1."))}${back && !isNaN(back) ? ` Opens again ${day(back)} ${time(back)}.` : ""}</div>
         <button class="big" data-open-shop="1">Open the shop now</button>`;
    const q = shopQ.trim().toLowerCase();
    const list = (shopItems || []).filter((d) => !q || d.name.toLowerCase().includes(q));
    const rows = shopItems === null ? `<div class="sh-sub">Loading dishes…</div>` : list.length ? list.slice(0, 80).map((d) => d.id === editId ? priceEditor(d) : `<div class="sh-row ${d.available ? "" : "off"}">
        <div class="sh-n">${d.custom ? "⭐ " : ""}${esc(d.name)}<span>${d.reason ? esc(d.reason) : (d.price !== d.original ? `<b class="chg">changed</b> was ${rs(d.original)}` : "")}</span></div>
        ${d.custom ? `<button class="sh-x" data-rm="${esc(d.id)}">Remove</button>` : `<button class="sh-p" data-edit="${esc(d.id)}">${rs(d.price)} ✎</button>`}
        ${d.reason ? "" : `<button class="sh-t ${d.available ? "on" : "so"}" data-so="${esc(d.id)}" data-now="${d.available ? 1 : 0}">${d.available ? "On" : "Sold out"}</button>`}
      </div>`).join("") + (list.length > 80 ? `<div class="sh-sub">Showing 80. Type to search.</div>` : "") : `<div class="sh-sub">No dish matches.</div>`;
    const changes = shopChanges.length ? `<div class="lane">PRICE CHANGES</div><div class="sh-card">${shopChanges.slice(0, 8).map((c) => `<div class="sh-ch">${esc(c.name)}${c.kind === "half" ? " (half)" : ""}: ${rs(c.old_price)} → <b>${rs(c.new_price)}</b><span>${day(utc(c.at))} ${time(utc(c.at))}</span></div>`).join("")}</div>` : "";
    return `<main class="home shop">
      <div class="lane">ONLINE ORDERS</div>${status}
      <div class="lane">ADD A SPECIAL</div>
      <div class="sh-card">
        <input class="sh-in" data-shop="name" placeholder="Dish name, e.g. Mango Lassi" value="${esc(shopDraft.name)}">
        <input class="sh-in" data-shop="price" inputmode="decimal" placeholder="Price in rupees" value="${esc(shopDraft.price)}">
        <label class="sh-chk"><input type="checkbox" data-shop="onlyToday" ${shopDraft.onlyToday ? "checked" : ""}> Only today</label>
        <button class="big" id="addspecial">Put on the menu</button>
        <div class="sh-sub">Customers can order it right away. It does not go to Petpooja, so ring it up on the POS yourself.</div>
      </div>
      <div class="lane">TODAY'S MENU</div>
      <input class="sh-in" data-shop="q" placeholder="Search a dish to mark sold out" value="${esc(shopQ)}">
      <div class="sh-list">${rows}</div>
      ${changes}
      <div class="lane">MORE</div>
      <a class="sh-link" href="/admin" target="_blank" rel="noopener">Reviews, discount codes and WhatsApp numbers ›</a>
    </main>`;
  }
  async function loadShop() {
    try { const r = await api("/api/admin/menu-lite"); shopItems = r.items; shopChanges = r.changes || []; } catch (e) { if (e.message !== "auth") toast("Couldn't load the menu."); shopItems = shopItems || []; }
    if (view === "shop") render();
  }

  function render() {
    const g = groups();
    const keepScroll = window.scrollY;
    let body;
    if (view === "focus") {
      const o = ((data && data.orders) || []).find((x) => x.id === focusOid);
      if (!o) { view = "home"; return render(); }
      const lbl = { new: "NEW", preparing: "COOKING", ready: "READY", out_for_delivery: "ON THE WAY", delivered: "DELIVERED", cancelled: "CANCELLED" }[o.status] || o.status;
      body = topbar(g, `#${o.id} · ${lbl}`, true) + banner(queue()) + `<main class="list">${card(o)}</main>`;
    } else if (view === "shop") body = topbar(g, "Shop", true) + shopView();
    else if (view === "together") body = topbar(g, "Cook together", true) + banner(queue()) + togetherView(g);
    else if (view === "later" || view === "done") {
      const list = g[view];
      body = topbar(g, view === "later" ? "Later" : "Done today", true) + banner(queue()) +
        `<main class="list">${list.length ? list.map(card).join("") : `<div class="empty">${view === "later" ? "No orders booked for later." : "Nothing finished yet today."}</div>`}</main>`;
    } else body = topbar(g, "", false) + homeView(g);
    app.innerHTML = body;
    window.scrollTo(0, keepScroll);
    bind();
  }

  function whenLine(o) {
    const placed = utc(o.created_at);
    if (o.scheduled_at) {
      const s = utc(o.scheduled_at);
      const win = WINDOWS[o.scheduled_window];
      return `<span class="later">For ${win ? win + ", " : time(s) + ", "}${day(s)}</span><div class="when">Placed ${time(placed)}, ${day(placed)}</div>`;
    }
    return `<div class="when">Placed ${time(placed)} · ${ago(placed)} · as soon as possible</div>`;
  }

  function billSec(o) {
    const sub = num(o.subtotal), disc = num(o.discount_total), pack = num(o.packing_fee), gst = num(o.gst_amount);
    const fee = num(o.delivery_fee), total = num(o.total), oldFare = num(o.delivery_fee_final);
    const kitchen = sub - disc + pack;
    let pay;
    if (o.payment_method === "upi" && o.paid) pay = `<div class="paybox ok">✓ Paid ${rs(total)} by UPI</div>`;
    else if (o.payment_method === "upi") pay = `<div class="paybox wait">UPI ${rs(total)}: not confirmed yet</div>`;
    else pay = `<div class="paybox cash">Cash: collect ${rs(Math.ceil(total))}<small>${o.order_type === "pickup" ? "At the counter when they pick up." : "Tell the Porter rider to collect it, or ask the customer to pay by UPI."}</small></div>`;
    const cost = num(o.courier_cost);
    const porterBox = o.order_type === "delivery"
      ? `<div><span>Delivery money (for Porter)</span><b>${rs(fee)}</b>${cost ? `<span>Porter cost ${rs(cost)} · ${cost > fee ? rs(cost - fee) + " extra from us" : rs(fee - cost) + " left over"}</span>` : ""}</div>` : "";
    return `<div class="sec"><h3>Bill</h3><div class="bill">
        ${(o.items || []).map((i) => `<div><span>${num(i.qty)} × ${esc(i.name)}</span><span>${rs(num(i.price) * num(i.qty))}</span></div>`).join("")}
        ${disc ? `<div class="minus"><span>Discount${o.discount_code ? " (" + esc(o.discount_code) + ")" : ""}</span><span>−${rs(disc)}</span></div>` : ""}
        ${pack ? `<div><span>Packing</span><span>${rs(pack)}</span></div>` : ""}
        ${gst ? `<div><span>GST 5%</span><span>${rs(gst)}</span></div>` : ""}
        ${o.order_type === "delivery" ? `<div><span>Delivery</span><span>${fee ? rs(fee) : "—"}</span></div>` : ""}
        <div class="tot"><span>Customer pays</span><span>${rs(total)}</span></div>
        ${oldFare ? `<div><span>+ Porter fare (old order)</span><span>${rs(oldFare)}</span></div>` : ""}
      </div>
      ${pay}
      <div class="split">
        <div><span>Kitchen keeps (food + packing)</span><b>${rs(kitchen)}</b></div>
        <div><span>GST to set aside</span><b>${rs(gst)}</b></div>
        ${porterBox || `<div><span>Delivery</span><b>Pickup</b></div>`}
      </div></div>`;
  }

  // Opens the Porter / Rapido app on Android (falls back to the Play Store page).
  // Neither app accepts a pre-filled drop location from outside, so the flow is:
  // copy the location here, open the app, paste it into the Drop box.
  const appLink = (pkg) => `intent://#Intent;package=${pkg};S.browser_fallback_url=${encodeURIComponent("https://play.google.com/store/apps/details?id=" + pkg)};end`;
  const PORTER_PKG = "in.porter.customerapp", RAPIDO_PKG = "com.rapido.passenger";

  function mapsLinks(o, addr, landmark) {
    const dlat = parseFloat(o.delivery_lat), dlng = parseFloat(o.delivery_lng);
    const k = (data && data.kitchen) || {};
    const klat = parseFloat(k.lat), klng = parseFloat(k.lng);
    const nearKitchen = isFinite(klat) && isFinite(dlat) && Math.abs(klat - dlat) < 0.0012 && Math.abs(klng - dlng) < 0.0012;
    const hasPin = isFinite(dlat) && isFinite(dlng) && !nearKitchen;
    const text = (addr + (landmark ? ", " + landmark : "")).trim();
    return { hasPin, drop: hasPin ? `https://www.google.com/maps/search/?api=1&query=${dlat},${dlng}` : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(text)}` };
  }

  function porterSec(o) {
    if (o.order_type !== "delivery") return "";
    const [addr, landmark] = splitAddr(o.delivery_address);
    const k = (data && data.kitchen) || {};
    const m = mapsLinks(o, addr, landmark);
    const booked = o.sr_tracking_url || o.sr_courier;
    const flag = o.address_flagged ? `<div class="flag">⚠ ${esc(o.address_flag_reason || "Check this address.")} If it looks wrong, call the customer before booking.</div>` : "";
    const noPin = m.hasPin ? "" : `<div class="flag">⚠ The customer did not drop an exact pin. Check the landmark or call before booking.</div>`;
    const head = `<div class="sec"><h3>Deliver to</h3><div class="addr">${esc(addr)}</div>
      ${landmark ? `<div class="hint">Landmark: ${esc(landmark)}</div>` : ""}
      <a class="maplink" href="${m.drop}" target="_blank" rel="noopener">See it on the map</a>${noPin}${flag}`;
    if (booked) {
      return head + `<div class="rider" style="margin-top:10px">🛵 ${esc(o.sr_courier || "Rider booked")}${o.sr_tracking_url ? ` · <a href="${esc(o.sr_tracking_url)}" target="_blank" rel="noopener">rider link</a>` : ""}</div></div>`;
    }
    if (!["preparing", "ready"].includes(o.status)) return head + `</div>`;
    const cashNote = o.payment_method === "upi" && o.paid ? "Already paid. Rider collects nothing."
      : o.payment_method === "upi" ? `UPI not confirmed yet. Check before the rider leaves.`
      : `Rider collects ${rs(Math.ceil(num(o.total)))} cash.`;
    const d = drafts[o.id] || {};
    const dropText = addr + (landmark ? " (Landmark: " + landmark + ")" : "");
    const row = (label, value, copyText, btn) => `<div class="step-row"><div class="sr-l">${label}</div><div class="sr-v">${esc(value)}</div>
      <button class="copy" data-copy="${esc(copyText)}">${btn}</button></div>`;
    return head + `</div><div class="sec porter"><h3>Book a rider</h3>
      <div class="how"><b>How:</b> tap each green <b>Copy</b> button, open the app, and paste it in the matching box.</div>
      <div class="openapps">
        <a class="openapp" href="${appLink(PORTER_PKG)}">Open Porter</a>
        <a class="openapp" href="${appLink(RAPIDO_PKG)}">Open Rapido</a>
      </div>
      ${row("1 · Pickup (kitchen)", k.address || "", k.address || "", "Copy pickup")}
      ${row("2 · Drop (customer)", dropText, m.hasPin ? m.drop : dropText, m.hasPin ? "Copy map location" : "Copy drop address")}
      ${m.hasPin ? row("   or the address as text", dropText, dropText, "Copy address") : ""}
      ${row("3 · Receiver", (o.customer_name || "Customer") + " · " + prettyPhone(o.customer_phone), phone10(o.customer_phone).slice(-10), "Copy number")}
      <div class="how" style="margin-top:12px"><b>Tell the rider:</b> ${cashNote}</div>
      <div class="how">After booking, tap <b>Share</b> in the app, copy the tracking link and paste it below.</div>
      <div class="field"><label for="link-${o.id}">Rider tracking link</label>
        <input id="link-${o.id}" data-draft="link" data-id="${o.id}" inputmode="url" placeholder="https://..." value="${esc(d.link || "")}"></div>
      <div class="field"><label for="fare-${o.id}">What the rider app charged (₹)</label>
        <input id="fare-${o.id}" data-draft="fare" data-id="${o.id}" inputmode="decimal" placeholder="e.g. 62" value="${esc(d.fare || "")}">
        <div class="hint">Only for our accounts. The customer already paid ${rs(o.delivery_fee)} for delivery and is not charged again.</div></div>
      <button class="big blue" data-dispatch="${o.id}" style="margin-top:12px">I booked the rider: send link to customer</button>
      <div class="tells">The customer gets: “Your order is on its way” with the rider's live link.</div>
    </div>`;
  }

  // Steps shown as a strip so it is obvious where the order is.
  function steps(o) {
    const del = o.order_type === "delivery";
    const names = del ? ["Accepted", "Cooking", "Rider on the way", "Delivered"] : ["Accepted", "Cooking", "Ready", "Picked up"];
    const idx = { new: 0, preparing: 1, ready: del ? 1 : 2, out_for_delivery: 2, delivered: 4 }[o.status];
    if (idx === undefined) return "";
    return `<div class="steps">${names.map((n, i) => `<div class="stp ${i < idx ? "done" : i === idx ? "now" : ""}"><i>${i < idx ? "✓" : i + 1}</i><span>${n}</span></div>`).join("")}</div>`;
  }

  // Money first: the one thing that blocks the owner.
  function payNow(o) {
    if (!needsMoney(o)) return "";
    const total = rs(o.total);
    return `<div class="paynow"><div class="pn-k">💰 CHECK THE MONEY FIRST</div>
      <div class="pn-t">Did ${esc(o.customer_name || "the customer")} pay ${total}?</div>
      <div class="pn-s">Open your UPI app (GPay / PhonePe). Look for <b>${total}</b>. Tap the green button only when you can see it.</div>
      <button class="big pay" data-paid="${o.id}" data-confirm="Tap again to confirm. The customer is told you got ${total}.">Yes, I got ${total}</button>
      <div class="pn-s" style="margin-top:8px">Not there after a few minutes? <a href="tel:+${waNum(o.customer_phone)}"><b>Call ${prettyPhone(o.customer_phone)}</b></a></div></div>`;
  }

  // One plain-language "what to do now" block with the single main button.
  function nextStep(o) {
    const del = o.order_type === "delivery";
    const btn = (st, label, cls) => `<button class="big ${cls || ""}" data-do="${st}" data-id="${o.id}">${label}</button>`;
    let k, t, b = "", warn = false;
    if (o.status === "new") {
      k = "NEXT"; t = "Accept this order and start cooking"; b = btn("preparing", "Accept order") + `<div class="tells">The customer gets: “The kitchen has accepted your order.”</div>`;
    } else if (o.status === "preparing") {
      k = "NEXT"; t = del ? "Cook it and pack it. Then tap below." : "Cook it. Tap below when it is packed.";
      b = btn("ready", del ? "Food is packed and ready" : "Food is ready for pickup") + `<div class="tells">${del ? "The customer gets: “Packed and ready. We're booking your rider.”" : "The customer gets: “Your order is ready to collect.”"}</div>`;
    } else if (o.status === "ready" && !del) {
      k = "WAITING"; t = "Waiting for the customer to come and collect it."; b = btn("delivered", "Customer has collected it ✓", "blue");
    } else if (o.status === "ready" && del) {
      k = "NEXT"; t = (o.sr_tracking_url || o.sr_courier) ? "Rider is booked. Waiting for pickup." : "Book a rider. Steps are in “Book a rider” below ⬇"; 
    } else if (o.status === "out_for_delivery") {
      k = "ON THE WAY"; warn = true; t = "The rider has the food. It has NOT reached the customer yet.";
      b = `<div class="warn">Tap the button below only AFTER the customer has the food in their hand.</div>` + btn("delivered", "Customer has received the food ✓", "blue") + `<div class="tells">Tapping this tells the customer “Delivered” and asks them to rate. You cannot undo it.</div>`;
    } else return "";
    return `<div class="next ${warn ? "warnbox" : ""}"><div class="nx-k">${k}</div><div class="nx-t">${t}</div>${b}</div>`;
  }

  function actions(o) {
    const minor = [`<a href="${esc(o.track_url)}" target="_blank" rel="noopener">Customer's tracking page</a>`];
    if (o.status === "ready" && o.order_type === "delivery") minor.push(`<button data-do="delivered" data-id="${o.id}" data-confirm="Mark delivered without a rider?">Delivered without a rider</button>`);
    if (["new", "preparing"].includes(o.status)) minor.push(`<button class="danger" data-do="cancelled" data-id="${o.id}" data-confirm="Cancel order #${o.id}? The customer is told.">Cancel order</button>`);
    return `<div class="act"><div class="minor">${minor.join("")}</div></div>`;
  }

  function card(o) {
    const label = { new: "NEW", preparing: "COOKING", ready: "READY", out_for_delivery: "ON THE WAY", delivered: "DELIVERED", cancelled: "CANCELLED" }[o.status] || o.status;
    const note = (o.instructions || "").trim();
    const itemNotes = (o.items || []).filter((i) => i.note).map((i) => `${esc(i.name)}: ${esc(i.note)}`);
    return `<article class="card s-${o.status}" id="o-${o.id}">
      <div class="sec"><div class="head"><div><div class="oid">#${o.id}</div>${whenLine(o)}</div>
        <div class="pills"><span class="pill p-${o.status}">${label}</span><span class="pill p-type">${o.order_type === "pickup" ? "PICKUP" : "DELIVERY"}</span></div></div></div>
      ${OPEN.includes(o.status) ? `<div class="sec nx-wrap">${steps(o)}${payNow(o)}${nextStep(o)}</div>` : ""}
      <div class="sec cust"><div><b>${esc(o.customer_name || "Customer")}</b><div class="ph">${prettyPhone(o.customer_phone)}</div></div>
        <div class="cbtns"><a href="tel:+${waNum(o.customer_phone)}">📞 Call</a><a href="https://wa.me/${waNum(o.customer_phone)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
      <div class="sec"><ul class="items">${(o.items || []).map((i) => `<li><span class="q">${num(i.qty)}×</span><span>${esc(i.name)}</span></li>`).join("")}</ul>
        ${offPos(o) ? `<div class="note">⭐ Has a special that is not on Petpooja. Ring it up on the POS yourself.</div>` : ""}
        ${note || itemNotes.length ? `<div class="note">📝 ${[esc(note), ...itemNotes].filter(Boolean).join("<br>")}</div>` : ""}</div>
      ${o.feedback ? `<div class="sec"><span class="pill ${o.feedback.rating === "bad" ? "p-cancelled" : o.feedback.rating === "good" ? "p-preparing" : "p-new"}">${{ good: "😋 Loved it", ok: "🙂 It was okay", bad: "😕 Not great" }[o.feedback.rating] || ""}</span>${o.feedback.comment ? `<div class="note" style="margin-top:8px">“${esc(o.feedback.comment)}”</div>` : ""}</div>` : ""}
      ${porterSec(o)}
      ${billSec(o)}
      ${OPEN.includes(o.status) ? actions(o) : `<div class="act"><div class="minor"><a href="${esc(o.track_url)}" target="_blank" rel="noopener">Customer's tracking page</a></div></div>`}
    </article>`;
  }

  // ---------- actions
  const pendingConfirm = {};
  function bind() {
    const pa = document.getElementById("payalert");
    if (pa) pa.onclick = () => { const o = groups().now.find(needsMoney); const el = o && document.getElementById("o-" + o.id); if (el) { el.scrollIntoView({ block: "start", behavior: "smooth" }); window.scrollBy(0, -150); el.classList.add("flash"); } };
    const go = (v, id) => { view = v; focusOid = id || null; render(); window.scrollTo(0, 0); };
    const bk = document.getElementById("back"); if (bk) bk.onclick = () => go("home");
    app.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => { go(b.dataset.view); if (b.dataset.view === "shop") loadShop(); });
    app.querySelectorAll("[data-shop]").forEach((i) => i.oninput = () => {
      const k = i.dataset.shop;
      if (k === "onlyToday") shopDraft.onlyToday = i.checked; else if (k === "q") { shopQ = i.value; const pos = i.selectionStart; render(); const n = app.querySelector('[data-shop="q"]'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } } else shopDraft[k] = i.value;
    });
    app.querySelectorAll("[data-close]").forEach((b) => b.onclick = () => {
      const [mode, h] = b.dataset.close.split(":");
      act(b, () => api("/api/admin/store", { method: "POST", body: JSON.stringify({ is_open: false, mode, hours: h ? +h : null, reason: shopDraft.reason }) }).then((r) => { shopDraft.reason = ""; if (data) data.store = r; }),
        "Online orders are off.");
    });
    app.querySelectorAll("[data-open-shop]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/store", { method: "POST", body: JSON.stringify({ is_open: true }) }).then((r) => { if (data) data.store = r; }), "Online orders are back on."));
    const sp = document.getElementById("addspecial");
    if (sp) sp.onclick = () => {
      const price = parseFloat(shopDraft.price);
      if (!shopDraft.name.trim()) { toast("Type the dish name."); return; }
      if (!(price > 0)) { toast("Type the price in rupees."); return; }
      act(sp, () => api("/api/admin/custom-items", { method: "POST", body: JSON.stringify({ name: shopDraft.name, price, only_today: shopDraft.onlyToday }) }).then(async () => { shopDraft.name = ""; shopDraft.price = ""; await loadShop(); }),
        "Added. Customers can order it now.");
    };
    app.querySelectorAll("[data-so]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/sold-out", { method: "POST", body: JSON.stringify({ item_id: b.dataset.so, sold_out: b.dataset.now === "1" }) }).then(loadShop),
      b.dataset.now === "1" ? "Marked sold out." : "Back on the menu."));
    app.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => { editId = b.dataset.edit; render(); const i = document.getElementById("pe-price"); if (i) { i.focus(); i.select(); } });
    app.querySelectorAll("[data-pe]").forEach((b) => b.onclick = () => {
      const id = b.dataset.id, d = (shopItems || []).find((x) => x.id === (id || editId));
      if (b.dataset.pe === "cancel") { editId = null; render(); return; }
      if (b.dataset.pe === "reset") {
        act(b, () => api("/api/admin/prices/" + encodeURIComponent(id), { method: "DELETE" }).then(async () => { editId = null; await loadShop(); }), "Back to the original price.");
        return;
      }
      const price = parseFloat(document.getElementById("pe-price").value);
      const hEl = document.getElementById("pe-half"), half = hEl ? parseFloat(hEl.value) : null;
      if (!(price > 0) || (hEl && !(half > 0))) { toast("Type the price in rupees."); return; }
      if (price === d.price && (!hEl || half === d.half)) { editId = null; render(); return; }
      if (!confirm(`Change ${d.name} from ${rs(d.price)} to ${rs(price)}${hEl && half !== d.half ? ` (half ${rs(half)})` : ""}? Customers see it right away.`)) return;
      act(b, () => api("/api/admin/prices", { method: "POST", body: JSON.stringify({ item_id: id, price, half_price: hEl ? half : null }) }).then(async () => { editId = null; await loadShop(); }), "Price changed.");
    });
    app.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/custom-items/" + encodeURIComponent(b.dataset.rm), { method: "DELETE" }).then(loadShop), "Removed."));
    app.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => go("focus", +b.dataset.open));
    const sm = document.getElementById("showmore"); if (sm) sm.onclick = () => { showMore = true; render(); };
    app.querySelectorAll("[data-plate]").forEach((b) => b.onclick = () => { const k = b.dataset.plate; plated.has(k) ? plated.delete(k) : plated.add(k); render(); });
    app.querySelectorAll("[data-accept]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); const id = b.dataset.accept;
      act(b, () => api(`/api/admin/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status: "preparing" }) }), `Order #${id} accepted. Customer told.`); });
    const toggle = () => { soundOn = !soundOn; try { localStorage.setItem(SOUND_KEY, soundOn ? "1" : "0"); } catch (_) {} unlockSound(); if (soundOn) ring(); render(); };
    const s1 = document.getElementById("sound"), s2 = document.getElementById("sound2");
    if (s1) s1.onclick = toggle; if (s2) s2.onclick = toggle;
    app.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied ✓"; b.classList.add("done"); toast("Copied. Now open the app and paste it."); }
      catch (_) { toast("Couldn't copy. Press and hold the text instead."); }
    });
    app.querySelectorAll("[data-draft]").forEach((i) => i.oninput = () => { (drafts[i.dataset.id] = drafts[i.dataset.id] || {})[i.dataset.draft] = i.value; });
    app.querySelectorAll('[data-do="delivered"]').forEach((b) => { if (!b.dataset.confirm) b.dataset.confirm = "1"; });
    app.querySelectorAll("[data-do]").forEach((b) => b.onclick = () => {
      const id = b.dataset.id, st = b.dataset.do;
      if (b.dataset.confirm && pendingConfirm[id + st] !== true) {
        pendingConfirm[id + st] = true; b.textContent = "Tap again to confirm"; b.classList.add("sure");
        setTimeout(() => { delete pendingConfirm[id + st]; }, 5000); return;
      }
      delete pendingConfirm[id + st];
      act(b, () => api(`/api/admin/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status: st }) }),
        { preparing: `Order #${id} accepted. Customer told.`, ready: `#${id} marked ready. Customer told.`, delivered: `#${id} done 🎉`, cancelled: `#${id} cancelled. Customer told.` }[st]);
    });
    app.querySelectorAll("[data-paid]").forEach((b) => b.onclick = () => {
      const id = b.dataset.paid;
      if (b.dataset.confirm && pendingConfirm[id + "paid"] !== true) {
        pendingConfirm[id + "paid"] = true; b.dataset.label = b.textContent; b.textContent = "Tap again to confirm"; b.classList.add("sure");
        setTimeout(() => { delete pendingConfirm[id + "paid"]; if (b.isConnected) { b.textContent = b.dataset.label; b.classList.remove("sure"); } }, 5000); return;
      }
      delete pendingConfirm[id + "paid"];
      act(b, () => api(`/api/admin/orders/${id}/paid`, { method: "POST" }), "Payment noted. Customer thanked.");
    });
    app.querySelectorAll("[data-dispatch]").forEach((b) => b.onclick = () => {
      const id = b.dataset.dispatch, d = drafts[id] || {};
      const link = (d.link || "").trim(), fare = parseFloat(d.fare);
      if (!link) { toast("Paste the Porter tracking link first."); document.getElementById("link-" + id)?.focus(); return; }
      if (!/^https?:\/\//.test(link)) { toast("The link should start with https://"); return; }
      act(b, () => api(`/api/admin/orders/${id}/manual-dispatch`, { method: "POST", body: JSON.stringify({ tracking_url: link, fare: fare > 0 ? fare : null }) }),
        "Rider link sent to the customer 🛵", () => { delete drafts[id]; });
    });
  }

  async function act(btn, call, okMsg, after) {
    btn.disabled = true; const old = btn.textContent; btn.textContent = "Saving…";
    try { await call(); if (after) after(); toast(okMsg || "Done"); await load(); }
    catch (e) { if (e.message !== "auth") { toast(e.message, 5000); btn.disabled = false; btn.textContent = old; } }
  }

  // ---------- polling
  async function load() {
    const fresh = await api("/api/admin/kitchen");
    const ids = new Set(fresh.orders.map((o) => o.id));
    const newOnes = seen ? fresh.orders.filter((o) => !seen.has(o.id) && o.status === "new") : [];
    const firstLoad = !seen;
    seen = ids; data = fresh;
    if (firstLoad && focusId) {
      if (fresh.orders.some((o) => o.id === focusId)) { view = "focus"; focusOid = focusId; }
      else toast(`Order #${focusId} isn't open any more.`);
      focusId = null;
    }
    const typing = document.activeElement && document.activeElement.matches("input[data-draft], input[data-shop], #pe-price, #pe-half");
    if (!typing) render();
    if (newOnes.length) {
      // One chime. The banner (render) shows the order; nothing takes over the screen.
      ring();
      document.title = "🔔 New order · Kitchen"; setTimeout(() => { document.title = "Kitchen"; }, 15000);
    }
  }
  setInterval(() => { const c = document.getElementById("clock"); if (c) c.textContent = clock(); }, 20000);
  let timer = null;
  function start() {
    app.innerHTML = `<div class="empty">Loading orders…</div>`;
    load().catch((e) => { if (e.message !== "auth") app.innerHTML = `<div class="empty">Couldn't load orders. Check the internet and pull down to retry.</div>`; });
    clearInterval(timer);
    timer = setInterval(() => { if (token) load().catch(() => {}); }, 15000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && token) load().catch(() => {}); });
  document.addEventListener("click", unlockSound, { once: true });

  if (token) start(); else renderLogin();
})();
