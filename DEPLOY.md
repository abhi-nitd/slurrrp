# Put slurrrp online for free (no laptop needed)

This hosts slurrrp on **Render's free plan**. You get a fixed web link like
`https://slurrrp-xxxx.onrender.com` that works from any phone, on any network —
and your home laptop can be off.

Total time: ~15 minutes, one time. No coding, no card.

---

## Step 1 — Put the code on GitHub (free)

1. Create a free account at **https://github.com** (skip if you have one).
2. Click **＋ (top-right) → New repository**. Name it `slurrrp`, keep it
   **Private**, click **Create repository**.
3. On the new repo page, click **“uploading an existing file.”**
4. From the `slurrrp` folder on your laptop, drag in **these items only**:
   - `server.py`, `db.py`, `auth.py`, `events.py`, `api.py`
   - `requirements.txt`, `render.yaml`
   - the whole **`public`** folder
   - (optional) `README.md`
   > ⚠️ Do **not** upload the `data` or `tools` folders.
5. Click **Commit changes**.

## Step 2 — Deploy on Render (free)

1. Go to **https://render.com** and **Sign up with GitHub**.
2. Click **New ＋ → Blueprint**.
3. Pick your **slurrrp** repo → Render reads `render.yaml` → click **Apply**.
4. Wait ~2–3 minutes for it to build. When it says **Live**, you'll see a URL
   like **`https://slurrrp-xxxx.onrender.com`** at the top.

## Step 3 — Go live

1. Open that URL. Log in as **admin / slurrrp123**.
2. Go to **Staff → Reset PW** and change all three passwords.
3. Share the URL with your seller and kitchen. They open it on their phones
   (any network), log in, and **Add to Home Screen** for an app icon.

That link never changes. Bookmark it.

---

## Free-plan trade-offs (and how to remove them)

- **It sleeps when idle.** After ~15 minutes with nobody using it, the first
  open takes ~40 seconds to wake up. While your staff have the app open it stays
  awake on its own (the app quietly pings it). Fine for a working cart.
- **Data isn't permanent on the free plan.** If Render restarts the app (e.g.
  when you redeploy), the menu edits, staff and order history reset to defaults.
  Day-to-day use is fine; long-term history is not kept.

**To make data permanent + remove sleeping** (recommended once you're settled):
upgrade the service to Render's cheapest paid instance, add a **Disk** mounted at
`/data`, and set the env var **`SLURRRP_DATA_DIR=/data`** (already stubbed in
`render.yaml`). Ask me and I'll walk you through it — 5 minutes.

> Prefer a different free host? Koyeb and Fly.io work the same way
> (`python server.py`, it reads the `PORT` they give it). Render is the simplest.
