"""Ingestion: incoming payment lines into Repaydesk's database, each exactly once.

Two ways in, one way to store:

- statement files (bank CSV, MT940, POS settlement, branch cash sheets), picked up from the
  StatementSource. A file seen before (same source, name and content) is skipped whole;
- webhook deliveries from the payment provider, received by the API (repaydesk.api).

Both end in `store_line`, which is where duplicates are caught. The database decides, through
a unique index on the line's source reference, so two deliveries of the same event arriving at
the same moment can't both become originals.

    python -m repaydesk.ingest                  # ingest any new statement files
    python -m repaydesk.ingest --replay-webhooks http://api:8000
                                                # deliver the week's webhook file to the API,
                                                # the way the provider would
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from opentelemetry import trace
from opentelemetry.propagate import inject

from repaydesk import db, telemetry
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.domain import PaymentLine

tracer = trace.get_tracer("repaydesk.ingest")

# Webhook deliveries arrive over HTTP, so file ingestion leaves the provider's folder alone.
FILE_FOLDERS = ("northgate", "coastline", "termpay", "branch-cash")


def dedupe_key(line: PaymentLine) -> str:
    return f"{line.channel.value}:{line.bank_reference}"


@dataclass
class Stored:
    line_id: str
    duplicate_of: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


@dataclass
class FileResult:
    path: str
    skipped: bool = False
    stored: int = 0
    duplicates: int = 0
    duplicate_ids: list[str] = field(default_factory=list)


def store_line(conn: psycopg.Connection, line: PaymentLine, file_id: int | None = None) -> Stored:
    """Insert a line as an original, or, if its reference is already held, as a duplicate of it.
    Call inside a transaction."""
    key = dedupe_key(line)
    values = (line.channel.value, file_id, line.source_account, line.value_date, line.amount, line.narration,
              line.bank_reference, line.payer_name, line.payer_account, line.virtual_account, line.terminal_id,
              line.customer_reference, key)
    columns = ("channel, file_id, source_account, value_date, amount_kobo, narration, bank_reference, payer_name, "
               "payer_account, virtual_account, terminal_id, customer_reference, dedupe_key")
    row = conn.execute(
        f"INSERT INTO payment_lines (id, {columns}) VALUES (%s, {', '.join(['%s'] * len(values))}) "
        "ON CONFLICT (dedupe_key) WHERE status <> 'duplicate' DO NOTHING RETURNING id",
        (key, *values),
    ).fetchone()
    if row:
        return Stored(row[0])

    # Already held. Keep this sighting too, numbered after the ones before it.
    original = conn.execute(
        "SELECT id FROM payment_lines WHERE dedupe_key = %s AND status <> 'duplicate'", (key,)
    ).fetchone()[0]
    n = conn.execute("SELECT count(*) FROM payment_lines WHERE dedupe_key = %s", (key,)).fetchone()[0] + 1
    dup_id = f"{key}#{n}"
    conn.execute(
        f"INSERT INTO payment_lines (id, {columns}, status, duplicate_of) "
        f"VALUES (%s, {', '.join(['%s'] * len(values))}, 'duplicate', %s)",
        (dup_id, *values, original),
    )
    return Stored(dup_id, original)


def ingest_file(conn: psycopg.Connection, source: FolderStatementSource, path: Path) -> FileResult:
    result = FileResult(path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with tracer.start_as_current_span("ingest.file") as span, conn.transaction():
        span.set_attributes({"file.name": path.name, "file.source": path.parent.name})
        row = conn.execute(
            "INSERT INTO statement_files (source, file_name, sha256) VALUES (%s, %s, %s) "
            "ON CONFLICT ON CONSTRAINT statement_files_once DO NOTHING RETURNING id",
            (path.parent.name, path.name, digest),
        ).fetchone()
        if not row:
            result.skipped = True
            span.set_attribute("file.skipped", True)
            return result
        file_id = row[0]
        for line in source.read(path):
            stored = store_line(conn, line, file_id)
            if stored.is_duplicate:
                result.duplicates += 1
                result.duplicate_ids.append(stored.line_id)
            else:
                result.stored += 1
        conn.execute("UPDATE statement_files SET line_count = %s WHERE id = %s",
                     (result.stored + result.duplicates, file_id))
        db.audit(conn, "service:ingest", "statement.ingested", f"statement_file:{file_id}",
                 {"file": path.name, "stored": result.stored, "duplicates": result.duplicates},
                 telemetry.current_trace_id())
        span.set_attributes({"lines.stored": result.stored, "lines.duplicate": result.duplicates})
    return result


def ingest_folder(conn: psycopg.Connection, source: FolderStatementSource) -> list[FileResult]:
    with tracer.start_as_current_span("ingest.folder"):
        paths = [p for p in source.pending() if p.parent.name in FILE_FOLDERS]
        return [ingest_file(conn, source, p) for p in paths]


def replay_webhooks(api_url: str, inbox: Path) -> dict[str, int]:
    """Deliver recorded provider webhooks to the API, one HTTP request each, as the provider would."""
    import httpx

    counts: dict[str, int] = {}
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=10) as http:
        for path in sorted((inbox / "payrail").glob("*.jsonl")):
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                delivery = json.loads(raw)
                headers = dict(delivery["headers"])
                inject(headers)  # the API's spans join this trace
                r = http.post("/webhooks/payrail", content=delivery["body"].encode(), headers=headers)
                outcome = r.json().get("outcome", str(r.status_code)) if r.status_code < 500 else str(r.status_code)
                counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data")))
    ap.add_argument("--replay-webhooks", metavar="API_URL", help="deliver data/inbox/payrail to the API")
    args = ap.parse_args(argv)
    inbox = args.data_dir / "inbox"

    telemetry.setup("repaydesk-ingest")
    try:
        if args.replay_webhooks:
            with tracer.start_as_current_span("ingest.replay_webhooks"):
                counts = replay_webhooks(args.replay_webhooks, inbox)
            print("[ingest] webhook deliveries:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            return 0
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        source = FolderStatementSource(inbox, os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
        results = ingest_folder(conn, source)
        new = [r for r in results if not r.skipped]
        print(f"[ingest] {len(new)} new files, {len(results) - len(new)} already ingested; "
              f"{sum(r.stored for r in new)} lines stored, {sum(r.duplicates for r in new)} duplicates")
        return 0
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
