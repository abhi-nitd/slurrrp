"""SQLite storage layer for slurrrp. Thread-safe access via a single guarded
connection (fine for a single cart / a handful of devices)."""
import os
import sqlite3
import threading
from datetime import datetime

import auth

DATA_DIR = auth.DATA_DIR  # honours SLURRRP_DATA_DIR (e.g. a host's persistent disk)
DB_FILE = os.path.join(DATA_DIR, "slurrrp.db")

_conn = None
_lock = threading.RLock()


def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def query(sql, params=()):
    with _lock:
        cur = get_conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def transaction():
    """Return the lock + connection for multi-statement atomic work."""
    return _lock, get_conn()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('admin','kitchen','seller')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS menu_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'General',
    price         REAL NOT NULL,
    prep_location TEXT NOT NULL CHECK(prep_location IN ('cart','kitchen')) DEFAULT 'cart',
    is_active     INTEGER NOT NULL DEFAULT 1,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_number    INTEGER NOT NULL,
    order_date      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new'
                     CHECK(status IN ('new','preparing','ready','served','cancelled')),
    payment_mode    TEXT CHECK(payment_mode IN ('cash','upi','card')),
    needs_kitchen   INTEGER NOT NULL DEFAULT 0,
    subtotal        REAL NOT NULL DEFAULT 0,
    total           REAL NOT NULL DEFAULT 0,
    note            TEXT,
    created_by      INTEGER,
    created_by_name TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_item_id  INTEGER,
    name          TEXT NOT NULL,
    price         REAL NOT NULL,
    qty           INTEGER NOT NULL,
    prep_location TEXT NOT NULL,
    line_total    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
"""

SEED_USERS = [
    ("Admin", "admin", "slurrrp123", "admin"),
    ("Kitchen", "kitchen", "slurrrp123", "kitchen"),
    ("Cart Seller", "seller", "slurrrp123", "seller"),
]

# (name, category, price, prep_location)
SEED_MENU = [
    ("Veg Momos (Steamed)", "Momos", 80, "cart"),
    ("Chicken Momos (Steamed)", "Momos", 110, "cart"),
    ("Veg Spring Rolls", "Starters", 90, "cart"),
    ("Prawn Crackers", "Starters", 60, "cart"),
    ("Edamame (Salted)", "Starters", 100, "cart"),
    ("Bubble Tea (Classic)", "Beverages", 120, "cart"),
    ("Thai Iced Tea", "Beverages", 90, "cart"),
    ("Fresh Lime Soda", "Beverages", 50, "cart"),
    ("Veg Hakka Noodles", "Noodles", 140, "kitchen"),
    ("Chicken Hakka Noodles", "Noodles", 170, "kitchen"),
    ("Veg Pad Thai", "Noodles", 180, "kitchen"),
    ("Veg Fried Rice", "Rice", 130, "kitchen"),
    ("Chicken Fried Rice", "Rice", 160, "kitchen"),
    ("Chicken Ramen Bowl", "Ramen", 220, "kitchen"),
    ("Chilli Chicken (Dry)", "Mains", 200, "kitchen"),
    ("Veg Manchurian", "Mains", 160, "kitchen"),
    ("Thai Green Curry + Rice", "Mains", 210, "kitchen"),
    ("Dim Sum Platter", "Momos", 190, "kitchen"),
]


def init_db():
    conn = get_conn()
    with _lock:
        conn.executescript(SCHEMA)
        conn.commit()
    now = datetime.now().isoformat(timespec="seconds")

    if not query_one("SELECT id FROM users LIMIT 1"):
        for name, username, pw, role in SEED_USERS:
            execute(
                "INSERT INTO users (name, username, password_hash, role, created_at)"
                " VALUES (?,?,?,?,?)",
                (name, username, auth.hash_password(pw), role, now),
            )

    if not query_one("SELECT id FROM menu_items LIMIT 1"):
        for i, (name, cat, price, prep) in enumerate(SEED_MENU):
            execute(
                "INSERT INTO menu_items (name, category, price, prep_location,"
                " sort_order, created_at) VALUES (?,?,?,?,?,?)",
                (name, cat, price, prep, i, now),
            )
