"""Milestone 4's demo, start to finish, against the running stack: two late loans, from the morning
list to a promise kept and a promise broken.

1. **The morning list.** The first two loans due a message, from borrowers with a phone.
2. **Drafted.** By the drafting agent with `--model` (needs ANTHROPIC_API_KEY); otherwise from a
   fixed template, so the demo runs without a model. Either way, every draft goes through the
   collections policy's checks.
3. **Approved** by Ngozi, the portfolio manager, through the API, as the Messages screen does: the
   policy is checked again against the loan in core banking.
4. **Sent** by the gateway to the SMS and WhatsApp simulator, inside contact hours (08:00 to 18:00
   Lagos, Monday to Saturday). Outside them the demo stops here and says when they'll go.
5. **The borrowers reply** from the simulator: one "I go pay tomorrow", one "I go pay today".
   Each becomes a promise to pay on its loan.
6. **One promise is kept.** That borrower's payment arrives on a bank statement and goes the whole
   milestone 2 way: ingested, matched by rule, approved, posted by the gateway to Fineract and
   Odoo. Checked against Fineract's repayments, the promise is kept.
7. **One is broken.** The other borrower doesn't pay. Checked as of tomorrow (the demo can't wait a
   day, and says so on the promise), it's broken, and the loan is back at the top of the morning
   list as a broken promise.

Exits non-zero if anything didn't happen as it should.

    python -m repaydesk.collections.demo            # in the stack: python scripts/dev.py demo-m4
    python -m repaydesk.collections.demo --model    # drafts by the drafting agent
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import psycopg
from psycopg.types.json import Jsonb

from repaydesk import db, telemetry
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.collections import drafter, policy, promises, queue
from repaydesk.demo import Api, Payment, approve_all, wait_for_postings, write_statement
from repaydesk.domain import bank_today, naira
from repaydesk.ingest import ingest_file
from repaydesk.match import LINE_COLUMNS, RuleMatcher, _line, save, virtual_account_lookup

TEMPLATE_ACTOR = "demo:template"


def say(msg: str) -> None:
    print(f"[demo] {msg}", flush=True)


def template(case: drafter.Case) -> str:
    """What the drafting agent would write, near enough, for when there's no model."""
    asks = {"reminder": "Please pay", "follow_up": "Please pay, or reply with the date you will pay,",
            "arrangement": "Please reply or call your branch so we can agree a plan, or pay",
            "broken_promise": "Please pay, or reply with a new date,"}[case.bucket]
    return (f"Good morning {case.first_name}, this is Demo Microfinance Bank. Your loan {case.loan_ref} has "
            f"{case.brief()['amount_due']} due. {asks} into your loan's virtual account or at any branch with the "
            f"loan reference. Reply here if you'd like to talk.")


def save_template_draft(conn: psycopg.Connection, case: drafter.Case) -> tuple[int, list[dict]]:
    body = template(case)
    checks = policy.check(body, case.channel, case.facts())
    with conn.transaction():
        mid = conn.execute(
            "INSERT INTO collection_messages (loan_ref, borrower_ref, bucket, channel, body, amount_due_kobo, "
            "drafted_by, checks) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (case.loan_ref, case.borrower_ref, case.bucket, case.channel, body, case.amount_due_kobo, TEMPLATE_ACTOR,
             Jsonb(checks))).fetchone()[0]
        db.audit(conn, TEMPLATE_ACTOR, "message.drafted", f"loan:{case.loan_ref}",
                 {"message_id": mid, "bucket": case.bucket, "passes_policy": policy.sendable(checks)},
                 telemetry.current_trace_id())
    return mid, checks


class Simulator:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="store_true", help="draft with the drafting agent (uses the API key)")
    args = ap.parse_args(argv)
    from repaydesk.adapters.fineract import FineractCoreBanking

    telemetry.setup("repaydesk-demo-m4")
    try:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=30)
        api = Api(os.getenv("API_URL", "http://api:8000"))
        password = os.environ.get("STAFF_DEMO_PASSWORD", "")
        tokens = {who: api.sign_in(who, password) for who in ("ngozi", "amaka", "tunde")}
        sim = Simulator(os.getenv("MESSAGING_URL", "http://messaging:8000"),
                        os.getenv("MESSAGING_TOKEN", "dev-only-messaging-token"))
        timeout = float(os.getenv("DEMO_TIMEOUT", "120"))
        today = bank_today()
        ok = True

        # 1. The morning list: two loans, two borrowers, both with a phone.
        cases: list[drafter.Case] = []
        for item in queue.overview(conn, today, None, 50)["due"]["items"]:
            has_phone = conn.execute("SELECT phone IS NOT NULL FROM borrowers WHERE ref = %s",
                                     (item["borrower_ref"],)).fetchone()[0]
            if not has_phone or any(c.borrower_ref == item["borrower_ref"] for c in cases):
                continue
            if case := drafter.gather(conn, core, item):
                cases.append(case)
            if len(cases) == 2:
                break
        if len(cases) < 2:
            raise SystemExit("[demo] fewer than two loans due a message; is the stack seeded?")
        total = queue.overview(conn, today, None, 1)["due"]["total"]
        taking = " and ".join(f"{c.loan_ref} ({c.first_name}, {c.bucket}, {naira(c.amount_due_kobo)})" for c in cases)
        say(f"1. the morning list: {total} loans due a message; taking {taking}")

        # 2. Drafted.
        ids = []
        if args.model:
            from repaydesk.investigate import model_client

            client, _ = model_client()
            out = anyio.run(drafter.draft_morning, conn, core, drafter.Drafter(client),
                            [{"loan_ref": c.loan_ref, "borrower_ref": c.borrower_ref,
                              "borrower_name": c.first_name, "bucket": c.bucket} for c in cases])
            ids = [o["message_id"] for o in out]
            if None in ids:
                raise SystemExit(f"[demo] the drafting agent didn't draft: {out}")
            say("2. drafted by the drafting agent: " + ", ".join(
                f"{o['loan_ref']} passes the policy: {o['passes_policy']}" for o in out))
        else:
            for c in cases:
                mid, checks = save_template_draft(conn, c)
                ids.append(mid)
                say(f"2. drafted from the template (no model): {c.loan_ref}, "
                    f"{sum(x['ok'] for x in checks)} of {len(checks)} policy checks pass")

        # 3. Approved by the portfolio manager.
        for c, mid in zip(cases, ids, strict=True):
            status, body = api.call("POST", f"/collections/messages/{mid}/approve", tokens["ngozi"], {})
            ok &= status == 200
            say(f"3. Ngozi approved message {mid} for {c.loan_ref}: "
                + (f"goes from {body['send_after']}" if status == 200 else f"REFUSED {body}"))
        if not ok:
            return 1

        # 4. Sent by the gateway, inside contact hours.
        if not policy.may_contact_at(datetime.now(UTC)):
            nxt = policy.next_contact_window(datetime.now(UTC))
            say(f"4. outside contact hours: the gateway holds both messages until {nxt:%a %d %b %H:%M} Lagos time.")
            say("   The rest of the demo needs them sent; run it again between 08:00 and 18:00, Monday to Saturday.")
            return 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = conn.execute("SELECT id, status, provider_id, to_phone, error FROM collection_messages "
                                "WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()
            if all(r[1] != "approved" for r in rows):
                break
            time.sleep(1)
        phones = {}
        for (mid, status, provider, to_phone, error), c in zip(rows, cases, strict=True):
            inbox = sim.call("GET", f"/v1/messages?to={to_phone}")["items"]
            arrived = any(m["client_ref"] == f"CM-{mid}" for m in inbox)
            ok &= status == "sent" and arrived
            phones[c.loan_ref] = to_phone
            say(f"4. sent by the gateway: {c.loan_ref} {status} {provider or ''} by {c.channel}; "
                f"in the phone's inbox: {'yes' if arrived else 'NO'}" + (f" ({error})" if error else ""))
        if not ok:
            return 1

        # 5. The borrowers reply.
        keeps, breaks = cases
        for c, text in ((keeps, "Thank you. I go pay tomorrow"), (breaks, "I go pay today, abeg")):
            r = sim.call("POST", "/v1/replies", {"from": phones[c.loan_ref], "body": text})
            row = conn.execute("SELECT status, due_on, amount_kobo FROM promises WHERE loan_ref = %s "
                               "AND status = 'open' ORDER BY id DESC LIMIT 1", (c.loan_ref,)).fetchone()
            ok &= r["delivery"] == "delivered: promise" and row is not None
            say(f"5. {c.first_name} replied \"{text}\": {r['delivery']}" +
                (f"; promise of {naira(row[2])} by {row[1]:%d %b}" if row else "; NO PROMISE"))
        if not ok:
            return 1
        promised = conn.execute("SELECT amount_kobo FROM promises WHERE loan_ref = %s AND status = 'open'",
                                (keeps.loan_ref,)).fetchone()[0]

        # 6. One pays: a statement, matched, approved, posted; then the promise is checked.
        name = conn.execute("SELECT name FROM borrowers WHERE ref = %s", (keeps.borrower_ref,)).fetchone()[0]
        inbox = Path(os.getenv("DATA_DIR", "data")) / "inbox"
        path = write_statement(inbox, os.getenv("BANK_NAME", "Demo Microfinance Bank"), today,
                               [Payment(keeps.loan_ref, name, promised)], random.Random())
        source = FolderStatementSource(inbox, os.getenv("PAYRAIL_WEBHOOK_SECRET", "dev-only-payrail-secret"))
        ingest_file(conn, source, path)
        line_ids = [r[0] for r in conn.execute(
            "SELECT l.id FROM payment_lines l JOIN statement_files f ON f.id = l.file_id WHERE f.file_name = %s "
            "AND l.narration LIKE %s", (path.name, f"%{keeps.loan_ref}%"))]
        matcher = RuleMatcher(core, virtual_account_lookup(conn))
        for row in conn.execute(f"SELECT {LINE_COLUMNS} FROM payment_lines WHERE id = ANY(%s) AND status = 'new'",
                                (line_ids,)).fetchall():
            if proposal := matcher.propose(_line(row)):
                save(conn, proposal)
        say(f"6. {name} paid {naira(promised)}: a statement line, matched by rule, approved:")
        approve_all(conn, api, tokens, line_ids)
        posted = wait_for_postings(conn, line_ids, timeout)
        ok &= bool(posted) and all(r[1] == "posted" for r in posted)
        say(f"   posted by the gateway: {', '.join(f'{r[0]} {r[1]}' for r in posted) or 'NOTHING'}")
        promises.settle(conn, core, today, loans=[keeps.loan_ref])
        kept = conn.execute("SELECT status, note FROM promises WHERE loan_ref = %s ORDER BY id DESC LIMIT 1",
                            (keeps.loan_ref,)).fetchone()
        ok &= kept[0] == "kept"
        say(f"   the promise is {kept[0]}: {kept[1]}")

        # 7. The other doesn't: checked as of tomorrow, the promise is broken, and the loan is back on top.
        tomorrow = today + timedelta(days=1)
        promises.settle(conn, core, today, as_of=tomorrow, loans=[breaks.loan_ref])
        broken = conn.execute("SELECT status, note FROM promises WHERE loan_ref = %s ORDER BY id DESC LIMIT 1",
                              (breaks.loan_ref,)).fetchone()
        first = queue.overview(conn, today, "broken_promise", 50)["due"]["items"]
        back = any(d["loan_ref"] == breaks.loan_ref for d in first)
        top = queue.overview(conn, today, None, 1)["due"]["items"]
        ok &= broken[0] == "broken" and back and bool(top) and top[0]["bucket"] == "broken_promise"
        say(f"7. {breaks.first_name} didn't pay. Checked as of {tomorrow:%d %b}: the promise is {broken[0]}: "
            f"{broken[1]}")
        say(f"   back on the morning list as a broken promise: {'yes' if back else 'NO'}; the list now starts "
            f"with {top[0]['loan_ref'] if top else '-'} ({top[0]['bucket_label'] if top else '-'})")
        say("   see it: http://127.0.0.1:18082/collections, /messages (Replies), and the phones at "
            "http://127.0.0.1:18083")
        if not ok:
            say("FAILED: something above didn't happen as it should")
        return 0 if ok else 1
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
