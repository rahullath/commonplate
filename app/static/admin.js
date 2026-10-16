(() => {
  const TOKEN_KEY = "admin_token";
  let token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    token = prompt("Admin token:");
    if (!token) return;
    localStorage.setItem(TOKEN_KEY, token);
  }
  const headers = { "X-Admin-Token": token };

  async function api(url, opts = {}) {
    const res = await fetch(url, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
    if (res.status === 401) { localStorage.removeItem(TOKEN_KEY); location.reload(); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Request failed");
    return data;
  }

  const money = (n) => "₹" + (Math.round(n * 100) / 100).toLocaleString("en-IN");

  function toast(msg) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
  }

  function isScheduledForFutureDay(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr); d.setHours(0, 0, 0, 0);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return d.getTime() > today.getTime();
  }

  function formatScheduled(dateStr) {
    const d = new Date(dateStr);
    const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }).toUpperCase();
    if (isScheduledForFutureDay(dateStr)) {
      const day = d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }).toUpperCase();
      return `FOR ${day}, ${time}`;
    }
    return "FOR " + time;
  }

  function timeSince(dateStr) {
    if (!dateStr) return "";
    const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + " min";
    if (diff < 86400) return Math.floor(diff / 3600) + " hr";
    return Math.floor(diff / 86400) + " d";
  }

  // ---- Tab navigation ----
  const tabs = document.querySelectorAll(".tab");
  const tabContents = document.querySelectorAll(".tab-content");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tabContents.forEach(tc => tc.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    });
  });

  // ---- Filter chips ----
  let activeFilter = "live";
  document.querySelectorAll(".ah-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".ah-chip").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      activeFilter = chip.dataset.filter;
      renderFilteredOrders();
    });
  });

  // ---- Orders ----
  const STATUS_META = {
    new: { badge: "badge-new", label: "NEW", action: "Start cooking", next: "preparing" },
    preparing: { badge: "badge-cooking", label: "COOKING", action: "Food ready", next: "ready" },
    ready: { badge: "badge-ready", label: "READY", action: "Out for delivery", next: "out_for_delivery" },
    out_for_delivery: { badge: "badge-cooking", label: "DISPATCHED", action: "Delivered", next: "delivered" },
    delivered: { badge: "badge-done", label: "DONE", action: null, next: null },
    cancelled: { badge: "badge-cancelled", label: "CANCELLED", action: null, next: null },
  };
  const PROGRESS = { new: 0, preparing: 1, ready: 2, out_for_delivery: 3, delivered: 4 };

  let allOrders = [];
  let scheduledOrders = [];
  let seenOrderIds = new Set();

  // Beep + flash the tab when a brand-new order arrives (fallback for when the
  // kitchen isn't on Telegram). Uses the WebAudio API — no audio file needed.
  function beepNewOrder(n) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.18, 0.36].forEach((delay, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = "sine";
        osc.frequency.value = i === 2 ? 988 : 784;  // G5, G5, B5
        gain.gain.setValueAtTime(0.0001, ctx.currentTime + delay);
        gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + delay + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + delay + 0.22);
        osc.start(ctx.currentTime + delay);
        osc.stop(ctx.currentTime + delay + 0.25);
      });
    } catch (e) { /* audio unavailable — skip */ }
    if (n === 1) {
      toast(`🔔 New order!`);
    } else if (n > 1) {
      toast(`🔔 ${n} new orders!`);
    }
    document.title = `(${n}) New order · Admin`;
    setTimeout(() => { document.title = "Admin"; }, 8000);
  }

  function renderOrder(o) {
    const meta = STATUS_META[o.status] || STATUS_META.new;
    const isDone = ["delivered", "cancelled"].includes(o.status);
    const items = o.items.map(i => `${i.name} × ${i.qty}`).join(" · ");
    const phone = o.customer_phone || "";
    const addr = o.delivery_address || "";
    const pin = o.delivery_pincode || "";
    const typeLabel = o.order_type === "delivery"
      ? `Delivery · ${pin}${addr ? " · " + addr.slice(0, 20) : ""}` + (o.payment_method ? ` · ${o.payment_method.toUpperCase()}` : "")
      : `Pickup` + (o.payment_method ? ` · ${o.payment_method.toUpperCase()}` : "");

    let breakdownHTML = "";
    if (o.packing_fee || o.gst_amount) {
      const parts = [`Subtotal ${money(o.subtotal)}`];
      if (o.packing_fee) parts.push(`Packing ${money(o.packing_fee)}`);
      if (o.gst_amount) parts.push(`GST ${money(o.gst_amount)}`);
      if (o.delivery_fee) parts.push(`Delivery ${money(o.delivery_fee)}`);
      breakdownHTML = `<div class="order-breakdown">${parts.join(" · ")}</div>`;
    }

    let progressHTML = "";
    if (!isDone && o.status !== "cancelled") {
      const steps = 5;
      const filled = (PROGRESS[o.status] || 0) + 1;
      progressHTML = `<div class="order-progress">${Array.from({length: steps}, (_, i) =>
        `<div class="progress-bar${i < filled ? " filled" : ""}"></div>`
      ).join("")}</div>`;
    }

    let dispatchHTML = "";
    if (o.order_type === "delivery" && o.sr_courier) {
      const track = o.sr_tracking_url ? ` · <a class="dispatch-action" href="${o.sr_tracking_url}" target="_blank" rel="noopener">Track</a>` : "";
      dispatchHTML = `<div class="order-dispatch"><span class="dispatch-label">Rider: ${escapeHTML(o.sr_courier)}${o.sr_awb ? " · " + escapeHTML(o.sr_awb) : ""}</span><span>${track}</span></div>`;
    } else if (o.order_type === "delivery" && ["preparing", "ready"].includes(o.status)) {
      dispatchHTML = `<div class="order-dispatch"><span class="dispatch-label">Book a Porter rider</span><span class="dispatch-action" data-id="${o.id}">Book</span></div>`;
    }

    let flagHTML = "";
    if (o.address_flagged && !isDone) {
      const waLink = phone ? `https://wa.me/${phone.replace(/\D/g, "")}` : null;
      flagHTML = `<div class="order-flag">
        <div class="order-flag-title">Address needs checking</div>
        <div class="order-flag-reason">${o.address_flag_reason || ""}</div>
        ${waLink ? `<a class="order-flag-action" href="${waLink}" target="_blank" rel="noopener">Ask on WhatsApp</a>` : ""}
      </div>`;
    }

    let quickHTML = "";
    if (o.order_type === "delivery" && !isDone) {
      const waLink = phone ? `https://wa.me/${phone.replace(/\D/g, "")}` : null;
      const mapsQuery = (o.delivery_lat && o.delivery_lng)
        ? `${o.delivery_lat},${o.delivery_lng}`
        : encodeURIComponent(addr || "");
      const mapsLink = mapsQuery ? `https://www.google.com/maps/search/?api=1&query=${mapsQuery}` : null;
      quickHTML = `<div class="order-quick">
        ${phone ? `<a class="quick-btn" href="tel:${phone}">Call</a>` : ""}
        ${waLink ? `<a class="quick-btn" href="${waLink}" target="_blank" rel="noopener">WhatsApp</a>` : ""}
        ${mapsLink ? `<a class="quick-btn quick-btn-muted" href="${mapsLink}" target="_blank" rel="noopener">Open in Maps</a>` : ""}
      </div>`;
    }

    let actionsHTML = "";
    if (meta.action) {
      actionsHTML = `<div class="order-actions">
        <button class="action-primary" data-id="${o.id}" data-next="${meta.next}">${meta.action}</button>
        <button class="action-secondary" data-id="${o.id}" title="More">⋯</button>
      </div>`;
    }

    const card = document.createElement("div");
    card.className = "order-card" + (isDone ? " dimmed" : "");
    card.dataset.status = o.status;
    card.dataset.id = o.id;
    card.innerHTML = `
      <div class="order-top">
        <div class="order-info">
          <div class="order-name">#${o.id} · ${o.customer_name || "Customer"}</div>
          <div class="order-meta">${typeLabel}${o.created_at ? " · " + timeSince(o.created_at) : ""}</div>
        </div>
        <span class="order-badge ${meta.badge}">${meta.label}${!isDone && o.scheduled_at ? " · " + formatScheduled(o.scheduled_at) : (!isDone && o.created_at ? " · " + timeSince(o.created_at) : "")}</span>
      </div>
      <div class="order-items">${items}${o.notes ? `<br><span class="order-notes">"${o.notes}"</span>` : ""}</div>
      ${breakdownHTML}
      ${flagHTML}
      ${quickHTML}
      ${progressHTML}
      ${dispatchHTML}
      ${actionsHTML}
    `;
    return card;
  }

  function renderFilteredOrders() {
    const list = document.getElementById("orders-list");
    const empty = document.getElementById("orders-empty");
    list.innerHTML = "";
    // Scheduled is a planning view across ALL days, not just today — it has
    // its own data source (today_orders() only ever looks at today, so a
    // 10-day-out order would otherwise vanish until its actual due date).
    let filtered = activeFilter === "scheduled"
      ? scheduledOrders
      : allOrders.filter(o => {
        if (activeFilter === "all") return true;
        // Live = needs kitchen action today. An order scheduled for a future
        // day shouldn't sit in today's action queue — it belongs in Scheduled
        // until its actual day arrives, so it can't get started on by mistake.
        if (activeFilter === "live") return ["new", "preparing", "ready", "out_for_delivery"].includes(o.status)
          && !isScheduledForFutureDay(o.scheduled_at);
        if (activeFilter === "done") return ["delivered", "cancelled"].includes(o.status);
        return true;
      });
    empty.hidden = filtered.length > 0;
    for (const o of filtered) list.appendChild(renderOrder(o));
    list.querySelectorAll(".action-primary").forEach(btn => {
      btn.addEventListener("click", () => advanceOrder(btn.dataset.id, btn.dataset.next, btn));
    });
    list.querySelectorAll("span.dispatch-action").forEach(btn => {
      btn.addEventListener("click", () => dispatchOrder(btn.dataset.id, btn));
    });
  }

  function escapeHTML(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }

  async function dispatchOrder(orderId, btn) {
    btn.textContent = "Loading…";
    btn.style.pointerEvents = "none";
    try {
      const d = await api(`/api/admin/orders/${orderId}/dispatch`, { method: "POST" });
      if (d.manual) {
        openPorterSheet(orderId, d);
      } else {
        toast(`Order #${orderId} → rider booked (${d.courier_name || "Porter"})`);
        loadOrders();
      }
    } catch (e) {
      toast(`Booking failed: ${e.message}`);
    }
    btn.textContent = "Book";
    btn.style.pointerEvents = "";
  }

  // Manual Porter booking: copy the card into the Porter app, book, then
  // paste the tracking link back so the customer gets it.
  function openPorterSheet(orderId, d) {
    document.getElementById("porter-sheet")?.remove();
    const b = d.booking || {};
    const sheet = document.createElement("div");
    sheet.id = "porter-sheet";
    sheet.className = "porter-sheet";
    sheet.innerHTML = `
      <div class="porter-card" role="dialog" aria-label="Book Porter rider">
        <div class="porter-title">Book Porter · Order #${orderId}</div>
        <pre class="porter-text">${escapeHTML(d.booking_text)}</pre>
        <div class="porter-row">
          <button type="button" class="porter-btn" id="porter-copy">Copy details</button>
          ${b.drop_map ? `<a class="porter-btn ghost" href="${b.drop_map}" target="_blank" rel="noopener">Drop pin</a>` : ""}
          <a class="porter-btn ghost" href="${b.porter_url || "https://porter.in/"}" target="_blank" rel="noopener">Open Porter</a>
        </div>
        <label class="porter-label">Porter tracking link<input id="porter-url" type="url" placeholder="https://…" autocomplete="off"></label>
        <label class="porter-label">Porter fare (₹) — the customer pays this<input id="porter-fare" type="number" inputmode="decimal" min="10" max="1000" placeholder="e.g. 86"></label>
        <label class="porter-label">Rider name<input id="porter-rider" type="text" autocomplete="off"></label>
        <label class="porter-label">Rider phone<input id="porter-phone" type="tel" autocomplete="off"></label>
        <div class="porter-row">
          <button type="button" class="porter-btn" id="porter-save">Save &amp; notify customer</button>
          <button type="button" class="porter-btn ghost" id="porter-close">Close</button>
        </div>
      </div>`;
    document.body.appendChild(sheet);
    sheet.addEventListener("click", e => { if (e.target === sheet) sheet.remove(); });
    sheet.querySelector("#porter-close").onclick = () => sheet.remove();
    sheet.querySelector("#porter-copy").onclick = async () => {
      try { await navigator.clipboard.writeText(d.booking_text); toast("Copied — paste into Porter"); }
      catch { toast("Copy blocked — long-press the text to copy"); }
    };
    sheet.querySelector("#porter-save").onclick = async (ev) => {
      const saveBtn = ev.currentTarget;
      saveBtn.disabled = true;
      try {
        await api(`/api/admin/orders/${orderId}/manual-dispatch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tracking_url: sheet.querySelector("#porter-url").value.trim(),
            rider_name: sheet.querySelector("#porter-rider").value.trim(),
            rider_phone: sheet.querySelector("#porter-phone").value.trim(),
            fare: Number(sheet.querySelector("#porter-fare").value) || null,
          }),
        });
        toast(`Order #${orderId} → out for delivery`);
        sheet.remove();
        loadOrders();
      } catch (e) {
        toast(`Save failed: ${e.message}`);
        saveBtn.disabled = false;
      }
    };
  }

  async function loadOrders() {
    try {
      const d = await api("/api/admin/today-orders");
      const freshIds = new Set(d.orders.map(o => o.id));
      const isFirstLoad = seenOrderIds.size === 0;
      const newOnes = [...freshIds].filter(id => !seenOrderIds.has(id)
        && d.orders.find(o => o.id === id && !["delivered", "cancelled"].includes(o.status)));
      if (!isFirstLoad && newOnes.length) beepNewOrder(newOnes.length);
      seenOrderIds = freshIds;
      allOrders = d.orders;
      const live = allOrders.filter(o => ["new", "preparing", "ready", "out_for_delivery"].includes(o.status)
        && !isScheduledForFutureDay(o.scheduled_at));
      const done = allOrders.filter(o => ["delivered", "cancelled"].includes(o.status));
      const total = allOrders.reduce((s, o) => s + (o.total || 0), 0);
      const chipLive = document.querySelector('[data-filter="live"]');
      const chipDone = document.querySelector('[data-filter="done"]');
      if (chipLive) chipLive.textContent = `Live · ${live.length}`;
      if (chipDone) chipDone.textContent = `Done · ${done.length}`;
      document.getElementById("day-summary").textContent = `${allOrders.length} orders · ${money(total)}`;
      renderFilteredOrders();
    } catch (e) {
      document.getElementById("orders-empty").textContent = "Failed to load: " + e.message;
      document.getElementById("orders-empty").hidden = false;
    }
  }

  async function loadScheduledOrders() {
    try {
      const d = await api("/api/admin/scheduled-orders");
      scheduledOrders = d.orders;
      const chipScheduled = document.querySelector('[data-filter="scheduled"]');
      if (chipScheduled) chipScheduled.textContent = `Scheduled · ${scheduledOrders.length}`;
      if (activeFilter === "scheduled") renderFilteredOrders();
    } catch (e) { /* non-fatal — chip just keeps its last count */ }
  }

  async function advanceOrder(orderId, nextStatus, btn) {
    btn.disabled = true;
    btn.textContent = "Updating…";
    try {
      await api(`/api/admin/orders/${orderId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      toast(`Order #${orderId} → ${nextStatus}`);
      loadOrders();
      loadScheduledOrders().catch(() => {});
    } catch (e) {
      btn.textContent = "Failed: " + e.message;
      btn.disabled = false;
    }
  }

  // ---- Menu ----
  async function loadMenu() {
    try {
      const d = await api("/api/admin/availability");
      const on = new Set(d.available_ids);
      const all = document.querySelectorAll(".toggle input");
      let onCount = 0;
      all.forEach(inp => {
        inp.checked = on.has(inp.dataset.id);
        if (inp.checked) onCount++;
      });
      document.getElementById("menu-count").textContent = `${onCount} of ${all.length} items on`;
      if (d.last_day) document.getElementById("menu-count").textContent += ` · copied from ${d.last_day}`;
    } catch (e) {
      document.getElementById("menu-count").textContent = "Failed to load menu";
    }
  }

  let saveTimer = null;
  document.addEventListener("change", (e) => {
    if (!e.target.classList.contains("avail-check")) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveMenu, 400);
  });

  async function saveMenu() {
    const checks = document.querySelectorAll(".avail-check");
    try {
      await api("/api/admin/availability", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          available_ids: [...checks].filter(c => c.checked).map(c => c.dataset.id),
          unavailable_ids: [...checks].filter(c => !c.checked).map(c => c.dataset.id),
        }),
      });
      toast("Menu saved ✓");
      loadMenu();
    } catch (e) {
      toast("Save failed: " + e.message);
    }
  }

  document.getElementById("repeat-yesterday").addEventListener("click", async () => {
    try {
      await api("/api/admin/availability/repeat-yesterday", { method: "POST" });
      toast("Copied from yesterday ✓");
      loadMenu();
    } catch (e) {
      toast("Failed: " + e.message);
    }
  });

  document.getElementById("all-on").addEventListener("click", async () => {
    const checks = document.querySelectorAll(".avail-check");
    checks.forEach(c => { c.checked = true; });
    await saveMenu();
    toast("All items on ✓");
  });

  // ---- Chats ----
  async function loadConvos() {
    try {
      const d = await api("/api/admin/conversations");
      const list = document.getElementById("convos-list");
      const empty = document.getElementById("convos-empty");
      list.innerHTML = "";
      empty.hidden = d.conversations.length > 0;
      for (const c of d.conversations) {
        const card = document.createElement("div");
        card.className = "convo-card";
        card.innerHTML = `
          <div class="convo-info">
            <div class="convo-name">${c.name || c.wa_id}</div>
            <div class="convo-detail">${c.wa_id}${c.updated_at ? " · " + c.updated_at : ""}</div>
          </div>
          <button class="convo-toggle${c.human ? " human" : ""}" data-wa="${c.wa_id}" data-human="${c.human ? 1 : 0}">
            ${c.human ? "You" : "Bot"}
          </button>
        `;
        list.appendChild(card);
      }
      list.querySelectorAll(".convo-toggle").forEach(btn => {
        btn.addEventListener("click", async () => {
          const next = !(btn.dataset.human === "1");
          btn.disabled = true;
          try {
            await api(`/api/admin/conversations/${encodeURIComponent(btn.dataset.wa)}/human`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ human: next }),
            });
            btn.dataset.human = next ? "1" : "0";
            btn.textContent = next ? "You" : "Bot";
            btn.classList.toggle("human", next);
          } finally { btn.disabled = false; }
        });
      });
    } catch (e) {
      document.getElementById("convos-empty").hidden = false;
    }
  }

  // ---- Day stats ----
  async function loadDayStats() {
    try {
      const d = await api("/api/admin/today-orders");
      const orders = d.orders;
      const total = orders.reduce((s, o) => s + (o.total || 0), 0);
      const delivered = orders.filter(o => o.status === "delivered");
      const avg = delivered.length ? money(total / delivered.length) : "—";
      const delivery = orders.filter(o => o.order_type === "delivery");
      const pickup = orders.filter(o => o.order_type === "pickup");
      document.getElementById("day-stats").innerHTML = `
        <div>Total orders: <span class="day-stat-val">${orders.length}</span></div>
        <div>Revenue: <span class="day-stat-val">${money(total)}</span></div>
        <div>Avg order: <span class="day-stat-val">${avg}</span></div>
        <div>Delivered: <span class="day-stat-val">${delivered.length}</span></div>
        <div>Delivery: <span class="day-stat-val">${delivery.length}</span> · Pickup: <span class="day-stat-val">${pickup.length}</span></div>
      `;
    } catch (e) {
      document.getElementById("day-stats").textContent = "Failed to load stats";
    }
  }

  // ---- Reviews ----
  const SOURCE_LABELS = {
    google: "Google", instagram: "Instagram",
    facebook: "Facebook", whatsapp: "WhatsApp", in_person: "Physical / in-person",
  };

  function renderReview(r) {
    const card = document.createElement("div");
    card.className = "review-card";
    const stars = r.rating ? "★".repeat(r.rating) + "☆".repeat(5 - r.rating) : "";
    card.innerHTML = `
      <div class="review-top">
        <div>
          <span class="review-source">${SOURCE_LABELS[r.source] || r.source}</span>
          <div class="review-quote">"${r.quote}"</div>
          <div class="review-meta">${r.author_name || "Anonymous"}${stars ? " · " + stars : ""}${r.proof_url ? ` · <a href="${r.proof_url}" target="_blank" rel="noopener">proof</a>` : ""}</div>
        </div>
        <div class="review-actions">
          <label class="toggle" title="Feature on site">
            <input type="checkbox" class="review-feature" data-id="${r.id}" ${r.featured ? "checked" : ""}>
            <span class="slider"></span>
          </label>
          <button type="button" class="review-delete" data-id="${r.id}">Delete</button>
        </div>
      </div>
    `;
    return card;
  }

  async function loadReviews() {
    try {
      const d = await api("/api/admin/reviews");
      const list = document.getElementById("reviews-list");
      const empty = document.getElementById("reviews-empty");
      list.innerHTML = "";
      empty.hidden = d.reviews.length > 0;
      for (const r of d.reviews) list.appendChild(renderReview(r));
      list.querySelectorAll(".review-feature").forEach(inp => {
        inp.addEventListener("change", async () => {
          try {
            await api(`/api/admin/reviews/${inp.dataset.id}/feature`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ featured: inp.checked }),
            });
            toast(inp.checked ? "Featured on site ✓" : "Removed from site");
          } catch (e) { toast("Failed: " + e.message); inp.checked = !inp.checked; }
        });
      });
      list.querySelectorAll(".review-delete").forEach(btn => {
        btn.addEventListener("click", async () => {
          if (!confirm("Delete this review?")) return;
          try {
            await api(`/api/admin/reviews/${btn.dataset.id}`, { method: "DELETE" });
            loadReviews();
          } catch (e) { toast("Failed: " + e.message); }
        });
      });
    } catch (e) {
      document.getElementById("reviews-empty").textContent = "Failed to load: " + e.message;
      document.getElementById("reviews-empty").hidden = false;
    }
  }

  document.getElementById("review-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/admin/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: fd.get("source"),
          quote: fd.get("quote"),
          author_name: fd.get("author_name") || null,
          rating: fd.get("rating") ? Number(fd.get("rating")) : null,
          proof_url: fd.get("proof_url") || null,
        }),
      });
      e.target.reset();
      toast("Review added ✓");
      loadReviews();
    } catch (e) { toast("Failed: " + e.message); }
  });

  async function loadPlatformStats() {
    try {
      const d = await api("/api/admin/platform-stats");
      const s = d.stats || {};
      const form = document.getElementById("platform-stats-form");
      const googleDisplay = document.getElementById("google-stats-display");
      googleDisplay.textContent = s.google
        ? `Google: ${s.google.rating}★ (${s.google.review_count} reviews) · updated ${s.google.updated_at}`
        : "Google: not configured yet (needs GOOGLE_PLACES_API_KEY + GOOGLE_PLACE_ID)";
    } catch (e) { /* non-fatal — form just stays blank */ }
  }

  document.getElementById("platform-stats-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      toast("Numbers saved ✓");
      loadPlatformStats();
    } catch (e) { toast("Failed: " + e.message); }
  });

  async function loadOrderCount() {
    try {
      const d = await api("/api/admin/order-count");
      if (d.count) document.querySelector('#order-count-form [name=order_count]').value = d.count;
    } catch (e) { /* non-fatal — form just stays blank */ }
  }

  document.getElementById("order-count-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const count = new FormData(e.target).get("order_count");
    if (!count) return;
    try {
      await api("/api/admin/order-count", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: Number(count) }),
      });
      toast("Order count saved ✓");
    } catch (e) { toast("Failed: " + e.message); }
  });

  // ---- Init ----
  loadOrders();
  loadScheduledOrders().catch(() => {});
  loadMenu().catch(() => {});
  loadConvos().catch(() => {});
  loadDayStats().catch(() => {});
  loadReviews().catch(() => {});
  loadPlatformStats().catch(() => {});
  loadOrderCount().catch(() => {});
  setInterval(loadOrders, 30000);
  setInterval(loadScheduledOrders, 30000);
})();
