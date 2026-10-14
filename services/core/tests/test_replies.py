"""Borrowers' replies: how they're read, a reply with a date becoming a promise to pay, the rest
waiting for a person, and the simulator delivering them as a provider would."""

from datetime import date, timedelta

import pytest

from repaydesk import staff
from repaydesk.approvals import Refused
from repaydesk.collections import queue, replies
from repaydesk.collections.replies import read
from repaydesk.staff import LIMITS, Staff

WEDNESDAY = date(2026, 10, 14)


@pytest.mark.parametrize(("text", "kind", "due_on", "amount_kobo"), [
    ("I go pay Friday", "promise", date(2026, 10, 16), None),            # Pidgin
    ("Friday", "promise", date(2026, 10, 16), None),                     # just the day
    ("next friday I go clear am", "promise", date(2026, 10, 23), None),
    ("next tomorrow I go send am", "promise", date(2026, 10, 16), None),  # Pidgin: the day after tomorrow
    ("I will pay ₦30,000 tomorrow", "promise", date(2026, 10, 15), 30_000_00),
    ("Will pay 20k by end of the month", "promise", date(2026, 10, 31), 20_000_00),
    ("I will pay on 20/10", "promise", date(2026, 10, 20), None),
    ("pay 15th oct", "promise", date(2026, 10, 15), None),
    ("I fit pay 25th", "promise", date(2026, 10, 25), None),
    ("I don pay", "says_paid", None, None),
    ("I have paid, check am", "says_paid", None, None),
    ("How much is left?", "question", None, None),
    ("who be this?", "question", None, None),
    ("STOP", "stop", None, None),
    ("Please don't message me again", "stop", None, None),
    ("I will call you on Friday", "unclear", None, None),                # a date, but not paying
    ("pay on 3/10", "unclear", None, None),                              # already past
    ("I'll pay on the 15th of November", "unclear", None, None),         # more than a month: a plan
    ("pay tomorrow, ₦10,000 and ₦20,000", "unclear", None, None),        # which amount?
    ("I paid yesterday, will pay rest friday", "unclear", None, None),   # paid, or promising?
    ("Abeg give me till next week", "unclear", None, None),              # no date to hold them to
])
def test_replies_are_read_by_rules_a_person_can_check(text, kind, due_on, amount_kobo):
    r = read(text, WEDNESDAY)
    assert (r.kind, r.due_on, r.amount_kobo) == (kind, due_on, amount_kobo), r.why
    assert r.why


def test_a_day_and_month_without_a_year_means_the_coming_one():
    assert read("pay 5/1", date(2026, 12, 20)).due_on == date(2027, 1, 5)


# ---- against the database ------------------------------------------------------------------------

TODAY = date(2026, 10, 8)
NGOZI = Staff("ngozi", "Ngozi Eze", "manager", LIMITS["manager"])
AMAKA = Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"])


@pytest.fixture
def messaged(placed_week, conn):
    """The first loan due a message, with one sent to the borrower's phone."""
    staff.ensure_demo_staff(conn, "pw-for-tests")
    item = queue.overview(conn, TODAY, limit=50)["due"]["items"][0]
    phone = conn.execute("SELECT phone FROM borrowers WHERE ref = %s", (item["borrower_ref"],)).fetchone()[0]
    message_id = conn.execute(
        "INSERT INTO collection_messages (loan_ref, borrower_ref, bucket, channel, body, amount_due_kobo, drafted_by, "
        "status, decided_by, decided_at, idempotency_key, to_phone, send_after, sent_at, provider_id) "
        "VALUES (%s, %s, 'follow_up', 'sms', 'Good morning...', %s, 'agent:collector', 'sent', 'ngozi', now(), "
        "'CM-T1', %s, now(), now(), 'SIM-000001') RETURNING id",
        (item["loan_ref"], item["borrower_ref"], item["arrears_kobo"], phone)).fetchone()[0]
    return item, phone, message_id


def promises(conn, loan_ref):
    return conn.execute("SELECT status, due_on, amount_kobo, source, recorded_by FROM promises WHERE loan_ref = %s "
                        "ORDER BY id", (loan_ref,)).fetchall()


@pytest.mark.db
def test_a_reply_with_a_date_becomes_a_promise_on_the_loan_messaged(messaged, conn):
    item, phone, message_id = messaged
    out = replies.receive(conn, "IN-000001", "sms", phone, "I go pay Friday", TODAY)  # 8 Oct is a Thursday
    assert out["outcome"] == "promise" and out["promise_id"] and not out["duplicate"]
    assert promises(conn, item["loan_ref"]) == [
        ("open", date(2026, 10, 9), item["arrears_kobo"], "reply", "rule:replies")]
    row = conn.execute("SELECT borrower_ref, loan_ref, message_id, handled_by, reading->>'why' FROM inbound_messages "
                       "WHERE id = %s", (out["inbound_id"],)).fetchone()
    assert row[:4] == (item["borrower_ref"], item["loan_ref"], message_id, "rule:replies") and "friday" in row[4]
    # Promised and not yet due: the borrower is left alone.
    assert item["loan_ref"] not in [d["loan_ref"] for d in queue.overview(conn, TODAY, limit=500)["due"]["items"]]

    # The provider delivers it again: the same reply, one promise.
    assert replies.receive(conn, "IN-000001", "sms", phone, "I go pay Friday", TODAY)["duplicate"]
    # A later promise replaces the earlier one.
    replies.receive(conn, "IN-000002", "sms", phone, "Sorry, I will pay 20k on 12/10", TODAY)
    assert [p[:3] for p in promises(conn, item["loan_ref"])] == [
        ("cancelled", date(2026, 10, 9), item["arrears_kobo"]), ("open", date(2026, 10, 12), 20_000_00)]
    actions = [a for (a,) in conn.execute("SELECT action FROM audit_log WHERE subject = %s ORDER BY id",
                                          (f"loan:{item['loan_ref']}",))]
    assert actions == ["reply.received", "promise.recorded", "reply.received", "promise.recorded"]


@pytest.mark.db
def test_what_isnt_a_promise_waits_for_a_person(messaged, conn):
    item, phone, _ = messaged
    paid = replies.receive(conn, "IN-000003", "sms", phone, "I don pay, abeg check am", TODAY)
    stranger = replies.receive(conn, "IN-000004", "sms", "07005999999", "Friday", TODAY)
    assert (paid["outcome"], stranger["outcome"]) == ("says_paid", "unknown_number")
    page = replies.replies(conn, waiting=True)
    assert page["waiting"] == 2 and [r["id"] for r in page["items"]] == [stranger["inbound_id"], paid["inbound_id"]]
    assert page["items"][1]["answers"] == "Good morning..." and page["items"][1]["borrower_name"]
    assert promises(conn, item["loan_ref"]) == []

    with pytest.raises(Refused) as e:
        replies.close(conn, paid["inbound_id"], AMAKA, "Found it")
    assert e.value.code == "not_allowed"
    with pytest.raises(Refused) as e:
        replies.close(conn, paid["inbound_id"], NGOZI, " ")
    assert e.value.code == "reason_required"
    replies.close(conn, paid["inbound_id"], NGOZI, "Found the payment on the 6th; it went to suspense.")
    with pytest.raises(Refused) as e:
        replies.close(conn, paid["inbound_id"], NGOZI, "again")
    assert e.value.code == "already_handled"
    assert replies.replies(conn, waiting=True)["waiting"] == 1


@pytest.mark.db
def test_a_person_records_the_promise_the_rules_wouldnt(messaged, conn):
    item, phone, _ = messaged
    unclear = replies.receive(conn, "IN-000005", "sms", phone, "Abeg give me till next week Wednesday", TODAY)
    # "Wednesday" is a date, but "give me till" isn't a paying word: a person reads it.
    assert unclear["outcome"] == "unclear"
    rid = unclear["inbound_id"]
    for due, amount, code in ((TODAY - timedelta(days=1), 100, "bad_date"),
                              (TODAY + timedelta(days=40), 100, "bad_date"),
                              (TODAY + timedelta(days=6), 0, "bad_amount")):
        with pytest.raises(Refused) as e:
            replies.record_promise(conn, rid, NGOZI, due, amount, TODAY)
        assert e.value.code == code
    other = conn.execute("SELECT ref FROM loans WHERE borrower_ref <> %s LIMIT 1",
                         (item["borrower_ref"],)).fetchone()[0]
    with pytest.raises(Refused) as e:
        replies.record_promise(conn, rid, NGOZI, TODAY + timedelta(days=6), 30_000_00, TODAY, loan_ref=other)
    assert e.value.code == "wrong_loan"

    replies.record_promise(conn, rid, NGOZI, TODAY + timedelta(days=6), 30_000_00, TODAY)
    assert promises(conn, item["loan_ref"]) == [("open", date(2026, 10, 14), 30_000_00, "staff", "ngozi")]
    handled = replies.replies(conn, waiting=False)["items"][0]
    assert handled["handled_by_name"] == "Ngozi Eze" and handled["promise"]["amount_kobo"] == 30_000_00


# ---- over HTTP, and from the simulator -----------------------------------------------------------


@pytest.mark.db
def test_the_simulator_delivers_a_signed_reply_that_becomes_a_promise(messaged, conn, schema_url, monkeypatch):
    from fastapi.testclient import TestClient

    from repaydesk.simulators import messaging

    item, phone, _ = messaged
    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("STAFF_DEMO_PASSWORD", "pw-for-tests")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("REPAYDESK_WEBHOOK_URL", "http://api.test/webhooks/messaging")
    monkeypatch.delenv("MESSAGING_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("repaydesk.domain.bank_today", lambda: TODAY)
    from repaydesk.api import app

    messaging.reset()
    sim = TestClient(messaging.app)
    with TestClient(app) as api:
        def post(url, body):  # the simulator's delivery, straight into the API under test
            r = api.post("/webhooks/messaging", content=body,
                         headers={"x-messaging-signature": messaging.sign(body), "Content-Type": "application/json"})
            return f"delivered: {r.json().get('outcome')}" if r.status_code == 200 else f"refused: {r.status_code}"

        monkeypatch.setattr(messaging, "post", post)
        assert api.post("/webhooks/messaging", content=b'{"event": "message.received"}',
                        headers={"x-messaging-signature": "forged"}).status_code == 401
        assert api.post("/webhooks/messaging", content=b"{}",
                        headers={"x-messaging-signature": messaging.sign(b"{}")}).status_code == 400

        r = sim.post(f"/phones/{phone}/reply", data={"body": "I go pay Friday"}, follow_redirects=False)
        assert r.status_code == 303
        page = sim.get("/").text
        assert "I go pay Friday" in page and "delivered: promise" in page
        assert promises(conn, item["loan_ref"])[0][:2] == ("open", date(2026, 10, 9))

        auth = {"Authorization": "Bearer dev-only-messaging-token"}
        assert sim.post("/v1/replies/IN-000001/redeliver", headers=auth).json()["delivery"] == "delivered: promise"
        assert len(promises(conn, item["loan_ref"])) == 1  # delivered twice, kept once

        token = api.post("/auth/sign-in", json={"staff_id": "ngozi", "password": "pw-for-tests"}).json()["token"]
        ngozi = {"Authorization": f"Bearer {token}"}
        q = sim.post("/v1/replies", json={"from": phone, "body": "How much is left?"}, headers=auth).json()
        assert q["delivery"] == "delivered: question" and q["channel"] == "sms"  # the channel last used
        waiting = api.get("/collections/replies", params={"waiting": True}, headers=ngozi).json()
        assert waiting["waiting"] == 1 and waiting["items"][0]["body"] == "How much is left?"
        rid = waiting["items"][0]["id"]
        r = api.post(f"/collections/replies/{rid}/promise", headers=ngozi,
                     json={"due_on": (TODAY + timedelta(days=60)).isoformat(), "amount_kobo": 100})
        assert r.status_code == 422 and r.json()["refused"] == "bad_date"
        assert api.post(f"/collections/replies/{rid}/close", json={"note": "Called back: ₦58,080 left."},
                        headers=ngozi).status_code == 200
        assert api.post(f"/collections/replies/{rid}/close", json={"note": "x"}, headers=ngozi).status_code == 409
