"""A stand-in SMS and WhatsApp provider, for the prototype: it accepts messages the way an
aggregator's API does, shows each phone's conversation on a page, and lets the demo reply as the
borrower: the reply goes back to Repaydesk as a signed webhook, as a provider's would. Nothing leaves
the machine.

    POST /v1/messages   {channel, to, body, client_ref}   Authorization: Bearer <MESSAGING_TOKEN>
                        201 accepted; the same client_ref again is 200 with the same id (no second message)
                        422 a number that can't receive (malformed, or listed in SIM_UNREACHABLE)
    GET  /v1/messages   ?to=  the messages, newest first
    POST /v1/replies    {from, body, channel}   a borrower replies; delivered to REPAYDESK_WEBHOOK_URL
    POST /v1/replies/{id}/redeliver             deliver again (same id: Repaydesk keeps it once)
    GET  /              every phone's conversation, with a box to reply from that phone

Replies are signed with HMAC-SHA256 over the raw body (`x-messaging-signature`), keyed by
MESSAGING_WEBHOOK_SECRET. Stores to SQLite (`SIM_DB`), so a restart forgets nothing.

    uvicorn repaydesk.simulators.messaging:app --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
        _db.execute("CREATE TABLE IF NOT EXISTS replies (id TEXT PRIMARY KEY, channel TEXT NOT NULL, "
                    "from_phone TEXT NOT NULL, body TEXT NOT NULL, sent_at TEXT NOT NULL, delivered TEXT)")
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


class Incoming(BaseModel):
    from_: str = Field(alias="from")
    body: str = Field(min_length=1, max_length=MAX_BODY)
    channel: Literal["sms", "whatsapp"] | None = None  # the channel they were last messaged on, if absent


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


# ---- replies -------------------------------------------------------------------------------------


def sign(body: bytes) -> str:
    secret = os.getenv("MESSAGING_WEBHOOK_SECRET", "dev-only-messaging-secret").encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _post(url: str, body: bytes) -> str:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "x-messaging-signature": sign(body)})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return f"delivered: {json.loads(res.read() or b'{}').get('outcome', 'ok')}"
    except urllib.error.HTTPError as e:
        return f"refused: {e.code}"
    except (urllib.error.URLError, TimeoutError) as e:
        return f"not delivered: {getattr(e, 'reason', e)}"


post = _post  # tests replace this to deliver straight into the API under test


def _deliver(reply_id: str) -> str:
    with _lock:
        r = _conn().execute("SELECT id, channel, from_phone, body, sent_at FROM replies WHERE id = ?",
                            (reply_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such reply")
    url = os.getenv("REPAYDESK_WEBHOOK_URL")
    event = {"event": "message.received",
             "data": {"id": r[0], "channel": r[1], "from": r[2], "body": r[3], "received_at": r[4]}}
    result = post(url, json.dumps(event).encode()) if url else "not delivered: no REPAYDESK_WEBHOOK_URL"
    with _lock:
        _conn().execute("UPDATE replies SET delivered = ? WHERE id = ?", (result, reply_id))
        _conn().commit()
    return result


def reply(from_phone: str, body: str, channel: str | None = None) -> dict:
    """The borrower on `from_phone` replies. On the channel they were last messaged on, by default."""
    if not NUMBER.match(from_phone):
        raise HTTPException(422, f"{from_phone} is not a mobile number")
    with _lock:
        db = _conn()
        if channel is None:
            last = db.execute("SELECT channel FROM messages WHERE to_phone = ? ORDER BY id DESC LIMIT 1",
                              (from_phone,)).fetchone()
            channel = last[0] if last else "sms"
        n = db.execute("SELECT count(*) FROM replies").fetchone()[0] + 1
        reply_id = f"IN-{n:06d}"
        db.execute("INSERT INTO replies (id, channel, from_phone, body, sent_at) VALUES (?, ?, ?, ?, ?)",
                   (reply_id, channel, from_phone, body.strip(), datetime.now(UTC).isoformat()))
        db.commit()
    return {"id": reply_id, "channel": channel, "delivery": _deliver(reply_id)}


@app.post("/v1/replies")
def reply_api(msg: Incoming, authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorised(authorization)
    return reply(msg.from_, msg.body, msg.channel)


@app.post("/v1/replies/{reply_id}/redeliver")
def redeliver(reply_id: str, authorization: Annotated[str | None, Header()] = None) -> dict:
    _authorised(authorization)
    return {"id": reply_id, "delivery": _deliver(reply_id)}


@app.post("/phones/{phone}/reply")
async def reply_form(phone: str, request: Request):
    """The page's reply box: the demo typing as the borrower."""
    form = parse_qs((await request.body()).decode())
    body = (form.get("body") or [""])[0].strip()
    if body:
        reply(phone, body[:MAX_BODY])
    return RedirectResponse(f"/#p{quote(phone)}", status_code=303)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def phones() -> str:
    with _lock:
        db = _conn()
        out = db.execute("SELECT to_phone, received_at, channel, body, id, 'out', NULL FROM messages").fetchall()
        back = db.execute("SELECT from_phone, sent_at, channel, body, id, 'in', delivered FROM replies").fetchall()
    by_phone: dict[str, list] = {}
    for r in sorted(out + back, key=lambda r: r[1]):
        by_phone.setdefault(r[0], []).append(r)
    e = html.escape

    def bubble(r) -> str:
        kind = "WhatsApp" if r[2] == "whatsapp" else "SMS"
        meta = f"{kind} · {e(r[1][:16].replace('T', ' '))} UTC · {e(r[4])}"
        if r[5] == "in":
            meta = f"Reply · {meta} · {e(r[6] or 'not delivered')}"
        return f"<div class='msg {e(r[2])} {r[5]}'><span class='meta'>{meta}</span>{e(r[3])}</div>"

    cards = "".join(
        f"<section id='p{e(phone)}'><h2>{e(phone)}</h2>{''.join(bubble(r) for r in msgs)}"
        f"<form method='post' action='/phones/{e(phone)}/reply'>"
        f"<label for='r{e(phone)}'>Reply as this borrower</label>"
        f"<input id='r{e(phone)}' name='body' maxlength='{MAX_BODY}' placeholder='I go pay Friday' required>"
        f"<button>Send reply</button></form></section>"
        for phone, msgs in sorted(by_phone.items())) or "<p>No messages yet.</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Messaging simulator</title>
<style>
:root {{ --ink:#111; --muted:#666; --line:#e5e5e5; --bg:#fff; --sms:#f4f4f4; --wa:#e7f6ec; --in:#e8eefc; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --ink:#eee; --muted:#aaa; --line:#333; --bg:#111; --sms:#1d1d1d; --wa:#13281b; --in:#18213a; }}
}}
body {{ font: 15px/1.45 system-ui, sans-serif; color: var(--ink); background: var(--bg);
       margin: 0 auto; max-width: 760px; padding: 24px 16px; }}
.badge {{ display:inline-block; border:1px solid var(--ink); border-radius:999px; padding:2px 10px; font-size:12px; }}
section {{ border-top: 1px solid var(--line); padding: 12px 0; }}
h2 {{ font-size: 16px; margin: 4px 0 8px; }}
.msg {{ background: var(--sms); border-radius: 12px; padding: 10px 14px; margin: 8px 15% 8px 0;
        white-space: pre-wrap; }}
.msg.whatsapp {{ background: var(--wa); }}
.msg.in {{ background: var(--in); margin: 8px 0 8px 15%; }}
.meta {{ display:block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }}
label {{ width: 100%; font-size: 12px; color: var(--muted); }}
input {{ flex: 1; min-width: 0; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line);
        border-radius: 8px; padding: 8px 10px; }}
button {{ font: inherit; border: 1px solid var(--ink); background: var(--ink); color: var(--bg); border-radius: 8px;
         padding: 8px 14px; cursor: pointer; }}
</style></head><body>
<p><span class="badge">Prototype</span> A stand-in SMS and WhatsApp provider. Nothing here reached a real phone,
and the replies are typed here, not by a borrower.</p>
<h1>Phones</h1>{cards}</body></html>"""
