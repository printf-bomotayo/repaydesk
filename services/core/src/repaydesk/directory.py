"""The borrower directory: Repaydesk's copy of who borrows what, taken from core banking.

Staff find borrowers and loans by searching and filtering this copy. Core banking stays the
system of record: a borrower's or a loan's own page asks it live, and nothing here is edited by
Repaydesk. Each sync reads the whole book through the core banking adapter, writes what changed,
and removes what core banking no longer has, in one transaction, so a page never sees half a sync.

    python -m repaydesk.directory               # sync once
    python -m repaydesk.directory --every 900   # sync every 15 minutes (the compose service)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg
from opentelemetry import trace

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingDirectory

tracer = trace.get_tracer("repaydesk.directory")

# One sync at a time, across every process that might start one.
LOCK = 0x52440001

_BORROWER_COLUMNS = ("ref", "core_id", "name", "phone", "branch_code", "branch_name", "business_name", "group_ref",
                     "group_name", "joined_on", "active")
_LOAN_COLUMNS = ("ref", "core_id", "borrower_ref", "product", "product_name", "principal_kobo", "disbursed_on",
                 "status", "outstanding_kobo", "arrears_kobo", "days_late")


def log(msg: str) -> None:
    print(f"[directory] {msg}", flush=True)


def _replace(conn: psycopg.Connection, table: str, columns: tuple[str, ...], rows: list[tuple]) -> int:
    """Make `table` hold exactly `rows`. Returns how many rows were added, changed or removed."""
    cols = ", ".join(columns)
    conn.execute(f"CREATE TEMP TABLE incoming (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    conn.execute("ALTER TABLE incoming DROP COLUMN copied_at")
    with conn.cursor() as cur, cur.copy(f"COPY incoming ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns[1:])
    differs = " OR ".join(f"{table}.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in columns[1:])
    written = conn.execute(
        f"INSERT INTO {table} ({cols}, copied_at) SELECT {cols}, now() FROM incoming "
        f"ON CONFLICT (ref) DO UPDATE SET {updates}, copied_at = now() WHERE {differs}"
    ).rowcount
    removed = conn.execute(f"DELETE FROM {table} t WHERE NOT EXISTS (SELECT 1 FROM incoming i WHERE i.ref = t.ref)"
                           ).rowcount
    conn.execute("DROP TABLE incoming")
    return written + removed


def sync(conn: psycopg.Connection, source: CoreBankingDirectory) -> dict | None:
    """Copy the book. Returns the counts, or None if another sync is already running."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,)).fetchone()[0]:
        return None
    try:
        sync_id = conn.execute("INSERT INTO directory_syncs DEFAULT VALUES RETURNING id").fetchone()[0]
        with tracer.start_as_current_span("directory.sync") as span:
            try:
                borrowers = [tuple(getattr(b, c) for c in _BORROWER_COLUMNS) for b in source.list_borrowers()]
                loans = [(x.ref, x.core_id, x.borrower_ref, x.product, x.product_name, x.principal, x.disbursed_on,
                          x.status, x.outstanding, x.arrears, x.days_late) for x in source.list_loans()]
                with conn.transaction():
                    changed = (_replace(conn, "borrowers", _BORROWER_COLUMNS, borrowers)
                               + _replace(conn, "loans", _LOAN_COLUMNS, loans))
                    conn.execute(
                        "UPDATE directory_syncs SET finished_at = clock_timestamp(), borrowers = %s, loans = %s, "
                        "changed = %s WHERE id = %s", (len(borrowers), len(loans), changed, sync_id))
            except Exception as e:
                conn.execute("UPDATE directory_syncs SET finished_at = clock_timestamp(), error = %s WHERE id = %s",
                             (f"{type(e).__name__}: {e}"[:500], sync_id))
                raise
            counts = {"borrowers": len(borrowers), "loans": len(loans), "changed": changed}
            span.set_attributes({f"directory.{k}": v for k, v in counts.items()})
            return counts
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))


def last_sync(conn: psycopg.Connection) -> dict | None:
    """The newest sync that finished well, and whether a later one failed."""
    ok = conn.execute(
        "SELECT finished_at, borrowers, loans FROM directory_syncs WHERE error IS NULL AND finished_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 1").fetchone()
    failed = conn.execute(
        "SELECT finished_at, error FROM directory_syncs WHERE error IS NOT NULL "
        "AND id > coalesce((SELECT max(id) FROM directory_syncs WHERE error IS NULL AND finished_at IS NOT NULL), 0) "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if not ok and not failed:
        return None
    return {
        "synced_at": ok[0].isoformat() if ok else None,
        "borrowers": ok[1] if ok else 0,
        "loans": ok[2] if ok else 0,
        "failed_since": failed[0].isoformat() if failed else None,
    }


def main(argv: list[str] | None = None) -> int:
    from repaydesk.adapters.fineract import FineractCoreBanking

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--every", type=int, default=0, help="seconds between syncs; 0 syncs once")
    args = ap.parse_args(argv)

    telemetry.setup("repaydesk-directory")
    try:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.getenv("FINERACT_USERNAME", "mifos"), os.getenv("FINERACT_PASSWORD", "password"))
        while True:
            started = time.monotonic()
            try:
                counts = sync(conn, core)
                if counts is None:
                    log("another sync is running; skipped")
                else:
                    log(f"{counts['borrowers']} borrowers, {counts['loans']} loans, {counts['changed']} changed, "
                        f"in {time.monotonic() - started:.0f}s")
            except Exception as e:  # noqa: BLE001 - the loop keeps going; the failure is recorded and shown
                log(f"sync failed: {type(e).__name__}: {e}")
                if not args.every:
                    return 1
            if not args.every:
                return 0
            time.sleep(max(args.every - (time.monotonic() - started), 5))
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
