/* slurrrp — cart PWA front-end (vanilla JS, no build step) */
(function () {
  "use strict";

  var root = document.getElementById("app");
  var toastWrap = document.getElementById("toast");

  var S = {
    token: localStorage.getItem("slurrrp_token") || null,
    user: JSON.parse(localStorage.getItem("slurrrp_user") || "null"),
    view: "login",
    menu: [],
    orders: [],
    report: null,
    users: [],
    cart: {},            // menu_item_id -> qty
    cat: "All",
    payment: "cash",
    note: "",
    sheetOpen: false,
    kitchenAll: false,
    editItem: null,
    connected: false,
    es: null,
    audio: null,
    loginRole: "seller",
  };

  /* ---------------- helpers ---------------- */
  function money(n) {
    n = Math.round((Number(n) || 0) * 100) / 100;
    var s = n % 1 === 0 ? String(n) : n.toFixed(2);
    return "₹" + Number(s).toLocaleString("en-IN");
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function timeShort(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var h = d.getHours(), m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function prepBadge(p) {
    return p === "kitchen"
      ? '<span class="badge-prep badge-kitchen">Kitchen</span>'
      : '<span class="badge-prep badge-cart">Cart</span>';
  }

  function toast(msg, kind) {
    var t = document.createElement("div");
    t.className = "toast " + (kind || "info");
    t.textContent = msg;
    toastWrap.appendChild(t);
    setTimeout(function () {
      t.style.transition = "opacity .3s";
      t.style.opacity = "0";
      setTimeout(function () { t.remove(); }, 300);
    }, 2600);
  }

  function beep() {
    try {
      if (!S.audio) S.audio = new (window.AudioContext || window.webkitAudioContext)();
      var ctx = S.audio, t = ctx.currentTime;
      [0, 0.18].forEach(function (off) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "sine"; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, t + off);
        g.gain.exponentialRampToValueAtTime(0.35, t + off + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + off + 0.15);
        o.connect(g); g.connect(ctx.destination);
        o.start(t + off); o.stop(t + off + 0.16);
      });
    } catch (e) {}
  }

  function notify(title, body) {
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(title, { body: body, icon: "/icon.svg", badge: "/icon.svg" });
      }
    } catch (e) {}
  }

  /* ---------------- api ---------------- */
  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (S.token) headers.Authorization = "Bearer " + S.token;
    return fetch("/api" + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          if (r.status === 401 && S.token) { doLogout(); }
          throw new Error(data.error || "Request failed");
        }
        return data;
      });
    });
  }

  /* ---------------- auth ---------------- */
  function doLogin(username, password) {
    return api("/login", { method: "POST", body: { username: username, password: password } })
      .then(function (d) {
        S.token = d.token; S.user = d.user;
        localStorage.setItem("slurrrp_token", d.token);
        localStorage.setItem("slurrrp_user", JSON.stringify(d.user));
        try { if (!S.audio) S.audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
        if ("Notification" in window && Notification.permission === "default") {
          Notification.requestPermission();
        }
        afterLogin();
      });
  }
  function doLogout() {
    if (S.es) { S.es.close(); S.es = null; }
    if (S.keepAlive) { clearInterval(S.keepAlive); S.keepAlive = null; }
    S.token = null; S.user = null;
    localStorage.removeItem("slurrrp_token");
    localStorage.removeItem("slurrrp_user");
    S.view = "login"; S.cart = {}; S.orders = []; S.menu = [];
    render();
  }

  function defaultView() {
    if (!S.user) return "login";
    if (S.user.role === "seller") return "new-order";
    if (S.user.role === "kitchen") return "kitchen";
    return "dashboard";
  }

  function afterLogin() {
    S.view = defaultView();
    connectEvents();
    startKeepAlive();
    loadInitial().then(render);
    render();
  }

  // Keeps a free/idle host awake while any device has slurrrp open.
  function startKeepAlive() {
    if (S.keepAlive) clearInterval(S.keepAlive);
    S.keepAlive = setInterval(function () {
      fetch("/api/health").catch(function () {});
    }, 240000);
  }

  function loadInitial() {
    var jobs = [api("/menu").then(function (d) { S.menu = d.items; })];
    if (S.user.role !== "kitchen" || true) {
      jobs.push(api("/orders").then(function (d) { S.orders = d.orders; }).catch(function () {}));
    }
    if (S.user.role === "admin") {
      jobs.push(loadReport());
    }
    return Promise.all(jobs).catch(function () {});
  }
  function loadReport() {
    return api("/reports/summary").then(function (d) { S.report = d; }).catch(function () {});
  }
  function reloadOrders() {
    return api("/orders").then(function (d) { S.orders = d.orders; });
  }

  /* ---------------- live events ---------------- */
  function connectEvents() {
    if (S.es) S.es.close();
    var es = new EventSource("/api/events?token=" + encodeURIComponent(S.token));
    S.es = es;
    es.onopen = function () { S.connected = true; updateConnDot(); };
    es.onerror = function () { S.connected = false; updateConnDot(); };
    es.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleEvent(msg.event, msg.data);
    };
  }

  function upsertOrder(o) {
    var i = S.orders.findIndex(function (x) { return x.id === o.id; });
    if (i >= 0) S.orders[i] = o; else S.orders.unshift(o);
  }

  function handleEvent(event, data) {
    if (event === "order:new") {
      upsertOrder(data);
      S.flashId = data.id;
      if (S.user.role === "kitchen" || S.user.role === "admin") {
        beep();
        var items = data.items.map(function (it) { return it.qty + "× " + it.name; }).join(", ");
        notify("New order #" + data.token_number, items);
        toast("New order #" + data.token_number + " received", "info");
      }
      if (S.user.role === "admin") loadReport().then(maybeRerender);
      maybeRerender();
    } else if (event === "order:status") {
      upsertOrder(data);
      if (S.user.role === "seller" && data.status === "ready") {
        beep();
        notify("Order #" + data.token_number + " is ready", "Hand it over to the customer");
        toast("Order #" + data.token_number + " is READY", "ok");
      }
      if (S.user.role === "admin") loadReport().then(maybeRerender);
      maybeRerender();
    } else if (event === "menu:updated") {
      var refreshViews = ["new-order", "menu", "stock", "dashboard"];
      api("/menu").then(function (d) { S.menu = d.items; if (refreshViews.indexOf(S.view) >= 0) render(); });
    } else if (event === "stock:update") {
      (data.items || []).forEach(function (it) {
        var m = S.menu.find(function (x) { return x.id === it.id; });
        if (m) { m.stock = it.stock; m.low_stock = it.low_stock; }
      });
      if (["new-order", "kitchen", "stock", "menu", "dashboard"].indexOf(S.view) >= 0) render();
    } else if (event === "stock:low") {
      if (S.user.role === "kitchen" || S.user.role === "admin") {
        beep();
        var lowNames = (data.items || []).map(function (i) { return i.name + " (" + i.stock + " left)"; });
        notify("Refill inventory", lowNames.join(", "));
        toast("Refill needed: " + lowNames.join(", "), "err");
      }
    }
  }

  function maybeRerender() {
    var live = ["new-order", "seller-orders", "kitchen", "dashboard", "admin-orders"];
    if (live.indexOf(S.view) >= 0) render();
    else updateNavBadges();
  }

  /* ---------------- order actions ---------------- */
  function cartCount() {
    return Object.keys(S.cart).reduce(function (a, k) { return a + S.cart[k]; }, 0);
  }
  function cartTotal() {
    var t = 0;
    Object.keys(S.cart).forEach(function (k) {
      var m = S.menu.find(function (x) { return String(x.id) === String(k); });
      if (m) t += m.price * S.cart[k];
    });
    return t;
  }
  function itemStock(id) {
    var m = S.menu.find(function (x) { return String(x.id) === String(id); });
    return m && m.stock !== null && m.stock !== undefined ? m.stock : null;
  }
  function addToCart(id) {
    var stock = itemStock(id);
    var have = S.cart[id] || 0;
    if (stock !== null && have >= stock) {
      toast(stock <= 0 ? "Sold out" : "Only " + stock + " left in stock", "err");
      return;
    }
    S.cart[id] = have + 1;
    render();
  }
  function decCart(id) {
    if (!S.cart[id]) return;
    S.cart[id] -= 1;
    if (S.cart[id] <= 0) delete S.cart[id];
    render();
  }

  function placeOrder() {
    var items = Object.keys(S.cart).map(function (k) {
      return { menu_item_id: Number(k), qty: S.cart[k] };
    });
    if (!items.length) return;
    api("/orders", { method: "POST", body: { items: items, payment_mode: S.payment, note: S.note } })
      .then(function (d) {
        S.cart = {}; S.note = ""; S.sheetOpen = false;
        upsertOrder(d.order);
        toast("Order #" + d.order.token_number + " placed · " + money(d.order.total), "ok");
        render();
      })
      .catch(function (e) { toast(e.message, "err"); });
  }

  function setStatus(id, status) {
    api("/orders/" + id + "/status", { method: "PATCH", body: { status: status } })
      .then(function (d) { upsertOrder(d.order); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }

  /* ================= RENDER ================= */
  function render() {
    if (!S.user) { root.innerHTML = renderLogin(); wire(); return; }
    var body =
      '<div class="shell">' +
      renderHeader() +
      '<main class="main">' + renderView() + "</main>" +
      renderNav() +
      "</div>" +
      (S.sheetOpen ? renderCartSheet() : "");
    root.innerHTML = body;
    wire();
  }

  function wordmark(cls) {
    return '<svg class="wm ' + cls + '" viewBox="0 0 300 78" preserveAspectRatio="xMidYMid meet" role="img" aria-label="slurrrp">' +
      '<text x="150" y="58" text-anchor="middle">slurrrp</text></svg>';
  }

  function renderHeader() {
    var sub = { seller: "Cart counter", kitchen: "Back kitchen", admin: "Owner console" }[S.user.role];
    return (
      '<header class="app-header">' +
      '<div>' + wordmark("wm-hd") +
      '<div class="header-sub">' + sub + "</div></div>" +
      '<div class="header-spacer"></div>' +
      '<span class="conn-dot ' + (S.connected ? "" : "off") + '" title="live"></span>' +
      '<div class="user-chip"><div><div>' + esc(S.user.name) +
      '</div><div class="role">' + S.user.role + "</div></div>" +
      '<button class="icon-btn" data-act="logout" title="Log out">⏻</button></div>' +
      "</header>"
    );
  }

  function navItems() {
    if (S.user.role === "seller")
      return [["new-order", "🛒", "New Order"], ["seller-orders", "🧾", "Orders"]];
    if (S.user.role === "kitchen")
      return [["kitchen", "🍳", "Kitchen"], ["stock", "📦", "Stock"], ["seller-orders", "📋", "Orders"]];
    return [
      ["dashboard", "📊", "Dashboard"],
      ["admin-orders", "🧾", "Orders"],
      ["menu", "🍜", "Menu"],
      ["staff", "👥", "Staff"],
    ];
  }
  function activeOrderCount() {
    return S.orders.filter(function (o) {
      return ["new", "preparing", "ready"].indexOf(o.status) >= 0 &&
        (S.user.role !== "kitchen" || o.needs_kitchen);
    }).length;
  }
  function renderNav() {
    var items = navItems();
    var badgeView = S.user.role === "kitchen" ? "kitchen" : (S.user.role === "seller" ? "seller-orders" : "admin-orders");
    var cnt = activeOrderCount();
    var html = '<nav class="nav">';
    items.forEach(function (it) {
      var badge = (it[0] === badgeView && cnt) ? '<span class="badge">' + cnt + "</span>" : "";
      html += '<button data-nav="' + it[0] + '" class="' + (S.view === it[0] ? "active" : "") + '">' +
        '<span class="ico">' + it[1] + "</span>" + badge + "<span>" + it[2] + "</span></button>";
    });
    return html + "</nav>";
  }
  function updateNavBadges() {
    var nav = document.querySelector(".nav");
    if (nav) nav.outerHTML = renderNav();
    wireNav();
  }
  function updateConnDot() {
    var d = document.querySelector(".conn-dot");
    if (d) d.className = "conn-dot " + (S.connected ? "" : "off");
  }

  function renderView() {
    switch (S.view) {
      case "new-order": return renderNewOrder();
      case "seller-orders": return renderOrders(S.user.role === "kitchen");
      case "kitchen": return renderKitchen();
      case "stock": return renderInventory(false);
      case "dashboard": return renderDashboard();
      case "admin-orders": return renderOrders(false, true);
      case "menu": return renderMenuAdmin();
      case "staff": return renderStaff();
      default: return "";
    }
  }

  /* ---------- login ---------- */
  function renderLogin() {
    var demo = { seller: "seller", kitchen: "kitchen", admin: "admin" }[S.loginRole];
    return (
      '<div class="login-wrap"><div class="login-card">' +
      '<div class="login-logo"><span class="logo-badge">' + wordmark("wm-lg") + "</span></div>" +
      '<div class="login-tag">Pan-Asian · Cart & Kitchen</div>' +
      '<div class="role-tabs">' +
      ["seller", "kitchen", "admin"].map(function (r) {
        return '<button data-role="' + r + '" class="' + (S.loginRole === r ? "active" : "") + '">' +
          r.charAt(0).toUpperCase() + r.slice(1) + "</button>";
      }).join("") +
      "</div>" +
      '<div class="field"><label>Username</label><input id="lg-user" value="' + demo + '" autocapitalize="none" autocomplete="username"></div>' +
      '<div class="field"><label>Password</label><input id="lg-pass" type="password" value="slurrrp123" autocomplete="current-password"></div>' +
      '<button class="btn btn-primary btn-block" data-act="login">Log in</button>' +
      '<div class="login-hint">Demo logins are pre-filled. Default password <b>slurrrp123</b> — change these in <b>Staff</b> after first login.</div>' +
      "</div></div>"
    );
  }

  /* ---------- seller: new order ---------- */
  function renderNewOrder() {
    var cats = ["All"].concat(uniqueCats());
    var list = S.menu.filter(function (m) { return S.cat === "All" || m.category === S.cat; });
    var grid = list.length
      ? '<div class="menu-grid">' + list.map(menuCard).join("") + "</div>"
      : emptyState("🍜", "No items here", "Add items from the Menu tab.");
    var bar = cartCount()
      ? '<div class="cart-bar"><div class="cart-bar-inner" data-act="open-sheet">' +
        '<div><div class="cc">' + cartCount() + " item" + (cartCount() > 1 ? "s" : "") + "</div>" +
        '<div class="ct">Tap to review & take payment</div></div>' +
        '<div class="cc">' + money(cartTotal()) + " ›</div></div></div>"
      : "";
    return (
      '<div class="cat-bar">' + cats.map(function (c) {
        return '<button class="chip ' + (S.cat === c ? "active" : "") + '" data-cat="' + esc(c) + '">' + esc(c) + "</button>";
      }).join("") + "</div>" +
      grid + bar
    );
  }
  function uniqueCats() {
    var seen = [];
    S.menu.forEach(function (m) { if (seen.indexOf(m.category) < 0) seen.push(m.category); });
    return seen;
  }
  function menuCard(m) {
    var q = S.cart[m.id] || 0;
    var tracked = m.stock !== null && m.stock !== undefined;
    var soldOut = tracked && m.stock <= 0;
    var atMax = tracked && q >= m.stock;
    var stockLine = tracked
      ? '<div class="small ' + (m.stock <= m.low_stock ? "stock-low" : "muted") +
        '" style="font-weight:600">' + (soldOut ? "Sold out" : m.stock + " left") + "</div>"
      : "";
    var control = q
      ? '<div class="stepper"><button data-dec="' + m.id + '">−</button>' +
        '<span class="q">' + q + '</span>' +
        '<button data-inc="' + m.id + '"' + (atMax ? " disabled" : "") + ">+</button></div>"
      : (soldOut ? "" : '<button class="add-btn" data-inc="' + m.id + '">+</button>');
    return (
      '<div class="mcard' + (soldOut ? " mcard-out" : "") + '">' +
      (soldOut && !q ? '<span class="soldout-tag">Sold out</span>' : "") +
      '<div class="mname">' + esc(m.name) + "</div>" +
      '<div>' + prepBadge(m.prep_location) + "</div>" +
      stockLine +
      '<div class="between"><span class="mprice">' + money(m.price) + "</span>" +
      (q ? control : "") + "</div>" +
      (q ? "" : control) +
      "</div>"
    );
  }

  function renderCartSheet() {
    var rows = Object.keys(S.cart).map(function (k) {
      var m = S.menu.find(function (x) { return String(x.id) === String(k); });
      if (!m) return "";
      return (
        '<div class="line-item">' +
        '<div class="grow"><div class="li-name">' + esc(m.name) + " " + prepBadge(m.prep_location) + "</div>" +
        '<div class="small muted">' + money(m.price) + " each</div></div>" +
        '<div class="stepper"><button data-dec="' + m.id + '">−</button>' +
        '<span class="q">' + S.cart[k] + '</span><button data-inc="' + m.id + '">+</button></div>' +
        '<div style="min-width:64px;text-align:right;font-weight:800">' + money(m.price * S.cart[k]) + "</div>" +
        "</div>"
      );
    }).join("");
    var pays = ["cash", "upi", "card"].map(function (p) {
      return '<button data-pay="' + p + '" class="' + (S.payment === p ? "active" : "") + '">' +
        p.toUpperCase() + "</button>";
    }).join("");
    return (
      '<div class="sheet-backdrop" data-act="close-sheet"><div class="sheet" data-stop="1">' +
      '<div class="sheet-head"><h3>Review order</h3><button class="btn btn-sm btn-outline" data-act="close-sheet">Close</button></div>' +
      '<div class="sheet-body">' + (rows || emptyState("🛒", "Cart is empty", "")) +
      '<div class="field" style="margin-top:8px"><label>Payment mode</label><div class="pay-opts">' + pays + "</div></div>" +
      '<div class="field"><label>Note (optional)</label><input id="cart-note" placeholder="e.g. less spicy" value="' + esc(S.note) + '"></div>' +
      "</div>" +
      '<div class="sheet-foot"><button class="btn btn-primary btn-block" data-act="place">Place order · ' + money(cartTotal()) + "</button></div>" +
      "</div></div>"
    );
  }

  /* ---------- orders list (seller / kitchen all / admin) ---------- */
  function renderOrders(kitchenScope, admin) {
    var title = admin ? "All Orders" : (kitchenScope ? "All Orders" : "My Orders");
    var list = S.orders.slice();
    if (!list.length) return viewTitle(title, "Today") + emptyState("🧾", "No orders yet", "Orders placed at the cart show up here.");
    return viewTitle(title, "Today · " + list.length + " orders") +
      list.map(function (o) { return orderCard(o, admin); }).join("");
  }

  function orderCard(o, admin) {
    var flash = S.flashId === o.id ? " ticket-flash" : "";
    var items = o.items.map(function (it) {
      return '<div class="oi-row ' + (it.prep_location === "kitchen" ? "kitchen" : "cart") + '">' +
        '<span class="oi-name">' + it.qty + "× " + esc(it.name) + "</span>" +
        '<span class="muted">' + money(it.line_total) + "</span></div>";
    }).join("");
    var actions = orderActions(o, admin);
    return (
      '<div class="card order-card st-' + o.status + flash + '">' +
      '<div class="between"><div class="row" style="gap:8px">' +
      '<span class="token-badge">#' + o.token_number + "</span>" +
      statusPill(o.status) + "</div>" +
      '<div class="small muted">' + timeShort(o.created_at) + "</div></div>" +
      (o.needs_kitchen ? '<div class="small" style="color:#c2410c;font-weight:700;margin:6px 0 2px">🍳 Needs back kitchen</div>' : "") +
      '<div style="margin:8px 0">' + items + "</div>" +
      (o.note ? '<div class="small muted" style="margin-bottom:6px">Note: ' + esc(o.note) + "</div>" : "") +
      '<div class="between"><div><b>' + money(o.total) + "</b>" +
      (o.payment_mode ? ' <span class="small muted">· ' + o.payment_mode.toUpperCase() + "</span>" : ' <span class="small muted">· unpaid</span>') +
      " <span class='small muted'>· by " + esc(o.created_by_name || "") + "</span></div>" +
      "</div>" +
      (actions ? '<div class="divider"></div><div class="row" style="gap:8px;flex-wrap:wrap">' + actions + "</div>" : "") +
      "</div>"
    );
  }

  function statusPill(s) {
    var label = { new: "New", preparing: "Preparing", ready: "Ready", served: "Served", cancelled: "Cancelled" }[s];
    return '<span class="pill pill-' + s + '">' + label + "</span>";
  }

  function orderActions(o, admin) {
    var role = S.user.role, btns = [];
    var can = function (r) { return role === r || role === "admin"; };
    if (o.status === "new") {
      if (can("kitchen") && o.needs_kitchen) btns.push(btn("Start cooking", "btn-blue", "start", o.id));
      if (can("seller")) btns.push(btn("Mark served", "btn-ok", "served", o.id));
    } else if (o.status === "preparing") {
      if (can("kitchen")) btns.push(btn("Mark ready", "btn-ok", "ready", o.id));
    } else if (o.status === "ready") {
      if (can("seller")) btns.push(btn("Mark served", "btn-ok", "served", o.id));
    }
    // Voiding an order is admin-only (a seller must not be able to erase a sale).
    if (role === "admin" && ["new", "preparing", "ready"].indexOf(o.status) >= 0)
      btns.push(btn("Void order", "btn-danger", "cancel", o.id));
    return btns.join("");
  }
  function btn(label, cls, action, id) {
    return '<button class="btn btn-sm ' + cls + '" data-order="' + id + '" data-status="' + action + '">' + label + "</button>";
  }

  /* ---------- kitchen board ---------- */
  function renderKitchen() {
    var list = S.orders.filter(function (o) {
      var active = ["new", "preparing", "ready"].indexOf(o.status) >= 0;
      return active && (S.kitchenAll || o.needs_kitchen);
    });
    var head =
      '<div class="between" style="margin-bottom:10px"><div class="view-title" style="margin:0">Kitchen board' +
      '<small>' + list.length + " active · back-kitchen tickets</small></div>" +
      '<button class="btn btn-sm btn-outline" data-act="toggle-kitchen">' + (S.kitchenAll ? "Kitchen only" : "Show all") + "</button></div>";
    if (!list.length) return head + emptyState("✅", "All caught up", "New orders will pop up here with a sound.");
    // sort: new first, then preparing, then ready; oldest first within
    var rank = { new: 0, preparing: 1, ready: 2 };
    list.sort(function (a, b) { return (rank[a.status] - rank[b.status]) || (a.id - b.id); });
    return head + list.map(function (o) { return kitchenTicket(o); }).join("");
  }
  function kitchenTicket(o) {
    var flash = S.flashId === o.id ? " ticket-flash" : "";
    var items = o.items.map(function (it) {
      var isK = it.prep_location === "kitchen";
      return '<div class="oi-row ' + (isK ? "kitchen" : "cart") + '">' +
        '<span class="oi-name">' + it.qty + "× " + esc(it.name) + (isK ? "" : " <span class='badge-prep badge-cart'>cart</span>") + "</span></div>";
    }).join("");
    var action = "";
    if (o.status === "new") action = btn("Start cooking", "btn-blue btn-block", "start", o.id);
    else if (o.status === "preparing") action = btn("Mark ready", "btn-ok btn-block", "ready", o.id);
    else action = '<div class="small muted" style="text-align:center;width:100%">Ready — waiting for pickup</div>';
    return (
      '<div class="card order-card st-' + o.status + flash + '">' +
      '<div class="between"><span class="token-badge">#' + o.token_number + "</span>" +
      statusPill(o.status) + "</div>" +
      '<div class="small muted">' + timeShort(o.created_at) + "</div>" +
      '<div style="margin:8px 0">' + items + "</div>" +
      (o.note ? '<div class="small muted" style="margin-bottom:8px">Note: ' + esc(o.note) + "</div>" : "") +
      action + "</div>"
    );
  }

  /* ---------- inventory (kitchen read-only; admin manages in Menu) ---------- */
  function renderInventory(canManage) {
    var tracked = S.menu.filter(function (m) {
      return m.is_active && m.stock !== null && m.stock !== undefined;
    });
    var low = tracked.filter(function (m) { return m.stock <= m.low_stock; });
    var head = viewTitle("Inventory", tracked.length + " tracked · " + low.length + " need refill");
    if (!tracked.length)
      return head + emptyState("📦", "Nothing tracked yet", "Admin can set stock counts in the Menu tab.");
    var lowBanner = low.length
      ? '<div class="card" style="border-left:5px solid var(--red)"><b style="color:var(--red-dark)">⚠ Refill needed</b>' +
        '<div class="small" style="margin-top:4px">' +
        low.map(function (m) { return esc(m.name) + " (" + m.stock + " left)"; }).join(", ") + "</div></div>"
      : '<div class="card" style="border-left:5px solid var(--ok)"><b style="color:var(--ok)">✓ Stock levels healthy</b></div>';
    tracked = tracked.slice().sort(function (a, b) {
      return (a.stock - a.low_stock) - (b.stock - b.low_stock);
    });
    var rows = tracked.map(function (m) { return invRow(m, canManage); }).join("");
    return head + lowBanner + '<div class="card">' + rows + "</div>";
  }
  function invRow(m, canManage) {
    var isLow = m.stock <= m.low_stock;
    return (
      '<div class="list-row">' +
      '<div class="grow"><div style="font-weight:600">' + esc(m.name) + " " + prepBadge(m.prep_location) + "</div>" +
      '<div class="small ' + (isLow ? "stock-low" : "muted") + '" style="font-weight:600">' +
      m.stock + " left" + (isLow ? " · alert at " + m.low_stock : "") + "</div></div>" +
      (canManage
        ? '<button class="btn btn-sm btn-outline" data-restock="' + m.id + '">Restock</button>'
        : (isLow ? '<span class="pill pill-cancelled">LOW</span>' : "")) +
      "</div>"
    );
  }

  /* ---------- admin dashboard ---------- */
  function renderDashboard() {
    var r = S.report;
    if (!r) return '<div class="spinner"></div>';
    var top = (r.top_items || []).map(function (t, i) {
      return '<div class="rank-row"><div class="rank-n">' + (i + 1) + "</div>" +
        '<div class="grow">' + esc(t.name) + "</div>" +
        '<div class="small muted">' + t.qty + " sold</div>" +
        '<div style="font-weight:700;min-width:58px;text-align:right">' + money(t.revenue) + "</div></div>";
    }).join("") || '<div class="muted small">No sales yet today.</div>';
    return (
      viewTitle("Today", new Date().toDateString()) +
      '<div class="kpi-grid">' +
      '<div class="kpi hero"><div class="k-val">' + money(r.gross) + '</div><div class="k-lab">Gross sales today</div></div>' +
      '<div class="kpi"><div class="k-val">' + r.order_count + '</div><div class="k-lab">Orders</div></div>' +
      '<div class="kpi"><div class="k-val">' + money(r.avg_order) + '</div><div class="k-lab">Avg order</div></div>' +
      "</div>" +
      '<div class="cat-head">Payments</div>' +
      '<div class="pay-split">' +
      payTile("Cash", r.by_payment.cash) + payTile("UPI", r.by_payment.upi) + payTile("Card", r.by_payment.card) +
      "</div>" +
      (r.by_payment.unset ? '<div class="fab-note">' + money(r.by_payment.unset) + " marked unpaid</div>" : "") +
      ((r.low_stock && r.low_stock.length)
        ? '<div class="cat-head">Refill needed</div><div class="card" style="border-left:5px solid var(--red)">' +
          r.low_stock.map(function (m) {
            return '<div class="between" style="padding:5px 0"><span>' + esc(m.name) +
              '</span><span class="stock-low" style="font-weight:800">' + m.stock + " left</span></div>";
          }).join("") + "</div>"
        : "") +
      '<div class="cat-head">Live orders</div>' +
      renderActiveMini() +
      '<div class="cat-head">Top sellers</div><div class="card">' + top + "</div>"
    );
  }
  function payTile(label, val) {
    return '<div class="ps"><div class="v">' + money(val || 0) + '</div><div class="small muted">' + label + "</div></div>";
  }
  function renderActiveMini() {
    var active = S.orders.filter(function (o) { return ["new", "preparing", "ready"].indexOf(o.status) >= 0; });
    if (!active.length) return '<div class="card muted small">No live orders right now.</div>';
    return active.slice(0, 6).map(function (o) {
      return '<div class="card" style="padding:10px 12px"><div class="between">' +
        '<div class="row" style="gap:8px"><span class="token-badge">#' + o.token_number + "</span>" + statusPill(o.status) + "</div>" +
        "<div><b>" + money(o.total) + "</b></div></div></div>";
    }).join("");
  }

  /* ---------- admin menu ---------- */
  function renderMenuAdmin() {
    var e = S.editItem;
    var form =
      '<div class="card"><h3 style="margin-bottom:10px">' + (e ? "Edit item" : "Add menu item") + "</h3>" +
      '<div class="field"><label>Name</label><input id="mi-name" value="' + esc(e ? e.name : "") + '" placeholder="e.g. Chicken Bao"></div>' +
      '<div class="row"><div class="field grow"><label>Category</label><input id="mi-cat" value="' + esc(e ? e.category : "") + '" placeholder="Momos" list="cat-list"></div>' +
      '<div class="field" style="width:110px"><label>Price ₹</label><input id="mi-price" type="number" inputmode="decimal" value="' + (e ? e.price : "") + '"></div></div>' +
      '<datalist id="cat-list">' + uniqueCats().map(function (c) { return '<option value="' + esc(c) + '">'; }).join("") + "</datalist>" +
      '<div class="field"><label>Prepared at</label><div class="pay-opts">' +
      '<button data-prep="cart" class="' + (prepSel() === "cart" ? "active" : "") + '">🛒 Cart</button>' +
      '<button data-prep="kitchen" class="' + (prepSel() === "kitchen" ? "active" : "") + '">🍳 Back kitchen</button></div></div>' +
      '<div class="row"><div class="field grow"><label>Starting stock <span class="muted">(blank = don\'t track)</span></label>' +
      '<input id="mi-stock" type="number" inputmode="numeric" value="' + (e && e.stock !== null && e.stock !== undefined ? e.stock : "") + '" placeholder="e.g. 40"></div>' +
      '<div class="field" style="width:120px"><label>Alert at</label><input id="mi-low" type="number" inputmode="numeric" value="' + (e ? e.low_stock : 10) + '"></div></div>' +
      '<div class="row" style="gap:8px">' +
      '<button class="btn btn-primary grow" data-act="save-menu">' + (e ? "Save changes" : "Add item") + "</button>" +
      (e ? '<button class="btn btn-outline" data-act="cancel-edit">Cancel</button>' : "") +
      "</div></div>";
    var byCat = {};
    S.menu.forEach(function (m) { (byCat[m.category] = byCat[m.category] || []).push(m); });
    var list = Object.keys(byCat).sort().map(function (cat) {
      return '<div class="cat-head">' + esc(cat) + "</div><div class='card'>" +
        byCat[cat].map(menuAdminRow).join("") + "</div>";
    }).join("");
    return viewTitle("Menu", S.menu.length + " items") + form + list;
  }
  function prepSel() { return S.editItem ? S.editItem.prep_location : (S._newPrep || "cart"); }
  function menuAdminRow(m) {
    var tracked = m.stock !== null && m.stock !== undefined;
    var isLow = tracked && m.stock <= m.low_stock;
    var sub = money(m.price) +
      (tracked
        ? ' · <span class="' + (isLow ? "stock-low" : "muted") + '" style="font-weight:600">' + m.stock + " in stock</span>"
        : ' · <span class="muted">stock not tracked</span>') +
      (m.is_active ? "" : ' · <span class="muted">hidden</span>');
    return (
      '<div class="list-row ' + (m.is_active ? "" : "inactive") + '">' +
      '<div class="grow" style="min-width:130px"><div style="font-weight:600">' + esc(m.name) + " " + prepBadge(m.prep_location) + "</div>" +
      '<div class="small">' + sub + "</div></div>" +
      (tracked ? '<button class="btn btn-sm btn-blue" data-restock="' + m.id + '">Stock</button>' : "") +
      '<button class="btn btn-sm btn-outline" data-edit="' + m.id + '">Edit</button>' +
      '<button class="btn btn-sm btn-danger" data-del="' + m.id + '">Remove</button>' +
      "</div>"
    );
  }

  /* ---------- admin staff ---------- */
  function renderStaff() {
    var form =
      '<div class="card"><h3 style="margin-bottom:10px">Add staff login</h3>' +
      '<div class="field"><label>Name</label><input id="su-name" placeholder="e.g. Priya"></div>' +
      '<div class="row"><div class="field grow"><label>Username</label><input id="su-user" autocapitalize="none" placeholder="priya"></div>' +
      '<div class="field" style="width:130px"><label>Role</label><select id="su-role"><option value="seller">Seller</option><option value="kitchen">Kitchen</option><option value="admin">Admin</option></select></div></div>' +
      '<div class="field"><label>Password</label><input id="su-pass" placeholder="min 4 characters"></div>' +
      '<button class="btn btn-primary btn-block" data-act="add-user">Add staff</button></div>';
    var rows = S.users.map(function (u) {
      return '<div class="list-row ' + (u.is_active ? "" : "inactive") + '">' +
        '<div class="grow"><div style="font-weight:600">' + esc(u.name) + '</div>' +
        '<div class="small muted">@' + esc(u.username) + " · " + u.role + "</div></div>" +
        '<button class="btn btn-sm btn-outline" data-resetpw="' + u.id + '">Reset PW</button>' +
        (u.id === S.user.id ? '<span class="small muted">you</span>' :
          '<button class="btn btn-sm btn-danger" data-deluser="' + u.id + '">Remove</button>') +
        "</div>";
    }).join("");
    return viewTitle("Staff", S.users.length + " people") + form + "<div class='card'>" + (rows || "<div class='muted small'>No staff yet.</div>") + "</div>";
  }

  /* ---------- shared bits ---------- */
  function viewTitle(t, sub) {
    return '<div class="view-title">' + esc(t) + (sub ? "<small>" + esc(sub) + "</small>" : "") + "</div>";
  }
  function emptyState(ico, title, sub) {
    return '<div class="empty"><div class="e-ico">' + ico + "</div><div style='font-weight:700'>" + esc(title) + "</div>" +
      (sub ? '<div class="small">' + esc(sub) + "</div>" : "") + "</div>";
  }

  /* ================= WIRING ================= */
  function wire() {
    // login screen
    if (!S.user) {
      root.querySelectorAll("[data-role]").forEach(function (b) {
        b.onclick = function () { S.loginRole = b.getAttribute("data-role"); render(); };
      });
      var lb = root.querySelector('[data-act="login"]');
      if (lb) lb.onclick = function () {
        var u = root.querySelector("#lg-user").value.trim();
        var p = root.querySelector("#lg-pass").value;
        lb.disabled = true; lb.textContent = "Logging in…";
        doLogin(u, p).catch(function (e) { toast(e.message, "err"); lb.disabled = false; lb.textContent = "Log in"; });
      };
      return;
    }
    wireNav();
    // one delegated click handler for the app body
    root.onclick = onClick;
    // load lists lazily for admin views
    if (S.view === "staff" && !S._usersLoaded) {
      S._usersLoaded = true;
      api("/users").then(function (d) { S.users = d.users; render(); });
    }
  }

  function wireNav() {
    document.querySelectorAll("[data-nav]").forEach(function (b) {
      b.onclick = function () {
        S.view = b.getAttribute("data-nav");
        S.editItem = null;
        if (S.view === "staff") { S._usersLoaded = false; }
        if (S.view === "dashboard") loadReport().then(render);
        render();
      };
    });
  }

  function onClick(ev) {
    var t = ev.target.closest("[data-act],[data-inc],[data-dec],[data-cat],[data-pay],[data-order],[data-nav],[data-edit],[data-del],[data-prep],[data-deluser],[data-resetpw],[data-restock]");
    if (!t) return;
    var a;
    if ((a = t.getAttribute("data-inc"))) return addToCart(a);
    if ((a = t.getAttribute("data-dec"))) return decCart(a);
    if ((a = t.getAttribute("data-cat"))) { S.cat = a; return render(); }
    if ((a = t.getAttribute("data-pay"))) { S.payment = a; syncNote(); return render(); }
    if ((a = t.getAttribute("data-prep"))) {
      if (S.editItem) S.editItem.prep_location = a; else S._newPrep = a;
      // update selection in place — a full re-render would wipe typed fields
      t.parentElement.querySelectorAll("[data-prep]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-prep") === a);
      });
      return;
    }
    if ((a = t.getAttribute("data-order"))) {
      var st = t.getAttribute("data-status");
      var map = { start: "preparing", ready: "ready", served: "served", cancel: "cancelled" };
      return setStatus(a, map[st]);
    }
    if ((a = t.getAttribute("data-edit"))) {
      S.editItem = Object.assign({}, S.menu.find(function (m) { return String(m.id) === a; }));
      window.scrollTo(0, 0); return render();
    }
    if ((a = t.getAttribute("data-del"))) return removeMenu(a);
    if ((a = t.getAttribute("data-restock"))) return restock(a);
    if ((a = t.getAttribute("data-deluser"))) return removeUser(a);
    if ((a = t.getAttribute("data-resetpw"))) return resetPw(a);

    var act = t.getAttribute("data-act");
    if (act === "logout") return doLogout();
    if (act === "open-sheet") { syncNote(); S.sheetOpen = true; return render(); }
    if (act === "close-sheet") { syncNote(); S.sheetOpen = false; return render(); }
    if (act === "place") return placeOrder();
    if (act === "toggle-kitchen") { S.kitchenAll = !S.kitchenAll; return render(); }
    if (act === "save-menu") return saveMenu();
    if (act === "cancel-edit") { S.editItem = null; return render(); }
    if (act === "add-user") return addUser();
  }

  function syncNote() {
    var n = document.querySelector("#cart-note");
    if (n) S.note = n.value;
  }

  /* ---------- admin ops ---------- */
  function saveMenu() {
    var name = document.querySelector("#mi-name").value.trim();
    var cat = document.querySelector("#mi-cat").value.trim() || "General";
    var price = parseFloat(document.querySelector("#mi-price").value);
    var prep = prepSel();
    if (!name) return toast("Enter an item name", "err");
    if (isNaN(price) || price < 0) return toast("Enter a valid price", "err");
    var stockRaw = document.querySelector("#mi-stock").value.trim();
    var lowRaw = document.querySelector("#mi-low").value.trim();
    var body = {
      name: name, category: cat, price: price, prep_location: prep,
      stock: stockRaw === "" ? "" : stockRaw,        // "" => not tracked
      low_stock: lowRaw === "" ? 10 : lowRaw,
    };
    var req = S.editItem
      ? api("/menu/" + S.editItem.id, { method: "PUT", body: body })
      : api("/menu", { method: "POST", body: body });
    req.then(function () {
      S.editItem = null; S._newPrep = "cart";
      return api("/menu").then(function (d) { S.menu = d.items; });
    }).then(function () { toast("Menu saved", "ok"); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }
  function restock(id) {
    var m = S.menu.find(function (x) { return String(x.id) === String(id); });
    if (!m) return;
    var cur = (m.stock === null || m.stock === undefined) ? 0 : m.stock;
    var input = prompt("Restock " + m.name + " — current: " + cur +
      "\nEnter how many to ADD (e.g. 20), or =50 to set the total:", "");
    if (input === null) return;
    input = input.trim();
    if (input === "") return;
    var body;
    if (input.charAt(0) === "=") {
      body = { stock: input.slice(1).trim() };
    } else {
      var n = parseInt(input, 10);
      if (isNaN(n)) return toast("Enter a number", "err");
      body = { add: n };
    }
    api("/menu/" + id + "/stock", { method: "POST", body: body })
      .then(function () { return api("/menu"); })
      .then(function (d) { S.menu = d.items; toast("Stock updated", "ok"); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }
  function removeMenu(id) {
    if (!confirm("Remove this item from the menu? Past orders keep it.")) return;
    api("/menu/" + id, { method: "DELETE" })
      .then(function () { return api("/menu"); })
      .then(function (d) { S.menu = d.items; toast("Item removed", "ok"); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }
  function addUser() {
    var body = {
      name: document.querySelector("#su-name").value.trim(),
      username: document.querySelector("#su-user").value.trim(),
      role: document.querySelector("#su-role").value,
      password: document.querySelector("#su-pass").value,
    };
    api("/users", { method: "POST", body: body })
      .then(function () { return api("/users"); })
      .then(function (d) { S.users = d.users; toast("Staff added", "ok"); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }
  function removeUser(id) {
    if (!confirm("Remove this staff login?")) return;
    api("/users/" + id, { method: "DELETE" })
      .then(function () { return api("/users"); })
      .then(function (d) { S.users = d.users; toast("Removed", "ok"); render(); })
      .catch(function (e) { toast(e.message, "err"); });
  }
  function resetPw(id) {
    var pw = prompt("New password (min 4 characters):");
    if (!pw) return;
    api("/users/" + id, { method: "PATCH", body: { password: pw } })
      .then(function () { toast("Password reset", "ok"); })
      .catch(function (e) { toast(e.message, "err"); });
  }

  /* ---------- boot ---------- */
  function boot() {
    if ("serviceWorker" in navigator && location.protocol === "https:") {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    }
    if (S.token && S.user) {
      // validate token, then start
      api("/me").then(function (d) {
        S.user = d.user;
        afterLogin();
      }).catch(function () { doLogout(); });
    } else {
      render();
    }
  }
  boot();
})();
