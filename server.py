"""slurrrp cart app — self-hosted server (Python standard library only).

Run:  python server.py           (defaults to 0.0.0.0:8000)
      python server.py 8080      (custom port)

Then open the printed URL on each phone (seller / kitchen / admin) on the same
Wi-Fi. Default logins are printed on startup.
"""
import json
import mimetypes
import os
import queue
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import api
import auth
import db
import events

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(HERE, "public")

# Content Security Policy for the app shell. Everything is same-origin; inline
# scripts are disallowed (blocks injected XSS), inline styles are allowed for the
# app's style="" attributes, and the page can't be embedded in a frame.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; "
    "worker-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")

# method, compiled path regex, handler
ROUTES = [
    ("POST", r"^/api/login$", api.login),
    ("GET", r"^/api/me$", api.me),
    ("GET", r"^/api/menu$", api.list_menu),
    ("POST", r"^/api/menu$", api.create_menu),
    ("PUT", r"^/api/menu/(?P<id>\d+)$", api.update_menu),
    ("DELETE", r"^/api/menu/(?P<id>\d+)$", api.delete_menu),
    ("POST", r"^/api/menu/(?P<id>\d+)/stock$", api.set_stock),
    ("GET", r"^/api/inventory$", api.inventory),
    ("GET", r"^/api/orders$", api.list_orders),
    ("POST", r"^/api/orders$", api.create_order),
    ("GET", r"^/api/orders/(?P<id>\d+)$", api.get_order),
    ("PATCH", r"^/api/orders/(?P<id>\d+)/status$", api.update_status),
    ("GET", r"^/api/users$", api.list_users),
    ("POST", r"^/api/users$", api.create_user),
    ("PATCH", r"^/api/users/(?P<id>\d+)$", api.update_user),
    ("DELETE", r"^/api/users/(?P<id>\d+)$", api.delete_user),
    ("GET", r"^/api/reports/summary$", api.report_summary),
    ("GET", r"^/api/health$", api.health),
]
COMPILED = [(m, re.compile(p), h) for (m, p, h) in ROUTES]


def parse_qs(query):
    out = {}
    for part in query.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[_unquote(k)] = _unquote(v)
        else:
            out[_unquote(part)] = ""
    return out


def _unquote(s):
    from urllib.parse import unquote_plus
    return unquote_plus(s)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "slurrrp/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def _sec_headers(self):
        # Same-origin app: no wildcard CORS (other sites can't call the API in a
        # browser). Plus standard hardening headers.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._sec_headers()
        self.end_headers()
        self.wfile.write(body)

    def _user_from_auth(self, token=None):
        if token is None:
            hdr = self.headers.get("Authorization", "")
            if hdr.startswith("Bearer "):
                token = hdr[7:]
        return auth.verify_token(token) if token else None

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---- verb dispatch ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._sec_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/events":
            return self._sse(parse_qs(query))
        if path.startswith("/api/"):
            return self._dispatch("GET", path, parse_qs(query))
        return self._static(path)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        self._dispatch("POST", path, parse_qs(query))

    def do_PUT(self):
        path, _, query = self.path.partition("?")
        self._dispatch("PUT", path, parse_qs(query))

    def do_PATCH(self):
        path, _, query = self.path.partition("?")
        self._dispatch("PATCH", path, parse_qs(query))

    def do_DELETE(self):
        path, _, query = self.path.partition("?")
        self._dispatch("DELETE", path, parse_qs(query))

    def _dispatch(self, method, path, query):
        for m, rx, handler in COMPILED:
            if m != method:
                continue
            match = rx.match(path)
            if not match:
                continue
            body = self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
            xff = self.headers.get("X-Forwarded-For", "")
            ip = xff.split(",")[0].strip() if xff else self.client_address[0]
            ctx = {
                "user": self._user_from_auth(),
                "params": match.groupdict(),
                "query": query,
                "body": body,
                "ip": ip,
            }
            try:
                result = handler(ctx)
                self._json(200, result)
            except api.ApiError as e:
                self._json(e.status, {"error": e.message})
            except Exception as e:  # pragma: no cover - defensive
                sys.stderr.write("ERROR %s\n" % repr(e))
                self._json(500, {"error": "Something went wrong on the server."})
            return
        self._json(404, {"error": "Not found."})

    # ---- Server-Sent Events (live order relay) ----
    def _sse(self, query):
        user = self._user_from_auth(query.get("token"))
        if not user:
            return self._json(401, {"error": "Please log in."})
        cid, q = events.subscribe(user["role"])
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._sec_headers()
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=20)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # heartbeat keeps the socket alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            events.unsubscribe(cid)

    # ---- static PWA files ----
    def _static(self, path):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not target.startswith(PUBLIC_DIR):
            return self._json(403, {"error": "Forbidden."})
        if not os.path.isfile(target):
            # SPA fallback: serve the shell for client-side routes
            target = os.path.join(PUBLIC_DIR, "index.html")
            if not os.path.isfile(target):
                return self._json(404, {"error": "Not found."})
        ctype, _ = mimetypes.guess_type(target)
        ctype = ctype or "application/octet-stream"
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        if os.path.basename(target) == "sw.js":
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Cache-Control", "no-cache")
        self._sec_headers()
        self.end_headers()
        self.wfile.write(data)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    db.init_db()
    ip = lan_ip()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("=" * 56)
    print("  slurrrp cart app is running")
    print("=" * 56)
    print(f"  This device : http://localhost:{port}")
    print(f"  Phones (Wi-Fi): http://{ip}:{port}")
    print("  Default logins (please change in Staff): ")
    print("     admin / slurrrp123   (owner dashboard)")
    print("     kitchen / slurrrp123 (back-kitchen board)")
    print("     seller / slurrrp123  (cart)")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping slurrrp...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
