"""Repaydesk's HTTP API. For now: the payment provider's webhook, and health.

The webhook does the least it can at the door: check the signature, store the line (or note it
as a duplicate), answer. Matching happens later, off the request, so a slow match can never
make the provider think a delivery failed.

    uvicorn repaydesk.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from psycopg_pool import ConnectionPool

from repaydesk import db, telemetry
from repaydesk.adapters.webhooks import PayRailWebhooks
from repaydesk.ingest import store_line

tracer = trace.get_tracer("repaydesk.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.setup("repaydesk-api")
    url = os.environ["REPAYDESK_DATABASE_URL"]
    with db.connect(url) as conn:
        db.migrate(conn)
    app.state.pool = ConnectionPool(url, min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
    app.state.payrail = PayRailWebhooks(os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
    yield
    app.state.pool.close()
    telemetry.shutdown()


app = FastAPI(title="Repaydesk", lifespan=lifespan)

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
