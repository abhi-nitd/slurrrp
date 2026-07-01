"""Authentication: password hashing (PBKDF2) and signed session tokens (HMAC).

Uses only the Python standard library so there is nothing to install.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# Data location can be pointed at a persistent disk on a host via SLURRRP_DATA_DIR.
DATA_DIR = os.environ.get("SLURRRP_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
SECRET_FILE = os.path.join(DATA_DIR, "secret.txt")

TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days
PBKDF2_ITERATIONS = 200_000


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def get_secret() -> bytes:
    """Load the server signing secret, creating it on first run."""
    _ensure_data_dir()
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(SECRET_FILE, "r", encoding="utf-8") as fh:
        return bytes.fromhex(fh.read().strip())


# ---- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- tokens (compact HMAC-signed, JWT-like) --------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_token(payload: dict) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + TOKEN_TTL_SECONDS
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    b = _b64url(raw)
    sig = hmac.new(get_secret(), b.encode("ascii"), hashlib.sha256).digest()
    return f"{b}.{_b64url(sig)}"


def verify_token(token: str):
    """Return the token payload dict if valid & unexpired, else None."""
    try:
        b, sig = token.split(".")
        expected = hmac.new(get_secret(), b.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig), expected):
            return None
        payload = json.loads(_b64url_decode(b))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
