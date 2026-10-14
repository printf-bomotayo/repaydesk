"""A stand-in SMS and WhatsApp provider, for the prototype: it accepts messages the way an
aggregator's API does, and shows each phone's inbox on a page, so a demo can see what a borrower
received. Nothing leaves the machine.

    POST /v1/messages   {channel, to, body, client_ref}   Authorization: Bearer <MESSAGING_TOKEN>
                        201 accepted; the same client_ref again is 200 with the same id (no second message)
                        422 a number that can't receive (malformed, or listed in SIM_UNREACHABLE)
    GET  /v1/messages   ?to=  the messages, newest first
    GET  /              every phone's inbox

Stores to SQLite (`SIM_DB`), so a restart forgets nothing.

    uvicorn repaydesk.simulators.messaging:app --port 8000
"""

from __future__ import annotations

import html
import os
import re
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

NUMBER = re.compile(r"^0[789][01]\d{8}$")  # a Nigerian mobile number, 11 digits
MAX_BODY = 1600

app = FastAPI(title="Messaging simulator (prototype)")
_lock = threading.Lock()
_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        path = os.getenv("SIM_DB", "messaging.db")
        if path != ":memory:" and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        _db = sqlite3.connect(path, check_same_thread=False)
        _db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, client_ref TEXT UNIQUE NOT NULL, "
                    "channel TEXT NOT NULL, to_phone TEXT NOT NULL, body TEXT NOT NULL, received_at TEXT NOT NULL)")
        _db.commit()
    return _db


def reset(path: str = ":memory:") -> None:
    """For tests: start again with an empty store."""
    global _db
    os.environ["SIM_DB"] = path
    _db = None


class Outgoing(BaseModel):
    channel: Literal["sms", "whatsapp"]
    to: str
    body: str = Field(min_length=1)
    client_ref: str = Field(min_length=1, max_length=100)


def _authorised(authorization: str | None) -> None:
    token = os.getenv("MESSAGING_TOKEN", "dev-only-messaging-token")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "missing or wrong token")


def _row(r) -> dict:
    return {"id": r[0], "client_ref": r[1], "channel": r[2], "to": r[3], "body": r[4], "received_at": r[5]}


@app.post("/v1/messages")
def send(msg: Outgoing, authorization: Annotated[str | None, Header()] = None):
    _authorised(authorization)
    unreachable = {n.strip() for n in os.getenv("SIM_UNREACHABLE", "").split(",") if n.strip()}
    if not NUMBER.match(msg.to):
        return JSONResponse({"error": "invalid_number", "message": f"{msg.to} is not a mobile number"}, 422)
    if msg.to in unreachable:
        return JSONResponse({"error": "unreachable", "message": f"{msg.to} can't receive messages"}, 422)
    if len(msg.body) > MAX_BODY:
        return JSONResponse({"error": "too_long", "message": f"over {MAX_BODY} characters"}, 422)
    with _lock:
        db = _conn()
        found = db.execute("SELECT * FROM messages WHERE client_ref = ?", (msg.client_ref,)).fetchone()
        if found:
            return JSONResponse({**_row(found), "status": "accepted", "duplicate": True}, 200)
        n = db.execute("SELECT count(*) FROM messages").fetchone()[0] + 1
        row = (f"SIM-{n:06d}", msg.client_ref, msg.channel, msg.to, msg.body, datetime.now(UTC).isoformat())
        db.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", row)
        db.commit()
    return JSONResponse({**_row(row), "status": "accepted", "duplicate": False}, 201)


@app.get("/v1/messages")
def received(to: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100,
             authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorised(authorization)
    with _lock:
        rows = _conn().execute("SELECT * FROM messages WHERE ? IS NULL OR to_phone = ? ORDER BY id DESC LIMIT ?",
                               (to, to, limit)).fetchall()
    return {"items": [_row(r) for r in rows]}


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def phones() -> str:
    with _lock:
        rows = _conn().execute("SELECT * FROM messages ORDER BY to_phone, id DESC LIMIT 2000").fetchall()
    by_phone: dict[str, list] = {}
    for r in rows:
        by_phone.setdefault(r[3], []).append(r)
    cards = "".join(
        f"<section><h2>{html.escape(phone)}</h2>" + "".join(
            f"<div class='msg {html.escape(r[2])}'><span class='meta'>{'WhatsApp' if r[2] == 'whatsapp' else 'SMS'}"
            f" · {html.escape(r[5][:16].replace('T', ' '))} UTC · {html.escape(r[0])}</span>{html.escape(r[4])}</div>"
            for r in msgs) + "</section>"
        for phone, msgs in by_phone.items()) or "<p>No messages yet.</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Messaging simulator</title>
<style>
:root {{ --ink:#111; --muted:#666; --line:#e5e5e5; --bg:#fff; --sms:#f4f4f4; --wa:#e7f6ec; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --ink:#eee; --muted:#aaa; --line:#333; --bg:#111; --sms:#1d1d1d; --wa:#13281b; }}
}}
body {{ font: 15px/1.45 system-ui, sans-serif; color: var(--ink); background: var(--bg);
       margin: 0 auto; max-width: 760px; padding: 24px 16px; }}
.badge {{ display:inline-block; border:1px solid var(--ink); border-radius:999px; padding:2px 10px; font-size:12px; }}
section {{ border-top: 1px solid var(--line); padding: 12px 0; }}
h2 {{ font-size: 16px; margin: 4px 0 8px; }}
.msg {{ background: var(--sms); border-radius: 12px; padding: 10px 14px; margin: 8px 0; white-space: pre-wrap; }}
.msg.whatsapp {{ background: var(--wa); }}
.meta {{ display:block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
</style></head><body>
<p><span class="badge">Prototype</span> A stand-in SMS and WhatsApp provider. Nothing here reached a real phone.</p>
<h1>Phones</h1>{cards}</body></html>"""
