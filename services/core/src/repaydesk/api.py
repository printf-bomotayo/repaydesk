"""Repaydesk's HTTP API: the payment provider's webhook, staff sign-in, the review queue, and
the reads behind the web app (payments, ingestion, the audit log).

The webhook does the least it can at the door: check the signature, store the line (or note it
as a duplicate), answer. Matching happens later, off the request, so a slow match can never
make the provider think a delivery failed.

Staff endpoints take a bearer token from `POST /auth/sign-in`. The webhook takes none: the
provider proves itself with a signature instead.

    uvicorn repaydesk.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from repaydesk import approvals, db, staff, telemetry, views
from repaydesk.adapters.webhooks import PayRailWebhooks
from repaydesk.ingest import store_line

tracer = trace.get_tracer("repaydesk.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.setup("repaydesk-api")
    url = os.environ["REPAYDESK_DATABASE_URL"]
    with db.connect(url) as conn:
        db.migrate(conn)
        if password := os.getenv("STAFF_DEMO_PASSWORD"):
            staff.ensure_demo_staff(conn, password)
    app.state.session_secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
    app.state.pool = ConnectionPool(url, min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
    app.state.payrail = PayRailWebhooks(os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
    yield
    app.state.pool.close()
    telemetry.shutdown()


app = FastAPI(title="Repaydesk", lifespan=lifespan)


class StripApiPrefix:
    """The web app calls /api/... on its own origin, and nginx or Vite pass the path through
    untouched. Stripping the prefix here, rather than in the proxy, keeps line ids with '#' or
    ':' in them byte for byte: a proxy that rewrites a path decodes and re-encodes it first."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["path"] == "/api" or scope["path"].startswith("/api/")):
            scope = dict(scope, path=scope["path"][4:] or "/", raw_path=scope.get("raw_path", b"")[4:] or b"/")
        await self.app(scope, receive, send)


app.add_middleware(StripApiPrefix)

try:  # traces every request when OpenTelemetry is configured
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
except ImportError:  # pragma: no cover
    pass


@app.get("/health")
def health() -> dict:
    with app.state.pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


def _record(conn, sha: str, ok: bool, event_id: str | None, outcome: str, line_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO webhook_deliveries (provider, body_sha256, signature_ok, event_id, outcome, line_id) "
        "VALUES ('payrail', %s, %s, %s, %s, %s)",
        (sha, ok, event_id, outcome, line_id),
    )


@app.post("/webhooks/payrail")
async def payrail_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    sha = hashlib.sha256(body).hexdigest()
    hooks: PayRailWebhooks = app.state.payrail
    span = trace.get_current_span()

    with app.state.pool.connection() as conn:
        if not hooks.verify(body, request.headers.get(PayRailWebhooks.SIGNATURE_HEADER, "")):
            _record(conn, sha, False, None, "rejected")
            span.set_attribute("webhook.outcome", "rejected")
            # Never echo the body or say which part failed: either could help a forger.
            return JSONResponse({"outcome": "rejected"}, status_code=401)
        try:
            line = hooks.to_line(body)
        except (ValueError, KeyError, TypeError):
            _record(conn, sha, True, None, "rejected")
            return JSONResponse({"outcome": "rejected", "reason": "unreadable event"}, status_code=400)
        if line is None:
            event_id = json.loads(body).get("id")
            _record(conn, sha, True, event_id, "ignored")
            return JSONResponse({"outcome": "ignored"})

        with conn.transaction():
            stored = store_line(conn, line)
            outcome = "duplicate" if stored.is_duplicate else "stored"
            _record(conn, sha, True, line.bank_reference, outcome, stored.line_id)
            if not stored.is_duplicate:
                db.audit(conn, "service:api", "payment.received", f"payment_line:{stored.line_id}",
                         {"channel": line.channel.value, "amount_kobo": line.amount}, telemetry.current_trace_id())
        span.set_attributes({"webhook.outcome": outcome, "payment.line_id": stored.line_id})
        # 200 for a duplicate as well: the provider did nothing wrong, and an error would make it retry.
        return JSONResponse({"outcome": outcome, "line_id": stored.line_id})


# ---- staff -----------------------------------------------------------------------------------


class SignIn(BaseModel):
    staff_id: str
    password: str


class Rejection(BaseModel):
    reason: str


def signed_in(authorization: Annotated[str | None, Header()] = None) -> staff.Staff:
    token = (authorization or "").removeprefix("Bearer ").strip()
    staff_id = staff.read_token(token, app.state.session_secret) if token else None
    if staff_id:
        with app.state.pool.connection() as conn:
            who = staff.get(conn, staff_id)
        if who:
            trace.get_current_span().set_attribute("staff.id", who.id)
            return who
    raise HTTPException(401, "Sign in first.", headers={"WWW-Authenticate": "Bearer"})


Me = Annotated[staff.Staff, Depends(signed_in)]


def _refused(e: approvals.Refused) -> JSONResponse:
    status = 409 if e.code == "not_pending" else 422 if e.code in ("reason_required", "bad_split") else 403
    return JSONResponse({"refused": e.code, "message": e.message}, status_code=status)


@app.post("/auth/sign-in")
def sign_in(body: SignIn) -> dict:
    with app.state.pool.connection() as conn:
        who = staff.sign_in(conn, body.staff_id.strip().lower(), body.password)
        if not who:
            raise HTTPException(401, "Wrong name or password.")
        db.audit(conn, who.id, "staff.signed_in", f"staff:{who.id}", {}, telemetry.current_trace_id())
    return {"token": staff.issue_token(who.id, app.state.session_secret), "staff": who.public(),
            "expires_in": staff.TOKEN_HOURS * 3600}


@app.get("/me")
def me(who: Me) -> dict:
    return who.public() | {"two_person_above_kobo": approvals.TWO_PERSON_ABOVE_KOBO}


@app.get("/queue")
def review_queue(who: Me, limit: int = 200) -> dict:
    with app.state.pool.connection() as conn:
        items = approvals.queue(conn, who, min(max(limit, 1), 500))
    return {"items": items, "count": len(items)}


@app.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int, who: Me) -> dict:
    with app.state.pool.connection() as conn:
        try:
            return approvals.proposal(conn, proposal_id, who)
        except approvals.NotFound:
            raise HTTPException(404, "No such proposal.") from None


@app.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: int, who: Me):
    with app.state.pool.connection() as conn:
        try:
            out = approvals.approve(conn, proposal_id, who)
        except approvals.NotFound:
            raise HTTPException(404, "No such proposal.") from None
        except approvals.Refused as e:
            return _refused(e)
    return {"proposal_id": out.proposal_id, "status": out.status, "outbox_id": out.outbox_id}


@app.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: int, body: Rejection, who: Me):
    with app.state.pool.connection() as conn:
        try:
            out = approvals.reject(conn, proposal_id, who, body.reason)
        except approvals.NotFound:
            raise HTTPException(404, "No such proposal.") from None
        except approvals.Refused as e:
            return _refused(e)
    return {"proposal_id": out.proposal_id, "status": out.status}


# ---- reads for the web app -------------------------------------------------------------------


@app.get("/config")
def config() -> dict:
    """What the web app needs before sign-in: whose bank this is, and where the other systems'
    screens live, so a record can be opened in them. No secrets."""
    return {
        "bank_name": os.getenv("BANK_NAME", "Demo Microfinance Bank"),
        "links": {
            "fineract_web": os.getenv("FINERACT_WEB_PUBLIC_URL", "http://127.0.0.1:18081"),
            "odoo": os.getenv("ODOO_PUBLIC_URL", "http://127.0.0.1:18069"),
            "jaeger": os.getenv("JAEGER_PUBLIC_URL", "http://127.0.0.1:16686"),
        },
    }


@app.get("/summary")
def summary(who: Me) -> dict:
    with app.state.pool.connection() as conn:
        return views.summary(conn)


@app.get("/lines")
def list_lines(who: Me, status: str | None = None, channel: str | None = None, q: str | None = None,
               limit: int = 50, offset: int = 0) -> dict:
    if status and status not in views.STATUSES:
        raise HTTPException(422, f"status is one of {', '.join(views.STATUSES)}")
    with app.state.pool.connection() as conn:
        return views.lines(conn, status, channel, q, min(max(limit, 1), 200), max(offset, 0))


@app.get("/lines/{line_id:path}")
def get_line(line_id: str, who: Me) -> dict:
    with app.state.pool.connection() as conn:
        found = views.line(conn, line_id)
    if not found:
        raise HTTPException(404, "No such payment line.")
    return found


@app.get("/ingestion")
def ingestion(who: Me) -> dict:
    with app.state.pool.connection() as conn:
        return views.ingestion(conn)


@app.get("/audit")
def audit_log(who: Me, actor: str | None = None, action: str | None = None, subject: str | None = None,
              before: int | None = None, limit: int = 100) -> dict:
    with app.state.pool.connection() as conn:
        return views.audit(conn, actor, action, subject, before, min(max(limit, 1), 500))
