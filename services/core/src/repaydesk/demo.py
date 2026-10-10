"""Milestone 2's demo, start to finish, against the running stack.

1. **A statement arrives.** A new Northgate Bank statement, dated today at the bank, lands in the
   inbox: four repayments quoting live loans (one above the two-person threshold), a reversal of a
   failed transfer, and a transfer that names nobody.
2. **It is ingested and matched** by the same code as every other file: ingestion's `ingest_file`,
   then the rule matcher over the file's lines.
3. **People approve** the rules' proposals through the API, as the web app does: Amaka (officer)
   first, Tunde (supervisor) where a second approval or a higher limit is needed.
4. **The gateway posts** each approved match. The demo waits for it, then asks Fineract and Odoo
   themselves: each repayment under its idempotency key, each journal entry by its reference.

The nameless transfer stays unmatched: that's the investigator agent's job, in milestone 3.
Exits non-zero if anything didn't land where it should.

    python -m repaydesk.demo                  # in the stack: python scripts/dev.py demo
    python -m repaydesk.demo --for-review     # stop after matching; people approve in the web app
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from repaydesk import approvals, db, telemetry
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.domain import BANK_TIMEZONE, bank_today, naira
from repaydesk.ingest import ingest_file
from repaydesk.match import LINE_COLUMNS, RuleMatcher, _line, save, virtual_account_lookup
from repaydesk.synth.names import COLLECTION_ACCOUNTS, SENDER_BANKS
from repaydesk.synth.writers import write_northgate_day


@dataclass
class Payment:
    loan_ref: str
    name: str
    amount: int


# Loans that already have money on the way (proposed, or approved and not yet posted) are left out,
# so the demo's instalment really is the next one due.
_CANDIDATES = """
SELECT l.ref, b.name FROM loans l JOIN borrowers b ON b.ref = l.borrower_ref
WHERE l.status = 'active' AND NOT EXISTS (
    SELECT 1 FROM proposals p, jsonb_array_elements(p.allocations) a
    WHERE a->>'loan_ref' = l.ref AND p.status IN ('pending', 'approved')
      AND NOT EXISTS (SELECT 1 FROM posting_outbox o WHERE o.proposal_id = p.id AND o.status = 'posted'))
ORDER BY l.principal_kobo {order}, l.ref
LIMIT 60
"""


def pick_payments(conn: psycopg.Connection, core, threshold: int, rng: random.Random) -> list[Payment]:
    """Three instalments within one person's approval, and one that needs two people."""
    def due(order: str, small: bool, wanted: int) -> list[Payment]:
        rows = conn.execute(_CANDIDATES.format(order=order)).fetchall()
        rng.shuffle(rows)
        found = []
        for ref, name in rows:
            view = core.get_loan(ref)  # live: the directory may be up to 15 minutes behind
            upcoming = [i for i in view.installments if i.outstanding > 0] if view and view.status == "active" else []
            if upcoming and (upcoming[0].outstanding <= threshold) == small:
                found.append(Payment(ref, name, upcoming[0].outstanding))
                if len(found) == wanted:
                    break
        return found

    small, large = due("ASC", True, 3), due("DESC", False, 1)
    if len(small) < 3 or not large:
        raise SystemExit("[demo] not enough active loans with an instalment due; is the stack seeded?")
    return small + large


def write_statement(inbox: Path, bank_name: str, on: date, payments: list[Payment], rng: random.Random) -> Path:
    _name, number = COLLECTION_ACCOUNTS["bank_csv"]
    stamp = datetime.now(ZoneInfo(BANK_TIMEZONE)).strftime("%H%M%S")
    ref = iter(f"NGT{on:%y%m%d}{stamp}{i}" for i in range(1, 100))
    rows = [(next(ref), f"NIP/{rng.choice(SENDER_BANKS)}/{p.name.upper()}/{p.loan_ref} RPYMT", 0, p.amount)
            for p in payments]
    rows.append((next(ref), f"REVERSAL NIP/FAILED TRF/{stamp}", 0, 2_500_000_00))
    rows.append((next(ref), f"NIP/{rng.choice(SENDER_BANKS)}/ADUNNI STORES/MONTHLY", 0, 47_350_00))
    folder = inbox / "northgate"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"northgate_{number}_{on:%Y%m%d}_{stamp}.csv"
    write_northgate_day(path, bank_name, number, on, rows, 18_500_000_00)
    return path


class Api:
    """The API, called the way the web app calls it."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def call(self, method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def sign_in(self, staff_id: str, password: str) -> str:
        status, body = self.call("POST", "/auth/sign-in", body={"staff_id": staff_id, "password": password})
        if status != 200:
            raise SystemExit(f"[demo] {staff_id} couldn't sign in ({status}); is STAFF_DEMO_PASSWORD set?")
        return body["token"]


def approve_all(conn: psycopg.Connection, api: Api, tokens: dict[str, str], line_ids: list[str]) -> None:
    pending = conn.execute(
        "SELECT p.id, p.kind, l.amount_kobo FROM proposals p JOIN payment_lines l ON l.id = p.line_id "
        "WHERE p.line_id = ANY(%s) AND p.status = 'pending' ORDER BY l.amount_kobo", (line_ids,)).fetchall()
    for pid, kind, amount in pending:
        said = []
        for who in ("amaka", "tunde"):
            status, body = api.call("POST", f"/proposals/{pid}/approve", tokens[who])
            said.append(f"{who}: {body.get('status') or body.get('refused')}")
            if body.get("status") == "approved":
                break
        print(f"[demo]   {kind:<7} {naira(amount):>14}  " + ", ".join(said), flush=True)


def wait_for_postings(conn: psycopg.Connection, line_ids: list[str], timeout: float) -> list[tuple]:
    deadline = time.monotonic() + timeout
    while True:
        rows = conn.execute(
            "SELECT o.idempotency_key, o.status, o.last_error, o.core_transactions, o.ledger_entry, o.amount_kobo "
            "FROM posting_outbox o WHERE o.line_id = ANY(%s) ORDER BY o.id",
            (line_ids,)).fetchall()
        if all(r[1] != "pending" for r in rows) or time.monotonic() > deadline:
            return rows
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--for-review", action="store_true",
                    help="stop after matching, so people approve in the web app's review queue")
    args = ap.parse_args(argv)
    from repaydesk.adapters.fineract import FineractCoreBanking
    from repaydesk.adapters.odoo import OdooLedger

    telemetry.setup("repaydesk-demo")
    try:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"])
        ledger = OdooLedger(os.environ["ODOO_URL"], os.getenv("ODOO_DB", "ledger"),
                            os.getenv("ODOO_USERNAME", "admin"), os.getenv("ODOO_PASSWORD", "admin"))
        api = Api(os.getenv("API_URL", "http://api:8000"))
        tokens = {} if args.for_review else {
            who: api.sign_in(who, os.environ.get("STAFF_DEMO_PASSWORD", "")) for who in ("amaka", "tunde")}
        inbox = Path(os.getenv("DATA_DIR", "data")) / "inbox"
        on, rng = bank_today(), random.Random()

        payments = pick_payments(conn, core, approvals.TWO_PERSON_ABOVE_KOBO, rng)
        path = write_statement(inbox, os.getenv("BANK_NAME", "Demo Microfinance Bank"), on, payments, rng)
        print(f"[demo] 1. a statement arrived: {path.name}, 6 lines", flush=True)

        source = FolderStatementSource(inbox, os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
        result = ingest_file(conn, source, path)
        line_ids = [r[0] for r in conn.execute(
            "SELECT l.id FROM payment_lines l JOIN statement_files f ON f.id = l.file_id WHERE f.file_name = %s "
            "ORDER BY l.id", (path.name,))]
        print(f"[demo] 2. ingested: {result.stored} lines stored, {result.duplicates} duplicates", flush=True)

        matcher = RuleMatcher(core, virtual_account_lookup(conn))
        outcomes = []
        for row in conn.execute(f"SELECT {LINE_COLUMNS} FROM payment_lines WHERE id = ANY(%s) AND status = 'new'",
                                (line_ids,)).fetchall():
            proposal = matcher.propose(_line(row))
            if proposal:
                save(conn, proposal)
            outcomes.append(proposal.rule if proposal else "left_for_agent")
        print("[demo]    matched by rule: " + ", ".join(f"{o} {outcomes.count(o)}" for o in sorted(set(outcomes))),
              flush=True)

        if args.for_review:
            print("[demo] 3. waiting for people: open http://127.0.0.1:18082/review", flush=True)
            return 0
        print("[demo] 3. approved through the API:", flush=True)
        approve_all(conn, api, tokens, line_ids)

        rows = wait_for_postings(conn, line_ids, float(os.getenv("DEMO_TIMEOUT", "120")))
        print("[demo] 4. posted by the gateway, then checked in Fineract and Odoo:", flush=True)
        ok = len(rows) == len(payments)
        for key, status, error, txns, entry, amount in rows:
            in_core = all(any(r.reference == t["external_id"] for r in core.list_repayments(t["loan_ref"]))
                          for t in txns or [])
            in_ledger = bool(entry) and ledger.find_entry(key) == entry
            landed = status == "posted" and bool(txns) and in_core and in_ledger
            ok &= landed
            loans = ", ".join(f"{t['loan_ref']} #{t['transaction_id']}" for t in txns or []) or "-"
            print(f"[demo]   {key:<9} {naira(amount):>12}  {status:<7} Fineract {loans} "
                  f"{'found' if in_core and txns else 'MISSING'}; Odoo {entry or '-'} "
                  f"{'found' if in_ledger else 'MISSING'}" + (f"  ({error})" if error and not landed else ""),
                  flush=True)

        left = conn.execute("SELECT status, count(*) FROM payment_lines WHERE id = ANY(%s) GROUP BY 1 ORDER BY 1",
                            (line_ids,)).fetchall()
        print("[demo]    the file's lines now: " + ", ".join(f"{s} {n}" for s, n in left), flush=True)
        print("[demo]    open http://127.0.0.1:18082/today to see it", flush=True)
        if not ok:
            print("[demo] FAILED: not every approved payment reached both books", flush=True)
        return 0 if ok else 1
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
