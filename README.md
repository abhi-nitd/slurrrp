# slurrrp — Cart & Kitchen app

A phone app for your Pan-Asian cart: the seller takes orders, the total shows
instantly, the order is **relayed live to the back kitchen**, and you (admin)
see live sales. Built to run on your own machine with **zero installs** — pure
Python standard library, no packages, no database server.

---

## Who logs in (roles)

| Role | Sees | Can do |
|------|------|--------|
| **Seller** (cart) | Menu, cart, orders | Build an order, show the total, take payment, mark served |
| **Kitchen** | Live ticket board | Get a sound + ticket for every order, mark *Cooking* → *Ready* |
| **Admin** (owner) | Dashboard | Everything + manage menu, manage staff, see sales & top sellers |

**Default logins** (change them under **Staff** after first login):

```
admin   / slurrrp123      seller  / slurrrp123      kitchen / slurrrp123
```

---

## Run it

1. Open a terminal in the `slurrrp` folder and run:

   ```
   python server.py
   ```

   (or just double-click **`start-slurrrp.bat`** on Windows)

2. It prints two URLs:
   - **This device:** `http://localhost:8000`
   - **Phones (same Wi-Fi):** `http://<your-ip>:8000`  ← open this on each phone

3. On each phone, open the Wi-Fi URL and log in with the right role. Keep the
   kitchen tablet on this page — new orders pop in with a sound automatically.

> The seller, kitchen and admin can all be on different phones at the same time.
> Everything syncs live.

### Install it like a real app
On the phone browser menu choose **“Add to Home Screen”** — slurrrp opens
full-screen with its own icon. (Home-screen install + offline mode need HTTPS;
see *Going online* below. On plain Wi-Fi it still runs fine in the browser.)

---

## How orders flow

1. **Seller** taps items → the running **total** updates → picks **Cash / UPI /
   Card** → **Place order**. Each order gets a **daily token number** (#1, #2 …).
2. Items are tagged **🛒 Cart** or **🍳 Back kitchen**. If any item needs the
   kitchen, the order instantly appears on the **Kitchen board** with a sound and
   a notification.
3. **Kitchen** taps **Start cooking** → **Mark ready**. The seller’s screen shows
   **READY** (with a sound) so they know to hand it over, then taps **Served**.
4. **Admin** watches gross sales, cash/UPI/card split, live orders and top
   sellers update in real time.

Add or remove menu items anytime under **Menu** (removing hides an item from the
cart but keeps it on past orders).

---

## Your data

- Everything is stored locally in **`slurrrp/data/slurrrp.db`** (SQLite).
- To **back up**, copy that file. To **start fresh**, delete the `data` folder —
  it re-creates the default menu and logins on next start.

---

## Use it across two locations (kitchen on a different network)

Your seller and kitchen are on a **different network** from this laptop, so they
can't use the `192.168…` address. **Recommended:** put slurrrp online for free —
you get a fixed link that works from any phone on any network, and the laptop
doesn't need to be on at all.

➡️ Follow **[DEPLOY.md](DEPLOY.md)** (~15 min, free, no card).

### Quick test only: temporary public link from this laptop
For a fast look before deploying, double-click **`start-slurrrp-online.bat`** —
it shows a temporary `https://…trycloudflare.com` link. Note: this free tunnel
is **not reliable for daily trading** (it drops and the address keeps changing),
and the laptop must stay on. Use it to test; use DEPLOY.md for real use.
Stop it with **`stop-slurrrp.bat`**.

---

## Under the hood

- `server.py` — HTTP server, routing, live event stream (SSE), serves the app
- `db.py` — SQLite schema + seeded Pan-Asian menu
- `auth.py` — PBKDF2 password hashing + signed login tokens, role checks
- `events.py` — pushes new orders to kitchen/admin phones instantly
- `api.py` — all REST endpoints
- `public/` — the installable mobile app (HTML/CSS/JS, no build step)

No third-party packages. Runs on any machine with **Python 3.8+**.
