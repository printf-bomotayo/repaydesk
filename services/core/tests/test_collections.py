"""Collections foundations: the policy's checkable rules, the arrears buckets, who is due a message,
and the portfolio manager, who approves messages and never payments."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from repaydesk import approvals, staff
from repaydesk.collections import policy, queue
from repaydesk.collections.policy import Facts, bucket_for, check, may_contact_at, next_contact_window, sendable
from repaydesk.staff import LIMITS, Staff

LAGOS = ZoneInfo("Africa/Lagos")
FACTS = Facts(loan_ref="LN-2026-001209", amount_due_kobo=60_500_00, days_late=12, first_name="Zainab")
GOOD = ("Good afternoon Zainab, this is Demo Microfinance Bank. Your loan LN-2026-001209 has ₦60,500 due. "
        "Please pay into your loan's virtual account or at any branch with the loan reference. If something has "
        "made it hard to pay, reply here and we'll talk.")


def failed(body, channel="sms", facts=FACTS):
    return [c["check"] for c in check(body, channel, facts) if not c["ok"]]


# ---- the policy ----------------------------------------------------------------------------------


def test_a_good_message_passes_every_check():
    checks = check(GOOD, "sms", FACTS)
    assert sendable(checks) and len(checks) == 11


def test_threats_shame_secrets_links_and_shouting_are_caught():
    assert failed(GOOD + " Pay or the police will come.") == ["no_threats"]
    assert "no_threats" in failed(GOOD.replace("we'll talk", "your family will hear of it"), "whatsapp")
    assert failed(GOOD.replace("reply here", "send your BVN"), "whatsapp") == ["no_secrets"]
    assert failed(GOOD.replace("at any branch", "at demobank.ng/pay"), "whatsapp") == ["no_links"]
    assert failed(GOOD.replace("Please pay", "PLEASE PAY"), "whatsapp") == ["no_shouting"]
    assert failed(GOOD.replace("talk.", "talk! Now!"), "whatsapp") == ["no_shouting"]


def test_the_amount_reference_and_name_must_be_the_loans_own():
    assert failed(GOOD.replace("₦60,500", "₦65,000")) == ["amount_due"]
    for written in ("N60,500.00", "NGN 60500", "60,500 naira", "₦60,500.00"):
        assert failed(GOOD.replace("₦60,500", written)) == [], written
    assert failed(GOOD.replace("LN-2026-001209", "your loan")) == ["loan_reference"]
    assert failed(GOOD.replace("Zainab", "Customer")) == ["first_name"]


def test_who_is_messaged_and_how_often():
    assert failed(GOOD, facts=Facts(**{**FACTS.__dict__, "days_late": 120})) == ["in_scope"]
    assert failed(GOOD, facts=Facts(**{**FACTS.__dict__, "messages_last_7_days": 2})) == ["frequency"]
    promised = Facts(**{**FACTS.__dict__, "promise_open_until": date(2026, 10, 16), "today": date(2026, 10, 12)})
    assert failed(GOOD, facts=promised) == ["no_open_promise"]
    lapsed = Facts(**{**FACTS.__dict__, "promise_open_until": date(2026, 10, 9), "today": date(2026, 10, 12)})
    assert failed(GOOD, facts=lapsed) == []
    assert failed(GOOD * 2, "sms") == ["length"] and failed(GOOD, "whatsapp") == []
    assert [bucket_for(d) for d in (0, 1, 7, 8, 30, 31, 90, 91)] == [
        None, "reminder", "reminder", "follow_up", "follow_up", "arrangement", "arrangement", "recovery"]


def test_messages_go_out_between_eight_and_six_on_weekdays_and_saturday():
    monday_9 = datetime(2026, 10, 12, 9, 0, tzinfo=LAGOS)
    assert may_contact_at(monday_9)
    assert not may_contact_at(monday_9.replace(hour=18)) and not may_contact_at(monday_9.replace(hour=7, minute=59))
    sunday = datetime(2026, 10, 11, 12, 0, tzinfo=LAGOS)
    assert not may_contact_at(sunday) and next_contact_window(sunday) == monday_9.replace(hour=8)
    assert next_contact_window(monday_9) == monday_9
    saturday_evening = datetime(2026, 10, 17, 19, 0, tzinfo=LAGOS)
    assert next_contact_window(saturday_evening) == datetime(2026, 10, 19, 8, 0, tzinfo=LAGOS)  # past Sunday


# ---- the queue ---------------------------------------------------------------------------------


@pytest.mark.db
def test_the_morning_list_leaves_out_promised_drafted_and_twice_messaged_loans(placed_week, conn):
    today = date(2026, 10, 8)
    before = queue.overview(conn, today, limit=500)
    assert sum(b["loans"] for b in before["buckets"]) > 0
    due = before["due"]["items"]
    assert due and all(1 <= d["days_late"] <= 90 and d["arrears_kobo"] > 0 for d in due)
    assert all(d["bucket"] == policy.bucket_for(d["days_late"]) for d in due)
    a, b, c, d = (x["loan_ref"] for x in due[:4])
    borrower = {x["loan_ref"]: x["borrower_ref"] for x in due}

    def promise(loan, status, due_on):
        conn.execute("INSERT INTO promises (loan_ref, borrower_ref, amount_kobo, due_on, made_on, source, "
                     "recorded_by, status, resolved_at) VALUES (%s, %s, 100, %s, %s, 'staff', 'amaka', %s, %s)",
                     (loan, borrower[loan], due_on, today - timedelta(days=5), status,
                      None if status == "open" else datetime.now(LAGOS)))

    def message(loan, status, ago_days=0):
        sent = status == "sent"
        conn.execute("INSERT INTO collection_messages (loan_ref, borrower_ref, bucket, channel, body, "
                     "amount_due_kobo, drafted_by, status, decided_by, created_at, sent_at, provider_id) "
                     "VALUES (%s, %s, 'reminder', 'sms', 'x', 100, 'agent:collector', %s, %s, "
                     "now() - %s * interval '1 day', %s, %s)",
                     (loan, borrower[loan], status, None if status == "draft" else "ngozi", ago_days,
                      datetime.now(LAGOS) if sent else None, "sim-1" if sent else None))

    staff.ensure_demo_staff(conn, "x" * 16)
    promise(a, "open", today + timedelta(days=3))  # promised, not due yet: leave alone
    message(b, "draft")  # a draft is waiting for the manager
    message(c, "sent", 1)
    message(c, "sent", 3)  # two this week: enough
    promise(d, "broken", today - timedelta(days=1))  # broke a promise: comes back first, as its own kind

    after = queue.overview(conn, today, limit=500)
    refs = [x["loan_ref"] for x in after["due"]["items"]]
    assert a not in refs and b not in refs and c not in refs
    assert after["due"]["items"][0]["loan_ref"] == d and after["due"]["items"][0]["bucket"] == "broken_promise"
    assert after["drafts_waiting"] == 1 and after["promises"]["open"] == 1 and after["promises"]["broken"] == 1
    assert [x["loan_ref"] for x in queue.overview(conn, today, bucket="broken_promise")["due"]["items"]] == [d]


# ---- the portfolio manager ---------------------------------------------------------------------


def test_a_portfolio_manager_approves_messages_never_payments():
    ngozi = Staff("ngozi", "Ngozi Eze", "manager", LIMITS["manager"])
    assert ngozi.decides_messages and not ngozi.decides_payments
    verdict = approvals.judge(ngozi, 100_00, [])
    assert not verdict.allowed and verdict.message == "Portfolio managers approve messages, not payments."
    tunde = Staff("tunde", "Tunde Bakare", "supervisor", LIMITS["supervisor"])
    assert tunde.decides_messages and tunde.decides_payments
    amaka = Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"])
    assert not amaka.decides_messages
