"""REST API handlers for slurrrp.

Each handler receives a `ctx` dict with: user, params, body, query.
Handlers return a JSON-serialisable value, or raise ApiError.
"""
import threading
import time
from datetime import datetime

import auth
import db
import events


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---- login rate limiting (blunts password guessing on the public login) ----
_LOGIN_WINDOW = 600      # seconds
_LOGIN_MAX_FAILS = 10    # per IP within the window
_attempts = {}
_attempts_lock = threading.Lock()


def _rate_check(ip):
    now = time.time()
    with _attempts_lock:
        arr = [t for t in _attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
        _attempts[ip] = arr
        if len(arr) >= _LOGIN_MAX_FAILS:
            raise ApiError(429, "Too many login attempts. Please wait a few minutes.")


def _rate_fail(ip):
    with _attempts_lock:
        _attempts.setdefault(ip, []).append(time.time())


def _rate_clear(ip):
    with _attempts_lock:
        _attempts.pop(ip, None)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _require(ctx, *roles):
    user = ctx.get("user")
    if not user:
        raise ApiError(401, "Please log in.")
    if roles and user["role"] not in roles:
        raise ApiError(403, "You don't have access to this action.")
    return user


def _num(v, field):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{field} must be a number.")


def _parse_stock(v):
    """None/blank => untracked. Otherwise a non-negative integer count."""
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "Stock must be a whole number.")
    if n < 0:
        raise ApiError(400, "Stock can't be negative.")
    return n


def _emit_stock(item_ids):
    """Broadcast updated stock for the given items, and a low-stock alert."""
    ids = [i for i in set(item_ids) if i is not None]
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    items = db.query(
        f"SELECT id, name, stock, low_stock, prep_location FROM menu_items WHERE id IN ({ph})",
        tuple(ids),
    )
    events.publish(["seller", "kitchen", "admin"], "stock:update", {"items": items})
    low = [i for i in items if i["stock"] is not None and i["stock"] <= i["low_stock"]]
    if low:
        events.publish(["kitchen", "admin"], "stock:low", {"items": low})


# ---- auth ------------------------------------------------------------------

def login(ctx):
    body = ctx["body"]
    ip = ctx.get("ip") or "?"
    _rate_check(ip)
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    row = db.query_one("SELECT * FROM users WHERE username=? AND is_active=1", (username,))
    if not row or not auth.verify_password(password, row["password_hash"]):
        _rate_fail(ip)
        raise ApiError(401, "Wrong username or password.")
    _rate_clear(ip)
    user = {"id": row["id"], "name": row["name"], "username": row["username"], "role": row["role"]}
    token = auth.make_token(user)
    return {"token": token, "user": user}


def me(ctx):
    user = _require(ctx)
    return {"user": user}


# ---- menu ------------------------------------------------------------------

def list_menu(ctx):
    user = _require(ctx)
    include_all = ctx["query"].get("all") == "1" and user["role"] == "admin"
    where = "" if include_all else "WHERE is_active=1"
    rows = db.query(
        f"SELECT * FROM menu_items {where} ORDER BY category, sort_order, name"
    )
    return {"items": rows}


def create_menu(ctx):
    _require(ctx, "admin")
    b = ctx["body"]
    name = (b.get("name") or "").strip()
    if not name:
        raise ApiError(400, "Item name is required.")
    price = _num(b.get("price"), "Price")
    if price < 0:
        raise ApiError(400, "Price cannot be negative.")
    category = (b.get("category") or "General").strip() or "General"
    prep = b.get("prep_location", "cart")
    if prep not in ("cart", "kitchen"):
        raise ApiError(400, "prep_location must be 'cart' or 'kitchen'.")
    stock = _parse_stock(b.get("stock"))
    low_stock = int(_num(b.get("low_stock"), "Low-stock alert")) if b.get("low_stock") not in (None, "") else 10
    mid = db.execute(
        "INSERT INTO menu_items (name, category, price, prep_location, stock, low_stock, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (name, category, price, prep, stock, low_stock, _now()),
    )
    item = db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))
    events.publish(["seller", "kitchen", "admin"], "menu:updated", {})
    return {"item": item}


def update_menu(ctx):
    _require(ctx, "admin")
    mid = ctx["params"]["id"]
    item = db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))
    if not item:
        raise ApiError(404, "Menu item not found.")
    b = ctx["body"]
    name = (b.get("name") or item["name"]).strip()
    category = (b.get("category") or item["category"]).strip()
    price = _num(b["price"], "Price") if "price" in b else item["price"]
    prep = b.get("prep_location", item["prep_location"])
    if prep not in ("cart", "kitchen"):
        raise ApiError(400, "prep_location must be 'cart' or 'kitchen'.")
    is_active = int(bool(b["is_active"])) if "is_active" in b else item["is_active"]
    stock = _parse_stock(b["stock"]) if "stock" in b else item["stock"]
    low_stock = int(_num(b["low_stock"], "Low-stock alert")) if b.get("low_stock") not in (None, "") else item["low_stock"]
    db.execute(
        "UPDATE menu_items SET name=?, category=?, price=?, prep_location=?, is_active=?,"
        " stock=?, low_stock=? WHERE id=?",
        (name, category, price, prep, is_active, stock, low_stock, mid),
    )
    events.publish(["seller", "kitchen", "admin"], "menu:updated", {})
    _emit_stock([mid])
    return {"item": db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))}


def delete_menu(ctx):
    _require(ctx, "admin")
    mid = ctx["params"]["id"]
    item = db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))
    if not item:
        raise ApiError(404, "Menu item not found.")
    # Soft-delete to preserve historical orders that reference this item.
    db.execute("UPDATE menu_items SET is_active=0 WHERE id=?", (mid,))
    events.publish(["seller", "kitchen", "admin"], "menu:updated", {})
    return {"ok": True}


# ---- orders ----------------------------------------------------------------

def _order_dict(order):
    items = db.query(
        "SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order["id"],)
    )
    out = dict(order)
    out["items"] = items
    return out


def list_orders(ctx):
    user = _require(ctx)
    q = ctx["query"]
    date = q.get("date") or _today()
    params = [date]
    sql = "SELECT * FROM orders WHERE order_date=?"
    if q.get("status"):
        sql += " AND status=?"
        params.append(q["status"])
    sql += " ORDER BY id DESC"
    orders = db.query(sql, tuple(params))
    return {"orders": [_order_dict(o) for o in orders]}


def get_order(ctx):
    _require(ctx)
    order = db.query_one("SELECT * FROM orders WHERE id=?", (ctx["params"]["id"],))
    if not order:
        raise ApiError(404, "Order not found.")
    return {"order": _order_dict(order)}


def create_order(ctx):
    user = _require(ctx, "seller", "admin")
    b = ctx["body"]
    raw_items = b.get("items") or []
    if not raw_items:
        raise ApiError(400, "Add at least one item to the order.")
    payment_mode = b.get("payment_mode")
    if payment_mode not in (None, "cash", "upi", "card"):
        raise ApiError(400, "Invalid payment mode.")
    note = (b.get("note") or "").strip() or None

    # Resolve and snapshot each menu item.
    resolved = []
    subtotal = 0.0
    needs_kitchen = False
    for ri in raw_items:
        mi = db.query_one(
            "SELECT * FROM menu_items WHERE id=? AND is_active=1", (ri.get("menu_item_id"),)
        )
        if not mi:
            raise ApiError(400, "One of the items is no longer available.")
        qty = int(ri.get("qty") or 0)
        if qty <= 0:
            continue
        line_total = round(mi["price"] * qty, 2)
        subtotal += line_total
        if mi["prep_location"] == "kitchen":
            needs_kitchen = True
        resolved.append((mi, qty, line_total))

    if not resolved:
        raise ApiError(400, "Add at least one item to the order.")

    total = round(subtotal, 2)
    date = _today()
    now = _now()

    lock, conn = db.transaction()
    with lock:
        try:
            # Block overselling: re-check tracked stock against live counts.
            for mi, qty, _ in resolved:
                if mi["stock"] is not None:
                    cur_stock = conn.execute(
                        "SELECT stock FROM menu_items WHERE id=?", (mi["id"],)
                    ).fetchone()["stock"]
                    if cur_stock is not None and qty > cur_stock:
                        raise ApiError(
                            409,
                            f"Only {cur_stock} left of {mi['name']}. Please adjust the order.",
                        )
            row = conn.execute(
                "SELECT COALESCE(MAX(token_number),0)+1 AS t FROM orders WHERE order_date=?",
                (date,),
            ).fetchone()
            token = row["t"]
            cur = conn.execute(
                "INSERT INTO orders (token_number, order_date, status, payment_mode,"
                " needs_kitchen, subtotal, total, note, created_by, created_by_name,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (token, date, "new", payment_mode, int(needs_kitchen), subtotal, total,
                 note, user["id"], user["name"], now, now),
            )
            oid = cur.lastrowid
            for mi, qty, line_total in resolved:
                conn.execute(
                    "INSERT INTO order_items (order_id, menu_item_id, name, price, qty,"
                    " prep_location, line_total) VALUES (?,?,?,?,?,?,?)",
                    (oid, mi["id"], mi["name"], mi["price"], qty, mi["prep_location"], line_total),
                )
                # draw down tracked inventory
                conn.execute(
                    "UPDATE menu_items SET stock = stock - ? WHERE id=? AND stock IS NOT NULL",
                    (qty, mi["id"]),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    order = _order_dict(db.query_one("SELECT * FROM orders WHERE id=?", (oid,)))
    # Relay to kitchen + admin (new order) and back to sellers (list refresh).
    events.publish(["kitchen", "admin", "seller"], "order:new", order)
    _emit_stock([mi["id"] for mi, _, _ in resolved])  # live stock + low-stock alerts
    return {"order": order}


# Status transitions and who may trigger them. Voiding/cancelling an order is
# ADMIN-ONLY on purpose: a seller must not be able to make a recorded sale (and
# its cash) disappear.
_TRANSITIONS = {
    "new": {"preparing": ("kitchen", "admin"),
            "served": ("seller", "admin"),
            "cancelled": ("admin",)},
    "preparing": {"ready": ("kitchen", "admin"),
                  "cancelled": ("admin",)},
    "ready": {"served": ("seller", "admin"),
              "cancelled": ("admin",)},
    "served": {},
    "cancelled": {},
}


def update_status(ctx):
    user = _require(ctx)
    oid = ctx["params"]["id"]
    new_status = ctx["body"].get("status")
    order = db.query_one("SELECT * FROM orders WHERE id=?", (oid,))
    if not order:
        raise ApiError(404, "Order not found.")
    allowed = _TRANSITIONS.get(order["status"], {})
    if new_status not in allowed:
        raise ApiError(400, f"Can't move an order from {order['status']} to {new_status}.")
    if user["role"] not in allowed[new_status]:
        raise ApiError(403, "You don't have access to this action.")

    restock_ids = []
    lock, conn = db.transaction()
    with lock:
        try:
            conn.execute(
                "UPDATE orders SET status=?, updated_at=? WHERE id=?",
                (new_status, _now(), oid),
            )
            if new_status == "cancelled":
                # put the items back into inventory
                for it in db.query("SELECT menu_item_id, qty FROM order_items WHERE order_id=?", (oid,)):
                    if it["menu_item_id"] is not None:
                        conn.execute(
                            "UPDATE menu_items SET stock = stock + ? WHERE id=? AND stock IS NOT NULL",
                            (it["qty"], it["menu_item_id"]),
                        )
                        restock_ids.append(it["menu_item_id"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    updated = _order_dict(db.query_one("SELECT * FROM orders WHERE id=?", (oid,)))
    events.publish(["kitchen", "admin", "seller"], "order:status", updated)
    if restock_ids:
        _emit_stock(restock_ids)
    return {"order": updated}


def set_stock(ctx):
    """Admin restock: set an absolute count, add to it, or update the threshold."""
    _require(ctx, "admin")
    mid = ctx["params"]["id"]
    item = db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))
    if not item:
        raise ApiError(404, "Menu item not found.")
    b = ctx["body"]
    if "add" in b:
        base = item["stock"] or 0
        new_stock = base + int(_num(b["add"], "Amount"))
        if new_stock < 0:
            new_stock = 0
    elif "stock" in b:
        new_stock = _parse_stock(b["stock"])
    else:
        new_stock = item["stock"]
    low_stock = item["low_stock"]
    if b.get("low_stock") not in (None, ""):
        low_stock = int(_num(b["low_stock"], "Low-stock alert"))
    db.execute("UPDATE menu_items SET stock=?, low_stock=? WHERE id=?", (new_stock, low_stock, mid))
    _emit_stock([int(mid)])
    return {"item": db.query_one("SELECT * FROM menu_items WHERE id=?", (mid,))}


# ---- users -----------------------------------------------------------------

def list_users(ctx):
    _require(ctx, "admin")
    rows = db.query(
        "SELECT id, name, username, role, is_active, created_at FROM users ORDER BY role, name"
    )
    return {"users": rows}


def create_user(ctx):
    _require(ctx, "admin")
    b = ctx["body"]
    name = (b.get("name") or "").strip()
    username = (b.get("username") or "").strip().lower()
    password = b.get("password") or ""
    role = b.get("role")
    if not name or not username:
        raise ApiError(400, "Name and username are required.")
    if role not in ("admin", "kitchen", "seller"):
        raise ApiError(400, "Role must be admin, kitchen or seller.")
    if len(password) < 4:
        raise ApiError(400, "Password must be at least 4 characters.")
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        raise ApiError(409, "That username is already taken.")
    uid = db.execute(
        "INSERT INTO users (name, username, password_hash, role, created_at)"
        " VALUES (?,?,?,?,?)",
        (name, username, auth.hash_password(password), role, _now()),
    )
    row = db.query_one(
        "SELECT id, name, username, role, is_active, created_at FROM users WHERE id=?", (uid,)
    )
    return {"user": row}


def update_user(ctx):
    admin = _require(ctx, "admin")
    uid = int(ctx["params"]["id"])
    user = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not user:
        raise ApiError(404, "User not found.")
    b = ctx["body"]
    name = (b.get("name") or user["name"]).strip()
    role = b.get("role", user["role"])
    if role not in ("admin", "kitchen", "seller"):
        raise ApiError(400, "Invalid role.")
    is_active = int(bool(b["is_active"])) if "is_active" in b else user["is_active"]
    if uid == admin["id"] and (not is_active or role != "admin"):
        raise ApiError(400, "You can't lock yourself out of admin.")
    db.execute("UPDATE users SET name=?, role=?, is_active=? WHERE id=?",
               (name, role, is_active, uid))
    if b.get("password"):
        if len(b["password"]) < 4:
            raise ApiError(400, "Password must be at least 4 characters.")
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (auth.hash_password(b["password"]), uid))
    row = db.query_one(
        "SELECT id, name, username, role, is_active, created_at FROM users WHERE id=?", (uid,)
    )
    return {"user": row}


def delete_user(ctx):
    admin = _require(ctx, "admin")
    uid = int(ctx["params"]["id"])
    if uid == admin["id"]:
        raise ApiError(400, "You can't remove your own account.")
    if not db.query_one("SELECT id FROM users WHERE id=?", (uid,)):
        raise ApiError(404, "User not found.")
    db.execute("UPDATE users SET is_active=0 WHERE id=?", (uid,))
    return {"ok": True}


# ---- reports ---------------------------------------------------------------

def report_summary(ctx):
    _require(ctx, "admin")
    date = ctx["query"].get("date") or _today()
    counted = "status != 'cancelled'"
    total_row = db.query_one(
        f"SELECT COUNT(*) c, COALESCE(SUM(total),0) g FROM orders"
        f" WHERE order_date=? AND {counted}", (date,)
    )
    pay_rows = db.query(
        f"SELECT payment_mode, COUNT(*) c, COALESCE(SUM(total),0) g FROM orders"
        f" WHERE order_date=? AND {counted} GROUP BY payment_mode", (date,)
    )
    by_payment = {"cash": 0, "upi": 0, "card": 0, "unset": 0}
    for r in pay_rows:
        by_payment[r["payment_mode"] or "unset"] = r["g"]
    status_rows = db.query(
        "SELECT status, COUNT(*) c FROM orders WHERE order_date=? GROUP BY status", (date,)
    )
    by_status = {r["status"]: r["c"] for r in status_rows}
    top = db.query(
        f"SELECT oi.name, SUM(oi.qty) qty, SUM(oi.line_total) revenue"
        f" FROM order_items oi JOIN orders o ON o.id=oi.order_id"
        f" WHERE o.order_date=? AND o.{counted}"
        f" GROUP BY oi.name ORDER BY qty DESC LIMIT 8", (date,)
    )
    count = total_row["c"] or 0
    gross = round(total_row["g"] or 0, 2)
    inventory = db.query(
        "SELECT id, name, category, stock, low_stock, prep_location FROM menu_items"
        " WHERE is_active=1 AND stock IS NOT NULL ORDER BY stock ASC, category, name"
    )
    low_stock = [i for i in inventory if i["stock"] <= i["low_stock"]]
    return {
        "date": date,
        "order_count": count,
        "gross": gross,
        "avg_order": round(gross / count, 2) if count else 0,
        "by_payment": by_payment,
        "by_status": by_status,
        "top_items": top,
        "inventory": inventory,
        "low_stock": low_stock,
    }


def inventory(ctx):
    """Tracked-stock list — visible to seller/kitchen/admin."""
    _require(ctx)
    rows = db.query(
        "SELECT id, name, category, stock, low_stock, prep_location FROM menu_items"
        " WHERE is_active=1 AND stock IS NOT NULL ORDER BY stock ASC, category, name"
    )
    return {"items": rows, "low": [r for r in rows if r["stock"] <= r["low_stock"]]}


def health(ctx):
    return {"ok": True}
