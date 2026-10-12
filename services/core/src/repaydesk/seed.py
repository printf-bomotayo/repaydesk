"""Seed a demo: Repaydesk's database, Fineract, Odoo, and a week of incoming payments.

    python -m repaydesk.seed                 # what `make seed` runs inside compose
    python -m repaydesk.seed --files-only    # regenerate the week's files, load nothing

Safe to re-run. The first run records its seed, size and as-of date; later runs reuse them,
so the book comes out identical and every load step skips what already exists.

This is a setup tool with write access to Fineract and Odoo. In the running system only the
posting gateway writes to them (ADR 0002). The seed creates the restricted Fineract logins the
running system uses: read only for the API, the directory and rule matching, and repayments only
for the gateway.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import date, timedelta
from pathlib import Path

from opentelemetry import trace
from psycopg.types.json import Jsonb

from repaydesk import db, directory, telemetry
from repaydesk.adapters.chart import OPENING_BALANCE, PRINCIPAL, REPAYMENT_JOURNAL
from repaydesk.adapters.fineract import POSTING, READ_ONLY, FineractCoreBanking
from repaydesk.adapters.odoo import OdooLedger
from repaydesk.adapters.ports import AlreadyPosted, JournalEntry, JournalLine
from repaydesk.domain import Book, Loan, bank_today, naira
from repaydesk.synth.generator import Generator
from repaydesk.synth.names import COLLECTION_ACCOUNTS
from repaydesk.synth.writers import write_week

tracer = trace.get_tracer("repaydesk.seed")


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def wait_for(name: str, check: Callable[[], bool], timeout: int = 600) -> None:
    start = time.monotonic()
    while not check():
        if time.monotonic() - start > timeout:
            raise SystemExit(f"{name} did not come up in {timeout}s")
        time.sleep(5)
    log(f"{name} is up")


def parallel(label: str, items: Iterable, fn: Callable, workers: int = 8) -> int:
    items = list(items)
    started, done = time.monotonic(), 0
    with tracer.start_as_current_span(f"load.{label}") as span, ThreadPoolExecutor(workers) as pool:
        span.set_attribute("items", len(items))
        ctx = trace.set_span_in_context(span)

        def run(item):
            with tracer.start_as_current_span(f"{label}.one", context=ctx):
                return fn(item)

        for f in as_completed(pool.submit(run, i) for i in items):
            f.result()
            done += 1
            if done % 250 == 0:
                log(f"  {label}: {done}/{len(items)}")
    log(f"{label}: {done} in {time.monotonic() - started:.0f}s")
    return done


def principal_outstanding(loan: Loan, paid: int) -> int:
    """Principal left after `paid`, applied instalment by instalment, interest first (Fineract's
    standard order)."""
    left, principal_paid = paid, 0
    for inst in loan.schedule:
        interest = min(left, inst.interest)
        left -= interest
        p = min(left, inst.principal)
        left -= p
        principal_paid += p
    return loan.principal - principal_paid


def load(book: Book, core: FineractCoreBanking, ledger: OdooLedger) -> dict[str, int]:
    counts: dict[str, int] = {}
    with tracer.start_as_current_span("load.setup"):
        core.ensure_setup(book.branches, book.products)
        ledger.ensure_setup(book.bank_name, COLLECTION_ACCOUNTS)
    log("Fineract and Odoo set up: branches, products, payment types, chart of accounts, journals")

    counts["borrowers"] = parallel("borrowers", book.borrowers, core.create_borrower)
    counts["groups"] = parallel("groups", book.groups, core.create_group, workers=4)

    history: dict[str, list] = defaultdict(list)
    for r in book.history:
        history[r.loan_ref].append(r)

    def loan_with_history(loan: Loan) -> None:
        core.create_loan(loan)
        for r in sorted(history[loan.ref], key=lambda r: r.paid_on):
            with suppress(AlreadyPosted):  # posted on an earlier run
                core.post_repayment(loan.ref, r.paid_on, r.amount, r.reference, channel=r.channel)

    counts["loans"] = parallel("loans", book.loans, loan_with_history)
    counts["history_repayments"] = len(book.history)

    # The ledger gets one opening entry: principal outstanding when the statement week starts.
    # Day-by-day history would add thousands of entries and nothing the demo needs.
    paid = defaultdict(int)
    for r in book.history:
        paid[r.loan_ref] += r.amount
    outstanding = sum(principal_outstanding(loan, paid[loan.ref]) for loan in book.loans)
    entry = JournalEntry(
        on=book.week_start - timedelta(days=1),
        reference=f"OPENING-{book.week_start:%Y%m%d}",
        memo="Loan book opening balance (principal outstanding)",
        journal=REPAYMENT_JOURNAL,
        lines=(JournalLine(PRINCIPAL, debit=outstanding), JournalLine(OPENING_BALANCE, credit=outstanding)),
    )
    try:
        ledger.post_entry(entry)
        log(f"Odoo opening entry: {naira(outstanding)} principal outstanding")
    except AlreadyPosted:
        log("Odoo opening entry already posted")
    counts["principal_outstanding_kobo"] = outstanding
    return counts


def service_users(core: FineractCoreBanking) -> None:
    for role, prefix, permissions in (("Repaydesk read only", "FINERACT_READ", READ_ONLY),
                                      ("Repaydesk posting gateway", "FINERACT_GATEWAY", POSTING)):
        username, password = os.getenv(f"{prefix}_USERNAME"), os.getenv(f"{prefix}_PASSWORD")
        if not (username and password):
            log(f"{prefix}_USERNAME and {prefix}_PASSWORD not set: no {role.lower()} login in Fineract")
            continue
        core.ensure_service_user(username, password, role, permissions)
        log(f"Fineract login {username}: {', '.join(permissions)}")


def ledger_read_user(ledger: OdooLedger) -> None:
    username, password = os.getenv("ODOO_READ_USERNAME"), os.getenv("ODOO_READ_PASSWORD")
    if not (username and password):
        log("ODOO_READ_USERNAME and ODOO_READ_PASSWORD not set: no read-only login in Odoo")
        return
    ledger.ensure_read_user(username, password)
    log(f"Odoo login {username}: read-only accounting")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--borrowers", type=int, default=int(os.getenv("SEED_BORROWERS", "2000")))
    ap.add_argument("--seed", type=int, default=int(os.getenv("SEED_RANDOM", "42")))
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, help="default: today, or the first run's date")
    ap.add_argument("--files-only", action="store_true", help="write the week's files without loading anything")
    ap.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data")))
    args = ap.parse_args(argv)

    telemetry.setup("repaydesk-seed")
    try:
        with tracer.start_as_current_span("seed") as root:
            return _run(args, root)
    finally:
        telemetry.shutdown()


def _run(args, root) -> int:
    conn = None
    previous = None
    if not args.files_only:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        applied = db.migrate(conn)
        log(f"database migrated{': ' + ', '.join(applied) if applied else ' (nothing new)'}")
        previous = conn.execute(
            "SELECT random_seed, borrowers, as_of, finished_at FROM seed_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if previous:
            args.seed, args.borrowers, args.as_of = previous[0], previous[1], previous[2]
            log(f"reusing the first run's settings: seed {args.seed}, {args.borrowers} borrowers, as of {args.as_of}")

    as_of = args.as_of or bank_today()
    root.set_attributes({"seed.random": args.seed, "seed.borrowers": args.borrowers, "seed.as_of": str(as_of)})

    with tracer.start_as_current_span("generate"):
        book = Generator(args.seed, as_of, os.getenv("BANK_NAME", "Demo Microfinance Bank")).generate(args.borrowers)
    log(f"generated {len(book.borrowers)} borrowers, {len(book.groups)} groups, {len(book.loans)} loans, "
        f"{len(book.history)} past repayments, {len(book.lines)} payment lines for "
        f"{book.week_start} to {book.as_of - timedelta(days=1)}")

    counts: dict[str, int] = {}
    if not args.files_only:
        run_id = conn.execute(
            "INSERT INTO seed_runs (random_seed, borrowers, as_of) VALUES (%s, %s, %s) RETURNING id",
            (args.seed, args.borrowers, as_of),
        ).fetchone()[0]

        core = FineractCoreBanking(
            os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
            os.getenv("FINERACT_USERNAME", "mifos"), os.getenv("FINERACT_PASSWORD", "password"),
        )
        ledger = OdooLedger(
            os.environ["ODOO_URL"], os.getenv("ODOO_DB", "ledger"),
            os.getenv("ODOO_USERNAME", "admin"), os.getenv("ODOO_PASSWORD", "admin"),
        )
        wait_for("Fineract", core.health)
        wait_for("Odoo", ledger.health)
        counts = load(book, core, ledger)
        service_users(core)
        ledger_read_user(ledger)

        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO virtual_accounts (account_number, loan_ref, borrower_ref) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(loan.virtual_account, loan.ref, loan.borrower_ref) for loan in book.loans],
            )
        counts["virtual_accounts"] = len(book.loans)

        # The directory service syncs every 15 minutes; a fresh demo shouldn't wait for it.
        with tracer.start_as_current_span("directory.sync"):
            synced = directory.sync(conn, core)
        if synced:
            log(f"borrower directory: {synced['borrowers']} borrowers, {synced['loans']} loans")

    with tracer.start_as_current_span("write.files"):
        files = write_week(book, args.data_dir, os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
    counts.update({f"lines.{k}": v for k, v in files.items()})
    log(f"wrote the week's files to {args.data_dir / 'inbox'}: " + ", ".join(f"{k} {v}" for k, v in files.items()))

    if conn:
        with conn.transaction():
            conn.execute("UPDATE seed_runs SET finished_at = now(), counts = %s WHERE id = %s", (Jsonb(counts), run_id))
            db.audit(conn, "service:seed", "seed.completed", f"seed_run:{run_id}", counts, telemetry.current_trace_id())
    trace_id = telemetry.current_trace_id()
    if trace_id:
        log(f"trace: http://localhost:16686/trace/{trace_id}")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
