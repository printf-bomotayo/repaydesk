"""Staff, their limits, and signing in.

Sign-in is deliberately small: a password checked against a scrypt hash, and a signed token that
expires. It stands in for the bank's single sign-on (plan, stage 4); what the rest of Repaydesk
relies on is only "this request is from staff member X, whose role and limit are Y", read fresh
from the database on every request so a deactivated account stops working at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

import psycopg

TOKEN_HOURS = 8

# Approval limits in kobo. The two-person threshold lives in approvals.py.
LIMITS = {"officer": 50_000_000, "supervisor": 500_000_000, "auditor": 0}  # ₦500,000 · ₦5,000,000 · none

# The demo bank's reviewers. Names are synthetic, like everything else in the book.
DEMO_STAFF = [
    ("amaka", "Amaka Nwosu", "officer"),
    ("kunle", "Kunle Adebayo", "officer"),
    ("tunde", "Tunde Bakare", "supervisor"),
    ("hadiza", "Hadiza Lawal", "auditor"),
]


@dataclass(frozen=True)
class Staff:
    id: str
    name: str
    role: str
    limit_kobo: int

    @property
    def read_only(self) -> bool:
        return self.role == "auditor"

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role, "limit_kobo": self.limit_kobo}


# ---- passwords -------------------------------------------------------------------------------

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


# ---- tokens ----------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(staff_id: str, secret: str, now: float | None = None) -> str:
    body = _b64(json.dumps({"sub": staff_id, "exp": int((now or time.time()) + TOKEN_HOURS * 3600)}).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str, secret: str, now: float | None = None) -> str | None:
    """The staff id a token was issued to, or None if it is forged, damaged or expired."""
    body, _, sig = token.partition(".")
    want = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not sig or not hmac.compare_digest(sig, want):
        return None
    try:
        claims = json.loads(_unb64(body))
    except ValueError:
        return None
    if claims.get("exp", 0) < (now or time.time()):
        return None
    return claims.get("sub")


# ---- the staff table -------------------------------------------------------------------------


def get(conn: psycopg.Connection, staff_id: str) -> Staff | None:
    row = conn.execute("SELECT id, name, role, limit_kobo FROM staff WHERE id = %s AND active", (staff_id,)).fetchone()
    return Staff(*row) if row else None


def sign_in(conn: psycopg.Connection, staff_id: str, password: str) -> Staff | None:
    row = conn.execute("SELECT password_hash FROM staff WHERE id = %s AND active", (staff_id,)).fetchone()
    if not row:
        check_password(password, hash_password("not a user"))  # same work either way: no timing hint
        return None
    return get(conn, staff_id) if check_password(password, row[0]) else None


def ensure_demo_staff(conn: psycopg.Connection, password: str) -> None:
    """Creates the demo reviewers if missing, all with one password from the environment. Existing
    rows are left alone, so a changed limit survives a restart."""
    for staff_id, name, role in DEMO_STAFF:
        conn.execute(
            "INSERT INTO staff (id, name, role, limit_kobo, password_hash) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (staff_id, name, role, LIMITS[role], hash_password(password)),
        )
