"""Promises to pay, kept or broken, by what core banking shows was repaid.

The policy (docs/collections-policy.md): a promise is **kept** when repayments of at least the
promised amount post to the loan between the day it was made and the promised date, and **broken**
when the date passes without them. A kept promise is settled as soon as the money is there; a broken
one only the day after its date, since the borrower has all of the promised day to pay. A broken
promise puts the loan back on the morning list, first, as its own kind of message (`queue`).

Repayments are read live from core banking, so money paid at a branch, by transfer or through
Repaydesk all counts. The directory service settles promises after each sync; this runs it by hand:

    python -m repaydesk.collections.promises
"""

from __future__ import annotations

import os
import sys
from datetime import date

import psycopg

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.domain import bank_today, naira

ACTOR = "service:promises"


def settle(conn: psycopg.Connection, core: CoreBankingReader, today: date, as_of: date | None = None,
           loans: list[str] | None = None) -> dict:
    """Settle every open promise that can be settled, or only those on `loans`. `as_of` checks as if
    it were that day (the demo uses it, on its own loans, to show a broken promise without waiting a
    day); it's recorded on what it settles."""
    when = as_of or today
    counts = {"kept": 0, "broken": 0, "open": 0}
    for pid, loan_ref, amount, made_on, due_on in conn.execute(
            "SELECT id, loan_ref, amount_kobo, made_on, due_on FROM promises WHERE status = 'open' "
            "AND (%s::text[] IS NULL OR loan_ref = ANY(%s)) ORDER BY due_on, id", (loans, loans)).fetchall():
        paid = sum(r.amount for r in core.list_repayments(loan_ref) if made_on <= r.on <= due_on)
        if paid >= amount:
            status, note = "kept", f"{naira(paid)} repaid by {due_on:%d %b}, against {naira(amount)} promised"
        elif when > due_on:
            status, note = "broken", f"{naira(paid)} of {naira(amount)} repaid by {due_on:%d %b}"
        else:
            counts["open"] += 1
            continue
        if as_of:
            note += f" (checked as of {as_of:%d %b})"
        with conn.transaction():
            changed = conn.execute(
                "UPDATE promises SET status = %s, resolved_at = now(), note = %s WHERE id = %s AND status = 'open'",
                (status, note, pid)).rowcount
            if changed:
                db.audit(conn, ACTOR, f"promise.{status}", f"loan:{loan_ref}",
                         {"promise_id": pid, "paid_kobo": paid, "amount_kobo": amount, "due_on": due_on.isoformat(),
                          **({"as_of": as_of.isoformat()} if as_of else {})}, telemetry.current_trace_id())
                counts[status] += 1
    return counts


def main() -> int:
    from repaydesk.adapters.fineract import FineractCoreBanking

    telemetry.setup("repaydesk-promises")
    try:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=30)
        counts = settle(conn, core, bank_today())
        print(f"[promises] kept {counts['kept']}, broken {counts['broken']}, still open {counts['open']}")
        return 0
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
