// Kitchen v2: the owner's screen. Three tabs (Orders, Menu, Shop), one order list,
// one order page with one big button. Every button that changes an order also
// messages the customer on WhatsApp (server side).
(function () {
  const TOKEN_KEY = "admin_token", SOUND_KEY = "kitchen_sound";
  const app = document.getElementById("app"), bannerEl = document.getElementById("banner");
  let token = null, data = null, seen = null, soundOn = false, audio = null;
  let tab = "orders", oid = null, sheet = null, showDone = false, lastOk = 0;
  let shopItems = null, shopChanges = [], shopQ = "", editId = null;
  let claimed = null;  // orders whose customer has tapped "I've paid"
  const openGroups = new Set();  // Menu tab: which sections are unfolded
  // The "add a special" form. photo is a shrunken JPEG blob, review holds the spelling check result.
  const special = { open: false, name: "", price: "", onlyToday: true, photo: null, photoUrl: "", review: null, busy: false };
  let photoTarget = null;  // dish id the next picked picture is for
  const shopDraft = { reason: "" };
  const drafts = {};  // per-order rider paste/fare typed but not yet sent
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
  const firstName = (o) => String(o.customer_name || "the customer").trim().split(/\s+/)[0];
  const WINDOWS = { lunch: "Lunch (12:00–2:30)", dinner: "Dinner (6:30–9:30)" };
  const STATUS = { new: "New", preparing: "Cooking", ready: "Packed", out_for_delivery: "On the way", delivered: "Delivered", cancelled: "Cancelled" };
  const OPEN = ["new", "preparing", "ready", "out_for_delivery"];
  // 2 when the customer has tapped "I've paid" (look at these first), 1 when still waiting, 0 when nothing to check.
  const needsMoney = (o) => (o.payment_method === "upi" && !o.paid && o.status !== "cancelled" && o.status !== "delivered") ? (o.paid_claimed_at ? 2 : 1) : 0;
  const offPos = (o) => (o.items || []).some((i) => String(i.item_id || "").startsWith("sp-"));
  const ICON = {
    orders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 4h14v16H5zM9 9h6M9 13h6M9 17h3"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
    shop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M10 20v-6h4v6"/></svg>',
  };

  function toast(msg, ms) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), ms || 3500);
  }

  // One 401 can be a blip: retry once before treating it as a wrong password.
  async function api(url, opts, retried) {
    opts = opts || {};
    const res = await fetch(url, { ...opts, headers: { "X-Admin-Token": token, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (res.status === 401) {
      if (!retried) { await new Promise((r) => setTimeout(r, 800)); return api(url, opts, true); }
      try { localStorage.removeItem(TOKEN_KEY); } catch (_) {}
      token = null; renderLogin("That password didn't work. Try again."); throw new Error("auth");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Something went wrong. Try again.");
    return body;
  }

  // ---------- sound (phones only allow it after a tap)
  function unlockSound() { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (_) {} }
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

  // ---------- rider link and address cleaning (mirrors app/links.py)
  function cleanLink(text) {
    const t = (text || "").trim();
    if (!t) return { state: "empty" };
    const norm = t.slice(0, 5000).replace(/[​-‍﻿]/g, "").replace(/[“”‘’]/g, '"').replace(/\s*\n\s*/g, " ");
    const re = /(?:https?:\/\/|www\.)[^\s<>"'()\[\]]+|\b(?:[a-z0-9-]+\.)+(?:in|com|bike|app|co)\/[^\s<>"'()\[\]]+/gi;
    const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } };
    const found = (norm.match(re) || []).map((u) => u.replace(/[.,;:!?)\]]+$/, "")).map((u) => u.replace(/^http:\/\//i, "https://")).map((u) => /^https:\/\//i.test(u) ? u : "https://" + u)
      .map((u) => { if (!/(^|\.)porter\.in$/.test(host(u))) return u; try { const x = new URL(u); [...x.searchParams.keys()].forEach((k) => { if (/^utm_/i.test(k)) x.searchParams.delete(k); }); return x.toString(); } catch (e) { return u; } });
    const real = found.filter((u) => host(u) && !/(^|\.)(play\.google\.com|apps\.apple\.com|itunes\.apple\.com)$/.test(host(u)));
    const best = real.find((u) => /(^|\.)(porter\.in|rapido\.bike|rapido\.in|borzodelivery\.com)$/.test(host(u)));
    if (best) return { state: "ok", url: best };
    if (real.length) return { state: "unknown", url: real[0] };
    return { state: found.length ? "store" : "none" };
  }
  // The rider apps' Drop box is a map search: flat numbers, floors and landmarks go in the phone call.
  function splitDrop(raw) {
    let s = (raw || "").trim(), landmark = "";
    const m = /\(\s*landmark:\s*(.*?)\)\s*$/i.exec(s);
    if (m) { landmark = m[1].trim(); s = s.slice(0, m.index).trim(); }
    s = s.replace(/\([^)]*\)/g, " ");
    const parts = s.split(",").map((x) => x.trim()).filter(Boolean);
    const unit = /^(flat|door|house|plot|apt|apartment|block|tower|floor|\d+(st|nd|rd|th)\s+floor|no\.?|#)\b|^[\w#\/-]{1,5}$|^\d+[a-z]?([\/-]\d+[a-z]?)?$/i;
    const details = [];
    while (parts.length > 1 && unit.test(parts[0])) details.push(parts.shift());
    const kept = [];
    parts.forEach((p) => { if (/\b(near|opp|opposite|behind|beside|next to|above|below)\b/i.test(p)) landmark = landmark || p; else kept.push(p); });
    let search = kept.join(", ");
    return { search, details: details.join(", "), landmark };
  }
  function pinOf(o) {
    const dlat = parseFloat(o.delivery_lat), dlng = parseFloat(o.delivery_lng);
    const k = (data && data.kitchen) || {}, klat = parseFloat(k.lat), klng = parseFloat(k.lng);
    const near = isFinite(klat) && isFinite(dlat) && Math.abs(klat - dlat) < 0.0012 && Math.abs(klng - dlng) < 0.0012;
    return isFinite(dlat) && isFinite(dlng) && !near ? { lat: dlat, lng: dlng, url: `https://www.google.com/maps/search/?api=1&query=${dlat},${dlng}` } : null;
  }
  const appLink = (pkg) => `intent://#Intent;package=${pkg};S.browser_fallback_url=${encodeURIComponent("https://play.google.com/store/apps/details?id=" + pkg)};end`;
  const PORTER_PKG = "in.porter.customerapp", RAPIDO_PKG = "com.rapido.passenger";

  // ---------- grouping
  function isLater(o) { return !!o.scheduled_at && o.status === "new" && istDay(utc(o.scheduled_at)) > istDay(new Date()); }
  function dueOf(o) {
    if (o.scheduled_at) return utc(o.scheduled_at);
    return new Date((utc(o.created_at) || new Date()).getTime() + (o.order_type === "pickup" ? 25 : 45) * 60000);
  }
  function groups() {
    const all = (data && data.orders) || [], today = istDay(new Date());
    const now = all.filter((o) => OPEN.includes(o.status) && !isLater(o));
    const later = all.filter((o) => OPEN.includes(o.status) && isLater(o));
    const done = all.filter((o) => !OPEN.includes(o.status) && istDay(utc(o.created_at)) === today);
    const rank = { new: 0, preparing: 1, ready: 2, out_for_delivery: 3 };
    now.sort((a, b) => (needsMoney(b) - needsMoney(a)) || rank[a.status] - rank[b.status] || (dueOf(a) - dueOf(b)) || a.id - b.id);
    later.sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
    return { now, later, done };
  }
  const summaryItems = (o) => (o.items || []).map((i) => `${num(i.qty)} × ${i.name}`).join(", ");

  // ---------- login
  function renderLogin(err) {
    bannerEl.innerHTML = "";
    app.innerHTML = `<form class="login" id="login"><h1>Kitchen</h1>
      <div>Enter the kitchen password once. This phone remembers it.</div>
      <input class="in" id="pw" type="password" autocomplete="current-password" placeholder="Kitchen password" required>
      ${err ? `<div class="flag">${esc(err)}</div>` : ""}
      <button class="big" type="submit">Open the kitchen</button></form>`;
    document.getElementById("login").addEventListener("submit", (e) => {
      e.preventDefault(); token = document.getElementById("pw").value.trim();
      try { localStorage.setItem(TOKEN_KEY, token); } catch (_) {}
      unlockSound(); start();
    });
  }

  // ---------- banner (outside the re-rendered view, so it shows while she types)
  function renderBanner() {
    const stale = token && lastOk && Date.now() - lastOk > 45000;
    const conn = !token ? "" : stale
      ? `<div class="conn bad" role="alert">No internet. Orders may be missing.</div>`
      : lastOk ? `<div class="conn">Internet OK · updated ${ago(new Date(lastOk))}</div>` : "";
    const fresh = ((data && data.orders) || []).filter((o) => o.status === "new" && !isLater(o) && !(tab === "orders" && oid === o.id));
    let ban = "";
    if (fresh.length) {
      const o = fresh[fresh.length - 1];
      ban = `<div class="newban" role="status"><div class="nb-t" data-open="${o.id}">
        <div class="nb-k">🔔 New order #${o.id} · ${o.order_type === "pickup" ? "Pickup" : "Delivery"} ${time(dueOf(o))}${fresh.length > 1 ? ` · +${fresh.length - 1} more` : ""}</div>
        <div class="nb-i">${esc(summaryItems(o))}</div></div>
        <button class="nb-b" data-accept="${o.id}">Accept</button></div>`;
    }
    bannerEl.innerHTML = conn + ban;
    bannerEl.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openOrder(+b.dataset.open));
    bannerEl.querySelectorAll("[data-accept]").forEach((b) => b.onclick = () => {
      const id = b.dataset.accept;
      act(b, () => api(`/api/admin/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status: "preparing" }) }), `Order #${id} accepted. Customer told.`);
    });
  }

  // ---------- shell
  function topbar(title, back) {
    return `<header class="top">${back ? `<button class="back" id="back" aria-label="Back">‹ Back</button>` : ""}
      <span class="ttl">${esc(title)}</span>
      <button class="sound ${soundOn ? "on" : "off"}" id="sound" aria-label="${soundOn ? "Sound on" : "Sound off"}">${soundOn ? "🔔" : "🔕 Sound off"}</button></header>`;
  }
  function tabbar(g) {
    const closed = data && data.store && !data.store.is_open;
    const t = (k, label, extra, cls) => `<button data-tab="${k}" class="${tab === k ? "on" : ""} ${cls || ""}" aria-label="${label}">${ICON[k]}<span>${label}${extra || ""}</span></button>`;
    return `<nav class="tabs">${t("orders", "Orders", g.now.length ? `<span class="dot">${g.now.length}</span>` : "")}${t("menu", "Menu")}${t("shop", closed ? "Closed" : "Shop", "", closed ? "shut" : "")}</nav>`;
  }
  function closedStrip() {
    const st = data && data.store;
    if (!st || st.is_open) return "";
    return `<button class="strip red" data-tab="shop">Online orders are OFF. Tap to change.</button>`;
  }

  // ---------- orders list
  function ordersView(g) {
    const rows = (list) => list.map((o) => {
      const note = (o.instructions || "").trim();
      return `<button class="ocard ${o.status}" data-open="${o.id}">
        <div class="oc-h"><span class="oc-id">#${o.id} · ${o.order_type === "pickup" ? "Pickup" : "Delivery"}</span><span class="oc-st ${o.status}">${STATUS[o.status] || o.status}</span></div>
        <div class="oc-i">${esc(summaryItems(o))}</div>
        <div class="oc-m">${esc(firstName(o))} · due ${time(dueOf(o))} · ${o.payment_method === "upi" ? (o.paid ? "Paid " : "UPI ") : "Cash "}${rs(o.total)}</div>
        ${needsMoney(o) ? `<span class="chipwarn">${o.paid_claimed_at ? "Says they paid" : "Check payment"}</span> ` : ""}${offPos(o) ? `<span class="chipwarn">Ring up on POS</span> ` : ""}${note ? `<span class="chipwarn">Has a note</span>` : ""}
      </button>`;
    }).join("");
    return `${topbar("Orders")}<main class="list">${closedStrip()}
      ${g.now.length ? rows(g.now) : `<div class="empty">No orders right now.<br>New ones appear here by themselves.</div>`}
      ${g.later.length ? `<div class="lane">Later (${g.later.length})</div>${rows(g.later)}` : ""}
      ${g.done.length ? `<button class="linkbtn" id="toggledone">${showDone ? "Hide" : "Show"} done today (${g.done.length})</button>${showDone ? rows(g.done) : ""}` : ""}
    </main>${tabbar(g)}`;
  }

  // ---------- one order page
  function whenLine(o) {
    const placed = utc(o.created_at);
    if (o.scheduled_at) { const s = utc(o.scheduled_at), win = WINDOWS[o.scheduled_window]; return `For ${win ? win + ", " : time(s) + ", "}${day(s)} · placed ${time(placed)}`; }
    return `Placed ${time(placed)} · ${ago(placed)} · as soon as possible`;
  }
  function moneyLine(o) {
    const total = rs(o.total);
    if (o.payment_method === "upi" && o.paid) return `<div class="money ok">✓ Paid ${total} by UPI</div>`;
    if (o.payment_method === "upi") return `<div class="money">UPI ${total}: not confirmed yet</div>`;
    return `<div class="money">Cash: collect ${rs(Math.ceil(num(o.total)))}${o.order_type === "pickup" ? " at the counter" : ". The rider collects it."}</div>`;
  }
  function payCard(o) {
    if (!needsMoney(o)) return "";
    const total = rs(o.total);
    return `<div class="box paycard"><div class="q">${o.paid_claimed_at ? `${esc(firstName(o))} says they paid ${total}.` : `Did ${esc(firstName(o))} pay ${total}?`}</div>
      <div class="s">Open GPay or PhonePe and look for <b>${total}</b>. Tap green only when you can see it.</div>
      <button class="big" data-paid="${o.id}">Yes, I got ${total}</button>
      <button class="big ghost" data-paylink="${o.id}">Send payment link on WhatsApp</button>
      <button class="btn" data-copypay="${o.id}">Copy the link</button>
      <div class="s">Not there after a few minutes? <a href="tel:+${waNum(o.customer_phone)}"><b>Call ${prettyPhone(o.customer_phone)}</b></a></div></div>`;
  }
  function riderBox(o) {
    if (o.order_type !== "delivery" || !OPEN.includes(o.status)) return "";
    const sp = splitDrop(o.delivery_address), pin = pinOf(o);
    const flag = o.address_flagged ? `<div class="flag">${esc(o.address_flag_reason || "Check this address.")} If it looks wrong, call the customer before booking.</div>` : "";
    const noPin = pin ? "" : `<div class="warnc">The customer did not drop an exact pin. Check the landmark or call before booking.</div>`;
    const detail = [sp.details, sp.landmark ? "near " + sp.landmark.replace(/^(near|opp|opposite)\s+/i, "") : ""].filter(Boolean).join(" · ");
    if (o.sr_tracking_url || o.sr_courier) {
      return `<div class="box"><h3>Rider</h3><div class="good">🛵 ${esc(o.sr_courier || "Rider booked")}${o.sr_tracking_url ? ` · <a href="${esc(o.sr_tracking_url)}" target="_blank" rel="noopener">rider link</a>` : ""}</div>
        <div class="drop">${esc(sp.search)}</div>${pin ? `<a href="${pin.url}" target="_blank" rel="noopener">See it on the map</a>` : ""}</div>`;
    }
    const d = drafts[o.id] || {}, c = cleanLink(d.link);
    const found = c.state === "ok" ? `<div class="found ok">✓ Found the rider link</div>`
      : c.state === "unknown" ? `<div class="found unknown">Found a link, but not a Porter or Rapido one. Check it.</div>`
      : c.state === "store" ? `<div class="found bad">That is the app-store link, not the rider link.</div>`
      : c.state === "none" ? `<div class="found bad">No link in that yet.</div>` : "";
    const cashNote = o.payment_method === "upi" && o.paid ? "Already paid. Rider collects nothing."
      : o.payment_method === "upi" ? "UPI not confirmed yet. Check before the rider leaves." : `Rider collects ${rs(Math.ceil(num(o.total)))} cash.`;
    return `<div class="box"><h3>Book a rider</h3>
      <div class="step"><div class="n">1</div><div><div class="t">Drop address</div><div class="drop">${esc(sp.search)}</div>
        ${detail ? `<div class="d">Tell the rider on the phone: ${esc(detail)}</div>` : ""}</div></div>
      ${noPin}${flag}
      <button class="big" data-bookapp="${o.id}" data-pkg="porter">Copy address and open Porter</button>
      <button class="linkbtn" data-bookapp="${o.id}" data-pkg="rapido">Use Rapido instead</button>
      <div class="step"><div class="n">2</div><div><div class="t">Paste it in Drop, book, then tap Share and Copy</div><div class="d">${esc(cashNote)}</div></div></div>
      <div class="step"><div class="n">3</div><div style="width:100%"><div class="t">Paste what you copied</div></div></div>
      <div class="field"><input id="link-${o.id}" data-draft="link" data-id="${o.id}" inputmode="url" placeholder="Paste here" value="${esc(d.link || "")}" aria-label="Rider message">
        <div id="found-${o.id}">${found}</div></div>
      <button class="btn" data-readclip="${o.id}">Paste for me</button>
      <div class="field"><label for="fare-${o.id}">What the rider app charged (₹, optional)</label>
        <input id="fare-${o.id}" data-draft="fare" data-id="${o.id}" inputmode="decimal" placeholder="e.g. 62" value="${esc(d.fare || "")}"></div>
      <button class="big blue" id="send-${o.id}" data-dispatch="${o.id}" ${c.state === "ok" || c.state === "unknown" ? "" : "disabled"}>Send link to ${esc(firstName(o))}</button>
      <div class="tells">${firstName(o)} gets “Your order is on its way” with the rider's live link.</div></div>`;
  }
  function nextButton(o) {
    const del = o.order_type === "delivery", id = o.id;
    if (o.status === "new") return `<button class="big" data-do="preparing" data-id="${id}">Accept order</button><div class="tells">${esc(firstName(o))} gets: “The kitchen has accepted your order.”</div>`;
    if (o.status === "preparing") return `<button class="big" data-do="ready" data-id="${id}">${del ? "Food is packed" : "Food is ready for pickup"}</button><div class="tells">${del ? "The customer gets: “Packed and ready. We're booking your rider.”" : "The customer gets: “Your order is ready to collect.”"}</div>`;
    if (o.status === "ready" && !del) return `<button class="big blue" data-ask="delivered" data-id="${id}">Customer has collected it</button>`;
    if (o.status === "ready" && del) return (o.sr_tracking_url || o.sr_courier) ? `<div class="warnc">Rider is booked. Waiting for pickup.</div>` : `<div class="warnc">Food is packed. Book the rider below.</div>`;
    if (o.status === "out_for_delivery") return `<div class="warnc">The rider has the food. It has NOT reached the customer yet.</div><button class="big blue" data-ask="delivered" data-id="${id}">Customer has the food</button>`;
    return "";
  }
  function orderPage(o, g) {
    const note = (o.instructions || "").trim();
    const itemNotes = (o.items || []).filter((i) => i.note).map((i) => `${esc(i.name)}: ${esc(i.note)}`);
    const open = OPEN.includes(o.status);
    return `${topbar("Order #" + o.id, true)}<main class="page">
      <div class="box"><div class="status ${o.status}">${STATUS[o.status] || o.status} · ${o.order_type === "pickup" ? "Pickup" : "Delivery"}</div>
        <div class="when">${esc(whenLine(o))}</div>
        ${offPos(o) ? `<div class="note">⭐ A special that is not on Petpooja. Ring it up on the POS yourself.</div>` : ""}
        <ul class="items">${(o.items || []).map((i) => `<li><span class="q">${num(i.qty)}×</span><span>${esc(i.name)}</span></li>`).join("")}</ul>
        ${note || itemNotes.length ? `<div class="note">📝 ${[esc(note), ...itemNotes].filter(Boolean).join("<br>")}</div>` : ""}
        ${moneyLine(o)}
        ${open ? nextButton(o) : ""}</div>
      ${payCard(o)}
      <div class="box cust"><div><b>${esc(o.customer_name || "Customer")}</b><div class="ph">${prettyPhone(o.customer_phone)}</div></div>
        <div class="cbtns"><a href="tel:+${waNum(o.customer_phone)}">Call</a><a href="https://wa.me/${waNum(o.customer_phone)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
      ${riderBox(o)}
      ${o.feedback ? `<div class="box"><b>${{ good: "😋 Loved it", ok: "🙂 It was okay", bad: "😕 Not great" }[o.feedback.rating] || ""}</b>${o.feedback.comment ? `<div>“${esc(o.feedback.comment)}”</div>` : ""}</div>` : ""}
      <a class="linkbtn" style="text-align:center" href="${esc(o.track_url)}" target="_blank" rel="noopener">Customer's tracking page</a>
      ${["new", "preparing"].includes(o.status) ? `<button class="linkbtn red" data-ask="cancelled" data-id="${o.id}">Cancel this order</button>` : ""}
      ${o.status === "ready" && o.order_type === "delivery" ? `<button class="linkbtn" data-ask="delivered" data-id="${o.id}">Delivered without a rider</button>` : ""}
    </main>${tabbar(g)}`;
  }

  // ---------- menu + shop
  function priceEditor(d) {
    return `<div class="box"><div class="dn">${esc(d.name)}<span>Base price ${rs(d.original)}${d.original_half ? ` · half ${rs(d.original_half)}` : ""}</span></div>
      <div class="field"><label for="pe-price">Price</label><input id="pe-price" inputmode="decimal" value="${d.price}"></div>
      ${d.half ? `<div class="field"><label for="pe-half">Half portion</label><input id="pe-half" inputmode="decimal" value="${d.half}"></div>` : ""}
      <div class="grid2"><button class="big ghost" data-pe="cancel">Cancel</button><button class="big" data-pe="save" data-id="${esc(d.id)}">Save price</button></div>
      ${d.price !== d.original || (d.half && d.half !== d.original_half) ? `<button class="btn" data-pe="reset" data-id="${esc(d.id)}">Back to ${rs(d.original)}</button>` : ""}
</div>`;
  }
  function dishCard(d) {
    if (d.id === editId) return priceEditor(d);
    const pic = d.photo
      ? `<button class="ph" data-photo="${esc(d.id)}" aria-label="Change the photo of ${esc(d.name)}"><img src="${esc(d.photo)}" alt="" loading="lazy"><i>Change</i></button>`
      : `<button class="ph none" data-photo="${esc(d.id)}" aria-label="Add a photo of ${esc(d.name)}"><b>＋</b>Photo</button>`;
    const note = d.reason ? esc(d.reason) : d.price !== d.original ? `Changed, was ${rs(d.original)}` : "";
    return `<article class="dish2 ${d.available ? "" : "off"}">
      <div class="drow">${pic}<div class="dn">${d.custom ? "⭐ " : ""}${esc(d.name)}${note ? `<span>${note}</span>` : ""}</div></div>
      <div class="dacts">
        ${d.custom ? `<button class="price" data-rm="${esc(d.id)}">Remove</button>` : `<button class="price" data-edit="${esc(d.id)}" aria-label="Change price of ${esc(d.name)}">${rs(d.price)} ✎</button>`}
        ${d.reason ? "" : `<button class="tog ${d.available ? "on" : "so"}" data-so="${esc(d.id)}" data-now="${d.available ? 1 : 0}">${d.available ? "On" : "Sold out"}</button>`}
      </div></article>`;
  }
  function specialForm() {
    const r = special.review;
    const pic = special.photoUrl
      ? `<button class="sp-photo has" data-photo="__special" aria-label="Change the photo"><img src="${esc(special.photoUrl)}" alt=""><i>Change photo</i></button>`
      : `<button class="sp-photo" data-photo="__special"><b>📷</b>Add a photo<span>Take one now, or pick from your phone</span></button>`;
    const review = !r ? "" : `<div class="review">
      ${r.suggestions.length ? `<div class="rq">Did you mean <b>“${esc(r.corrected)}”</b>?</div>
        <button class="big" data-sp="use-fix">Yes, use “${esc(r.corrected)}”</button>
        <button class="big ghost" data-sp="keep">No, keep “${esc(r.clean)}”</button>` : ""}
      ${r.duplicate && !r.suggestions.length ? `<div class="rq">“${esc(r.duplicate.name)}” is already on the menu.</div>
        <button class="big" data-sp="use-existing">Show me that one</button>
        <button class="big ghost" data-sp="keep">Add mine anyway</button>` : ""}</div>`;
    return `<div class="box sp"><h3>New special</h3>${pic}
      <div class="field"><label for="sp-name">Dish name</label><input id="sp-name" class="in" data-sp-in="name" autocapitalize="words" autocomplete="off" placeholder="e.g. Mango Lassi" value="${esc(special.name)}"></div>
      <div class="field"><label for="sp-price">Price (₹)</label><input id="sp-price" class="in" data-sp-in="price" inputmode="decimal" placeholder="e.g. 120" value="${esc(special.price)}"></div>
      <label class="chk"><input type="checkbox" data-sp-in="onlyToday" ${special.onlyToday ? "checked" : ""}> Only today</label>
      ${review}
      ${r ? "" : `<div class="grid2"><button class="big ghost" data-sp="cancel">Cancel</button><button class="big" id="addspecial" ${special.busy ? "disabled" : ""}>${special.busy ? "Saving…" : "Put on menu"}</button></div>`}
      <div class="tells">Customers can order it straight away. It does not go to Petpooja, so ring it up on the POS yourself.</div></div>`;
  }
  function menuView(g) {
    const q = shopQ.trim().toLowerCase(), all = shopItems || [];
    const specials = all.filter((d) => d.custom);
    const regular = all.filter((d) => !d.custom && (!q || d.name.toLowerCase().includes(q) || (d.group || "").toLowerCase().includes(q)));
    const groupsOrder = [...new Set(regular.map((d) => d.group))];
    const body = shopItems === null ? `<div class="empty">Loading dishes…</div>` : groupsOrder.length ? groupsOrder.map((gr) => {
      const rows = regular.filter((d) => d.group === gr), open = q || openGroups.has(gr);
      return `<section class="grp"><button class="gh" data-grp="${esc(gr)}" aria-expanded="${open ? "true" : "false"}"><span>${esc(gr)}</span><em>${rows.length}</em><i>${open ? "▲" : "▼"}</i></button>${open ? rows.map(dishCard).join("") : ""}</section>`;
    }).join("") : `<div class="empty">No dish matches “${esc(shopQ)}”.</div>`;
    const spBlock = special.open ? specialForm() : `<div class="box sp"><button class="big" data-sp="open">＋ Add today's special</button>
      ${specials.length ? `<div class="spl">${specials.map(dishCard).join("")}</div>` : `<div class="tells">Nothing special added yet.</div>`}</div>`;
    return `${topbar("Menu")}<main class="list menu">${spBlock}
      <input class="in search" data-shop="q" placeholder="Search a dish" value="${esc(shopQ)}" aria-label="Search a dish" autocomplete="off">
      <div class="tells hint">Tap a picture to add or change it. Tap the price to change it.</div>
      ${body}</main>${tabbar(g)}<input type="file" id="photo-input" accept="image/*" hidden>`;
  }
  function shopView(g) {
    const st = (data && data.store) || { is_open: true };
    const back = st.turn_on_time ? new Date(String(st.turn_on_time).includes("T") ? st.turn_on_time + "+05:30" : st.turn_on_time.replace(" ", "T") + "+05:30") : null;
    const status = st.is_open
      ? `<div class="shop-state on">Taking online orders</div>
         <input class="in" data-shop="reason" placeholder="Message for customers (optional)" value="${esc(shopDraft.reason)}">
         <div class="grid2"><button class="btn" data-close="hours:1">Close for 1 hour</button><button class="btn" data-close="hours:2">Close for 2 hours</button>
           <button class="btn" data-close="today">Rest of today</button><button class="btn" data-close="manual">Until I open</button></div>`
      : `<div class="shop-state off">Online orders are OFF</div>
         <div>${esc((st.reason || "").replace(/([^.!?])$/, "$1."))}${back && !isNaN(back) ? ` Opens again ${day(back)} ${time(back)}.` : ""}</div>
         <button class="big" data-open-shop="1">Open the shop now</button>`;
    const changes = shopChanges.length ? `<div class="box"><h3>Price changes</h3>${shopChanges.slice(0, 8).map((c) => `<div class="chg">${esc(c.name)}${c.kind === "half" ? " (half)" : ""}: ${rs(c.old_price)} → <b>${rs(c.new_price)}</b><span>${day(utc(c.at))} ${time(utc(c.at))}</span></div>`).join("")}</div>` : "";
    return `${topbar("Shop")}<main class="page"><div class="box">${status}</div>${changes}
      <a class="sh-link" href="/admin" target="_blank" rel="noopener">Reviews, discount codes and WhatsApp numbers ›</a></main>${tabbar(g)}`;
  }
  async function loadShop() {
    try { const r = await api("/api/admin/menu-lite"); shopItems = r.items; shopChanges = r.changes || []; } catch (e) { if (e.message !== "auth") toast("Couldn't load the menu."); shopItems = shopItems || []; }
    if (tab === "menu" || tab === "shop") render();
  }

  // ---------- sheet (replaces confirm() and tap-twice)
  function sheetHtml() {
    if (!sheet) return "";
    return `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true"><h2>${esc(sheet.title)}</h2>${sheet.body ? `<p>${esc(sheet.body)}</p>` : ""}
      <button class="big ${sheet.cls || ""}" id="sheet-yes">${esc(sheet.yes)}</button><button class="big ghost" id="sheet-no">${esc(sheet.no || "Not yet")}</button></div></div>`;
  }
  function ask(s) { sheet = s; render(); }

  // ---------- render
  function render() {
    const g = groups(), keep = window.scrollY;
    let body;
    if (tab === "orders" && oid) {
      const o = ((data && data.orders) || []).find((x) => x.id === oid);
      if (!o) { oid = null; return render(); }
      body = orderPage(o, g);
    } else if (tab === "menu") body = menuView(g);
    else if (tab === "shop") body = shopView(g);
    else body = ordersView(g);
    app.innerHTML = body + sheetHtml();
    window.scrollTo(0, keep);
    bind(); renderBanner();
  }
  function openOrder(id) { tab = "orders"; oid = id; sheet = null; render(); window.scrollTo(0, 0); }
  function goTab(t) { tab = t; oid = null; sheet = null; if (t !== "orders" && shopItems === null) loadShop(); else if (t !== "orders") loadShop(); render(); window.scrollTo(0, 0); }

  // ---------- actions
  const DONE_MSG = (id) => ({ preparing: `Order #${id} accepted. Customer told.`, ready: `#${id} marked packed. Customer told.`, delivered: `#${id} done 🎉`, cancelled: `#${id} cancelled. Customer told.` });
  function setStatus(btn, id, st) { return act(btn, () => api(`/api/admin/orders/${id}/status`, { method: "POST", body: JSON.stringify({ status: st }) }), DONE_MSG(id)[st]); }
  // ---------- photos and specials
  // Shrink the picture on the phone first: a camera photo is several MB, the menu needs about 100 KB.
  async function shrink(file) {
    let bmp;
    try { bmp = await createImageBitmap(file, { imageOrientation: "from-image" }); }
    catch (_) {
      bmp = await new Promise((ok, no) => { const im = new Image(), u = URL.createObjectURL(file); im.onload = () => ok(im); im.onerror = no; im.src = u; });
    }
    const w = bmp.width || bmp.naturalWidth, h = bmp.height || bmp.naturalHeight, k = Math.min(1, 1200 / Math.max(w, h));
    const c = document.createElement("canvas"); c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return new Promise((ok, no) => c.toBlob((b) => b ? ok(b) : no(new Error("shrink")), "image/jpeg", 0.82));
  }
  async function uploadPhoto(id, blob) {
    const res = await fetch("/api/admin/photos/" + encodeURIComponent(id), { method: "POST", headers: { "X-Admin-Token": token, "Content-Type": "image/jpeg" }, body: blob });
    if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.detail || "Couldn't save the photo."); }
  }
  async function takePhoto(file) {
    try {
      toast("Getting the photo ready…");
      const blob = await shrink(file);
      if (photoTarget === "__special") { special.photo = blob; if (special.photoUrl) URL.revokeObjectURL(special.photoUrl); special.photoUrl = URL.createObjectURL(blob); render(); toast("Photo added."); return; }
      await uploadPhoto(photoTarget, blob); toast("Photo saved. Customers see it now."); await loadShop();
    } catch (e) { toast(e.message && e.message !== "shrink" ? e.message : "Couldn't use that picture. Try another.", 5000); }
  }
  function resetSpecial() { if (special.photoUrl) URL.revokeObjectURL(special.photoUrl); Object.assign(special, { open: false, name: "", price: "", onlyToday: true, photo: null, photoUrl: "", review: null, busy: false }); }
  async function saveSpecial(name, btn) {
    special.busy = true; render();
    try {
      const r = await api("/api/admin/custom-items", { method: "POST", body: JSON.stringify({ name, price: parseFloat(special.price), only_today: special.onlyToday }) });
      if (special.photo) { try { await uploadPhoto(r.id, special.photo); } catch (e) { toast("Added, but the photo didn't save. Tap “＋ Photo” on it to try again.", 6000); } }
      resetSpecial(); await loadShop(); toast("Added. Customers can order it now.");
    } catch (e) { special.busy = false; render(); if (e.message !== "auth") toast(e.message, 5000); }
  }
  async function specialAction(b, force) {
    const act2 = force || b.dataset.sp;
    if (act2 === "open") { special.open = true; render(); const n = document.getElementById("sp-name"); if (n) n.focus(); return; }
    if (act2 === "cancel") { resetSpecial(); render(); return; }
    if (act2 === "use-existing") { const d = special.review.duplicate; resetSpecial(); shopQ = d.name; render(); window.scrollTo(0, 0); return; }
    if (act2 === "use-fix") { const n = special.review.corrected; special.name = n; special.review = null; return saveSpecial(n, b); }
    if (act2 === "keep") { const n = special.review.clean; special.review = null; return saveSpecial(n, b); }
    if (act2 === "check") {
      const price = parseFloat(special.price);
      if (!special.name.trim()) { toast("Type the dish name."); return; }
      if (!(price > 0)) { toast("Type the price in rupees."); return; }
      b.disabled = true;
      try {
        const r = await api("/api/admin/check-name", { method: "POST", body: JSON.stringify({ name: special.name }) });
        special.name = r.clean;
        if (r.suggestions.length || r.duplicate) { special.review = r; render(); return; }
        return saveSpecial(r.clean, b);
      } catch (e) { b.disabled = false; if (e.message !== "auth") toast(e.message, 5000); }
    }
  }

  function bind() {
    const bk = document.getElementById("back"); if (bk) bk.onclick = () => { oid = null; render(); window.scrollTo(0, 0); };
    app.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => goTab(b.dataset.tab));
    app.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openOrder(+b.dataset.open));
    const td = document.getElementById("toggledone"); if (td) td.onclick = () => { showDone = !showDone; render(); };
    const s1 = document.getElementById("sound");
    if (s1) s1.onclick = () => { soundOn = !soundOn; try { localStorage.setItem(SOUND_KEY, soundOn ? "1" : "0"); } catch (_) {} unlockSound(); if (soundOn) ring(); render(); };

    // sheet
    const no = document.getElementById("sheet-no"), yes = document.getElementById("sheet-yes"), scrim = document.getElementById("scrim");
    if (no) no.onclick = () => { sheet = null; render(); };
    if (scrim) scrim.onclick = (e) => { if (e.target === scrim) { sheet = null; render(); } };
    if (yes) yes.onclick = () => { const s = sheet; sheet = null; render(); s.run(yes); };

    // order actions
    app.querySelectorAll("[data-do]").forEach((b) => b.onclick = () => setStatus(b, b.dataset.id, b.dataset.do));
    app.querySelectorAll("[data-ask]").forEach((b) => b.onclick = () => {
      const id = b.dataset.id, st = b.dataset.ask;
      if (st === "delivered") ask({ title: "Did the customer get the food?", body: "The customer is told “Delivered” and asked to rate. You cannot undo this.", yes: "Yes, delivered", cls: "blue", run: (x) => setStatus(x, id, st) });
      else ask({ title: `Cancel order #${id}?`, body: "The customer is told it is cancelled.", yes: "Yes, cancel it", no: "No, keep it", cls: "red", run: (x) => setStatus(x, id, st) });
    });
    app.querySelectorAll("[data-paid]").forEach((b) => b.onclick = () => {
      const id = b.dataset.paid, o = ((data && data.orders) || []).find((x) => x.id === +id);
      ask({ title: `Did you see ${rs(o && o.total)} in your UPI app?`, body: "The customer is told you got it.", yes: `Yes, I got ${rs(o && o.total)}`, run: (x) => act(x, () => api(`/api/admin/orders/${id}/paid`, { method: "POST" }), "Payment noted. Customer thanked.") });
    });
    const payMsg = (o) => `Hi ${firstName(o)}, this is the kitchen. Your order #${o.id} is ${rs(o.total)}. You can pay by UPI here: ${o.pay_url}`;
    app.querySelectorAll("[data-paylink]").forEach((b) => b.onclick = () => {
      const o = ((data && data.orders) || []).find((x) => x.id === +b.dataset.paylink);
      if (o) window.open(`https://wa.me/${waNum(o.customer_phone)}?text=${encodeURIComponent(payMsg(o))}`, "_blank", "noopener");
    });
    app.querySelectorAll("[data-copypay]").forEach((b) => b.onclick = async () => {
      const o = ((data && data.orders) || []).find((x) => x.id === +b.dataset.copypay);
      try { await navigator.clipboard.writeText(o.pay_url); toast("Link copied."); } catch (_) { toast("Couldn't copy. Use the WhatsApp button."); }
    });

    // rider booking
    app.querySelectorAll("[data-bookapp]").forEach((b) => b.onclick = async () => {
      const o = ((data && data.orders) || []).find((x) => x.id === +b.dataset.bookapp); if (!o) return;
      const text = splitDrop(o.delivery_address).search, rapido = b.dataset.pkg === "rapido";
      try { await navigator.clipboard.writeText(text); toast("Address copied. Paste it in Drop."); } catch (_) { toast("Couldn't copy. Press and hold the address instead."); return; }
      location.href = appLink(rapido ? RAPIDO_PKG : PORTER_PKG);
    });
    const draftOf = (id) => (drafts[id] = drafts[id] || {});
    const refreshLink = (id) => {
      const d = draftOf(id), c = cleanLink(d.link), el = document.getElementById("found-" + id), sb = document.getElementById("send-" + id);
      if (el) el.innerHTML = c.state === "ok" ? `<div class="found ok">✓ Found the rider link</div>` : c.state === "unknown" ? `<div class="found unknown">Found a link, but not a Porter or Rapido one. Check it.</div>` : c.state === "store" ? `<div class="found bad">That is the app-store link, not the rider link.</div>` : c.state === "none" ? `<div class="found bad">No link in that yet.</div>` : "";
      if (sb) sb.disabled = !(c.state === "ok" || c.state === "unknown");
    };
    app.querySelectorAll("[data-draft]").forEach((i) => i.oninput = () => { draftOf(i.dataset.id)[i.dataset.draft] = i.value; if (i.dataset.draft === "link") refreshLink(i.dataset.id); });
    app.querySelectorAll("[data-readclip]").forEach((b) => b.onclick = async () => {
      const id = b.dataset.readclip;
      try { draftOf(id).link = await navigator.clipboard.readText(); const i = document.getElementById("link-" + id); if (i) i.value = draftOf(id).link; refreshLink(id); }
      catch (_) { toast("Phone said no. Press and hold the box and choose Paste."); }
    });
    app.querySelectorAll("[data-dispatch]").forEach((b) => b.onclick = () => {
      const id = b.dataset.dispatch, d = drafts[id] || {}, c = cleanLink(d.link), fare = parseFloat(d.fare);
      if (!(c.state === "ok" || c.state === "unknown")) { toast("Paste the rider message first."); return; }
      act(b, () => api(`/api/admin/orders/${id}/manual-dispatch`, { method: "POST", body: JSON.stringify({ tracking_url: c.url, fare: fare > 0 ? fare : null }) }),
        "Rider link sent to the customer 🛵", () => { delete drafts[id]; });
    });

    // menu + shop
    app.querySelectorAll("[data-shop]").forEach((i) => i.oninput = () => {
      const k = i.dataset.shop;
      if (k === "onlyToday") shopDraft.onlyToday = i.checked;
      else if (k === "q") { shopQ = i.value; clearTimeout(bind.t); bind.t = setTimeout(() => { const pos = i.selectionStart; render(); const n = app.querySelector('[data-shop="q"]'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }, 200); }
      else shopDraft[k] = i.value;
    });
    app.querySelectorAll("[data-close]").forEach((b) => b.onclick = () => {
      const [mode, h] = b.dataset.close.split(":");
      act(b, () => api("/api/admin/store", { method: "POST", body: JSON.stringify({ is_open: false, mode, hours: h ? +h : null, reason: shopDraft.reason }) }).then((r) => { shopDraft.reason = ""; if (data) data.store = r; }), "Online orders are off.");
    });
    app.querySelectorAll("[data-open-shop]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/store", { method: "POST", body: JSON.stringify({ is_open: true }) }).then((r) => { if (data) data.store = r; }), "Online orders are back on."));
    app.querySelectorAll("[data-grp]").forEach((b) => b.onclick = () => { const g = b.dataset.grp; openGroups.has(g) ? openGroups.delete(g) : openGroups.add(g); render(); });
    app.querySelectorAll("[data-photo]").forEach((b) => b.onclick = () => { photoTarget = b.dataset.photo; const f = document.getElementById("photo-input"); if (f) { f.value = ""; f.click(); } });
    const pf = document.getElementById("photo-input"); if (pf) pf.onchange = () => { if (pf.files && pf.files[0]) takePhoto(pf.files[0]); };
    app.querySelectorAll("[data-sp-in]").forEach((i) => i.oninput = () => {
      const k = i.dataset.spIn; if (k === "onlyToday") special.onlyToday = i.checked; else special[k] = i.value; special.review = null;
    });
    app.querySelectorAll("[data-sp]").forEach((b) => b.onclick = () => specialAction(b));
    const spb = document.getElementById("addspecial"); if (spb) spb.onclick = () => specialAction(spb, "check");
    app.querySelectorAll("[data-so]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/sold-out", { method: "POST", body: JSON.stringify({ item_id: b.dataset.so, sold_out: b.dataset.now === "1" }) }).then(loadShop), b.dataset.now === "1" ? "Marked sold out." : "Back on the menu."));
    app.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => { editId = b.dataset.edit; render(); const i = document.getElementById("pe-price"); if (i) { i.focus(); i.select(); } });
    app.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => act(b, () => api("/api/admin/custom-items/" + encodeURIComponent(b.dataset.rm), { method: "DELETE" }).then(loadShop), "Removed."));
    app.querySelectorAll("[data-pe]").forEach((b) => b.onclick = () => {
      const id = b.dataset.id, d = (shopItems || []).find((x) => x.id === (id || editId));
      if (b.dataset.pe === "cancel") { editId = null; render(); return; }
      if (b.dataset.pe === "reset") { act(b, () => api("/api/admin/prices/" + encodeURIComponent(id), { method: "DELETE" }).then(async () => { editId = null; await loadShop(); }), "Back to the original price."); return; }
      const price = parseFloat(document.getElementById("pe-price").value);
      const hEl = document.getElementById("pe-half"), half = hEl ? parseFloat(hEl.value) : null;
      if (!(price > 0) || (hEl && !(half > 0))) { toast("Type the price in rupees."); return; }
      if (price === d.price && (!hEl || half === d.half)) { editId = null; render(); return; }
      ask({ title: `Change ${d.name} to ${rs(price)}?`, body: `Now ${rs(d.price)}${hEl && half !== d.half ? `, half ${rs(half)}` : ""}. Customers see the new price right away.`, yes: "Yes, change it", no: "No", run: (x) => act(x, () => api("/api/admin/prices", { method: "POST", body: JSON.stringify({ item_id: id, price, half_price: hEl ? half : null }) }).then(async () => { editId = null; await loadShop(); }), "Price changed.") });
    });
  }

  async function act(btn, call, okMsg, after) {
    const old = btn && btn.isConnected ? btn.textContent : null;
    if (old !== null) { btn.disabled = true; btn.textContent = "Saving…"; }
    try { await call(); if (after) after(); toast(okMsg || "Done"); await load(true); }
    catch (e) { if (e.message !== "auth") { toast(e.message, 5000); if (old !== null && btn.isConnected) { btn.disabled = false; btn.textContent = old; } } }
  }

  // ---------- polling
  async function load(force) {
    const fresh = await api("/api/admin/kitchen");
    lastOk = Date.now();
    const newOnes = seen ? fresh.orders.filter((o) => !seen.has(o.id) && o.status === "new") : [];
    const firstLoad = !seen;
    const claimedNow = new Set(fresh.orders.filter((o) => o.paid_claimed_at && !o.paid).map((o) => o.id));
    const newClaims = claimed ? [...claimedNow].filter((id) => !claimed.has(id)) : [];
    claimed = claimedNow;
    seen = new Set(fresh.orders.map((o) => o.id)); data = fresh;
    if (firstLoad && focusId) {
      if (fresh.orders.some((o) => o.id === focusId)) { tab = "orders"; oid = focusId; } else toast(`Order #${focusId} isn't open any more.`);
      focusId = null;
    }
    const typing = !force && document.activeElement && document.activeElement.matches("input, textarea");
    if (typing) renderBanner(); else render();   // the banner still updates while she types
    if (newClaims.length && !newOnes.length) { ring(); toast(`Order #${newClaims[0]}: customer says they paid. Check your bank.`, 6000); }
    if (newOnes.length) {
      ring();
      document.title = "🔔 New order · Kitchen"; setTimeout(() => { document.title = "Kitchen"; }, 15000);
    }
  }
  let timer = null;
  function start() {
    app.innerHTML = `<div class="empty">Loading orders…</div>`;
    load().catch((e) => { if (e.message !== "auth") app.innerHTML = `<div class="empty">Couldn't load orders. Check the internet.</div>`; });
    clearInterval(timer);
    timer = setInterval(() => { if (token) load().catch(() => renderBanner()); }, 15000);
  }
  setInterval(renderBanner, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && token) load().catch(() => renderBanner()); });
  document.addEventListener("click", unlockSound, { once: true });
  if (token) start(); else renderLogin();
})();
