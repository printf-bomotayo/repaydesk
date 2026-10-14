"""Promises to pay, kept or broken by what core banking shows was repaid, and what a broken one does
to the morning list."""

from datetime import date, timedelta

import pytest

from repaydesk import staff
from repaydesk.collections import promises, queue

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 8)


def promise(conn, item, due_on, amount, made_on=TODAY - timedelta(days=2)):
    return conn.execute(
        "INSERT INTO promises (loan_ref, borrower_ref, amount_kobo, due_on, made_on, source, recorded_by) "
        "VALUES (%s, %s, %s, %s, %s, 'staff', 'ngozi') RETURNING id",
        (item["loan_ref"], item["borrower_ref"], amount, due_on, made_on)).fetchone()[0]


def status(conn, pid):
    return conn.execute("SELECT status, note FROM promises WHERE id = %s", (pid,)).fetchone()


def due(conn, today=TODAY):
    return queue.overview(conn, today, limit=500)["due"]["items"]


def test_a_promise_is_kept_as_soon_as_the_money_is_there_and_broken_only_after_its_day(placed_week, conn):
    bk, _ = placed_week
    a, b, c = due(conn)[:3]
    kept = promise(conn, a, TODAY + timedelta(days=3), 20_000_00)
    short = promise(conn, b, TODAY, 20_000_00)
    early = promise(conn, c, TODAY, 20_000_00)
    bk.core.post_repayment(a["loan_ref"], TODAY, 12_000_00, "T-A1")
    bk.core.post_repayment(a["loan_ref"], TODAY, 8_000_00, "T-A2")   # two payments add up
    bk.core.post_repayment(b["loan_ref"], TODAY, 5_000_00, "T-B1")   # not enough
    bk.core.post_repayment(c["loan_ref"], TODAY - timedelta(days=5), 20_000_00, "T-C1")  # before the promise

    assert promises.settle(conn, bk.core, TODAY) == {"kept": 1, "broken": 0, "open": 2}
    assert status(conn, kept) == ("kept", "₦20,000.00 repaid by 11 Oct, against ₦20,000.00 promised")
    assert status(conn, short)[0] == "open"  # today is the promised day: there's still time

    tomorrow = TODAY + timedelta(days=1)
    assert promises.settle(conn, bk.core, tomorrow) == {"kept": 0, "broken": 2, "open": 0}
    assert status(conn, short) == ("broken", "₦5,000.00 of ₦20,000.00 repaid by 08 Oct")
    assert status(conn, early)[0] == "broken"  # paid before promising doesn't count
    assert promises.settle(conn, bk.core, tomorrow) == {"kept": 0, "broken": 0, "open": 0}  # settled once
    actions = [a for (a,) in conn.execute("SELECT action FROM audit_log WHERE actor = 'service:promises' ORDER BY id")]
    assert actions == ["promise.kept", "promise.broken", "promise.broken"]


def test_checking_as_of_another_day_says_so(placed_week, conn):
    bk, _ = placed_week
    item = due(conn)[0]
    pid = promise(conn, item, TODAY, 20_000_00)
    promises.settle(conn, bk.core, TODAY, as_of=TODAY + timedelta(days=1))
    assert status(conn, pid) == ("broken", "₦0.00 of ₦20,000.00 repaid by 08 Oct (checked as of 09 Oct)")
    detail = conn.execute("SELECT detail FROM audit_log WHERE action = 'promise.broken'").fetchone()[0]
    assert detail["as_of"] == "2026-10-09"


def test_a_broken_promise_comes_back_first_until_a_message_is_drafted_about_it(placed_week, conn):
    bk, _ = placed_week
    items = due(conn)
    last = items[-1]  # the least late: last on the list
    pid = promise(conn, last, TODAY, 20_000_00)
    assert last["loan_ref"] not in [d["loan_ref"] for d in due(conn)]  # promised: left alone

    promises.settle(conn, bk.core, TODAY, as_of=TODAY + timedelta(days=1))
    first = due(conn)[0]
    assert (first["loan_ref"], first["bucket"], first["promise"]["status"]) == (last["loan_ref"], "broken_promise",
                                                                                "broken")

    # The follow-up is drafted and sent: the loan is late like any other again, not "broken" forever.
    staff.ensure_demo_staff(conn, "pw-for-tests")
    conn.execute(
        "INSERT INTO collection_messages (loan_ref, borrower_ref, bucket, channel, body, amount_due_kobo, drafted_by, "
        "status, decided_by, decided_at, idempotency_key, to_phone, send_after, sent_at, provider_id) "
        "VALUES (%s, %s, 'broken_promise', 'sms', 'x', 100, 'agent:collector', 'sent', 'ngozi', now(), 'CM-T9', "
        "'07005000000', now(), now(), 'SIM-9')", (last["loan_ref"], last["borrower_ref"]))
    again = {d["loan_ref"]: d for d in due(conn)}
    assert again[last["loan_ref"]]["bucket"] == last["bucket"] and due(conn)[0]["loan_ref"] != last["loan_ref"]
    assert queue.overview(conn, TODAY, bucket="broken_promise")["due"]["items"] == []
    assert status(conn, pid)[0] == "broken"


def test_a_newer_kept_promise_ends_an_older_broken_one(placed_week, conn):
    bk, _ = placed_week
    item = due(conn)[0]
    old = promise(conn, item, TODAY - timedelta(days=1), 20_000_00, made_on=TODAY - timedelta(days=3))
    promises.settle(conn, bk.core, TODAY)
    assert status(conn, old)[0] == "broken" and due(conn)[0]["bucket"] == "broken_promise"
    promise(conn, item, TODAY + timedelta(days=2), 10_000_00, made_on=TODAY)
    bk.core.post_repayment(item["loan_ref"], TODAY, 10_000_00, "T-K1")
    assert promises.settle(conn, bk.core, TODAY)["kept"] == 1
    assert "broken_promise" not in [d["bucket"] for d in due(conn)]
