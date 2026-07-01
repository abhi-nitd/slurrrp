"""REST API handlers for slurrrp.

Each handler receives a `ctx` dict with: user, params, body, query.
Handlers return a JSON-serialisable value, or raise ApiError.
"""
from datetime import datetime

import auth
import db
import events


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


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


# ---- auth ------------------------------------------------------------------

def login(ctx):
    body = ctx["body"]
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    row = db.query_one("SELECT * FROM users WHERE username=? AND is_active=1", (username,))
    if not row or not auth.verify_password(password, row["password_hash"]):
        raise ApiError(401, "Wrong username or password.")
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
    mid = db.execute(
        "INSERT INTO menu_items (name, category, price, prep_location, created_at)"
        " VALUES (?,?,?,?,?)",
        (name, category, price, prep, _now()),
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
    db.execute(
        "UPDATE menu_items SET name=?, category=?, price=?, prep_location=?, is_active=?"
        " WHERE id=?",
        (name, category, price, prep, is_active, mid),
    )
    events.publish(["seller", "kitchen", "admin"], "menu:updated", {})
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
        conn.commit()

    order = _order_dict(db.query_one("SELECT * FROM orders WHERE id=?", (oid,)))
    # Relay to kitchen + admin (new order) and back to sellers (list refresh).
    events.publish(["kitchen", "admin", "seller"], "order:new", order)
    return {"order": order}


# status transitions and who may trigger them
_TRANSITIONS = {
    "new": {"preparing": ("kitchen", "admin"),
            "served": ("seller", "admin"),
            "cancelled": ("seller", "admin")},
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
    db.execute(
        "UPDATE orders SET status=?, updated_at=? WHERE id=?", (new_status, _now(), oid)
    )
    updated = _order_dict(db.query_one("SELECT * FROM orders WHERE id=?", (oid,)))
    events.publish(["kitchen", "admin", "seller"], "order:status", updated)
    return {"order": updated}


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
    return {
        "date": date,
        "order_count": count,
        "gross": gross,
        "avg_order": round(gross / count, 2) if count else 0,
        "by_payment": by_payment,
        "by_status": by_status,
        "top_items": top,
    }


def health(ctx):
    return {"ok": True, "connections": events.connection_count()}
