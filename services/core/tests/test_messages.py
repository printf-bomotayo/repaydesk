"""A portfolio manager's decisions on drafted messages: approve as drafted or edited, reject, and the
policy enforced at approval against the loan as it is now."""

import dataclasses
from datetime import UTC, date, datetime

import pytest

from repaydesk import db, staff
from repaydesk.approvals import Refused
from repaydesk.collections import decide, drafter, policy, queue
from repaydesk.investigator import Run
from repaydesk.staff import LIMITS, Staff

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 8)
MONDAY_EVENING = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)  # 19:30 in Lagos: after hours
NGOZI = Staff("ngozi", "Ngozi Eze", "manager", LIMITS["manager"])
TUNDE = Staff("tunde", "Tunde Bakare", "supervisor", LIMITS["supervisor"])
AMAKA = Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"])
HADIZA = Staff("hadiza", "Hadiza Lawal", "auditor", LIMITS["auditor"])


@pytest.fixture(autouse=True)
def people(conn):
    staff.ensure_demo_staff(conn, "pw-for-tests")


def good(case: drafter.Case) -> str:
    return (f"Good morning {case.first_name}, this is Demo Microfinance Bank. Your loan {case.loan_ref} has "
            f"{case.brief()['amount_due']} due. Please pay into your loan's virtual account or at any branch with "
            f"the loan reference. Reply here if you'd like to talk.")


def drafted(conn, core, n=0, body=None) -> tuple[int, drafter.Case]:
    """A draft for the nth loan due a message, as the drafting agent saves it."""
    item = queue.overview(conn, TODAY, limit=50)["due"]["items"][n]
    case = drafter.gather(conn, core, item)
    text = body or good(case)
    out = drafter.record(conn, case, Run(case.loan_ref), drafter.Draft(body=text, note="n"),
                         policy.check(text, case.channel, case.facts()), 1)
    return out["message_id"], case


def row(conn, message_id):
    return conn.execute("SELECT status, body, drafted_body, decided_by, idempotency_key, to_phone, send_after, "
                        "amount_due_kobo, reject_reason FROM collection_messages WHERE id = %s",
                        (message_id,)).fetchone()


def test_a_manager_approves_a_draft_and_it_waits_for_contact_hours(placed_week, conn):
    bk, _ = placed_week
    mid, case = drafted(conn, bk.core)
    out = decide.approve(conn, bk.core, mid, NGOZI, now=MONDAY_EVENING)
    assert out == {"message_id": mid, "status": "approved", "edited": False,
                   "send_after": "2026-10-13T08:00:00+01:00"}  # Tuesday morning, Lagos
    r = row(conn, mid)
    phone = conn.execute("SELECT phone FROM borrowers WHERE ref = %s", (case.borrower_ref,)).fetchone()[0]
    assert r[0] == "approved" and r[2] is None and r[3] == "ngozi" and r[4] == f"CM-{mid}" and r[5] == phone
    assert r[6] == datetime(2026, 10, 13, 7, 0, tzinfo=UTC) and r[7] == case.amount_due_kobo
    action, detail = conn.execute("SELECT action, detail FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert action == "message.approved" and detail["edited"] is False
    # Approved and not yet sent: the loan stays off the morning list.
    assert case.loan_ref not in [d["loan_ref"] for d in queue.overview(conn, TODAY, limit=500)["due"]["items"]]


def test_an_edited_message_is_checked_again_and_the_drafters_words_are_kept(placed_week, conn):
    bk, _ = placed_week
    mid, case = drafted(conn, bk.core)
    with pytest.raises(Refused) as e:
        decide.approve(conn, bk.core, mid, NGOZI, body=good(case) + " Or we tell your family.")
    assert e.value.code == "fails_policy" and "family" in e.value.message
    assert row(conn, mid)[0] == "draft"

    edited = good(case).replace("Reply here if you'd like to talk.", "Reply here, or call your branch, to talk.")
    assert decide.approve(conn, bk.core, mid, TUNDE, body=edited)["edited"] is True  # a supervisor may too
    r = row(conn, mid)
    assert r[0] == "approved" and r[1] == edited and r[2] == good(case) and r[3] == "tunde"


def test_a_draft_that_failed_its_checks_cant_be_approved_until_fixed(placed_week, conn):
    bk, _ = placed_week
    item = queue.overview(conn, TODAY, limit=50)["due"]["items"][0]
    case = drafter.gather(conn, bk.core, item)
    mid, _ = drafted(conn, bk.core, body=good(case).replace(case.brief()["amount_due"], "₦1"))
    with pytest.raises(Refused) as e:
        decide.approve(conn, bk.core, mid, NGOZI)
    assert e.value.code == "fails_policy"
    assert decide.approve(conn, bk.core, mid, NGOZI, body=good(case))["status"] == "approved"


def test_the_amount_is_checked_against_core_banking_now_not_at_drafting(placed_week, conn):
    bk, _ = placed_week
    mid, case = drafted(conn, bk.core)

    class PaidSome:
        """Core banking after a part-payment since the draft was written."""

        def __getattr__(self, name):
            return getattr(bk.core, name)

        def get_loan(self, ref):
            view = bk.core.get_loan(ref)
            return dataclasses.replace(view, arrears=view.arrears - 10_000_00) if ref == case.loan_ref else view

    with pytest.raises(Refused) as e:
        decide.approve(conn, PaidSome(), mid, NGOZI)
    assert e.value.code == "fails_policy"
    assert f"₦{(case.amount_due_kobo - 10_000_00) / 100:,.2f} due" in e.value.message


def test_only_managers_and_supervisors_decide_and_a_rejection_needs_a_reason(placed_week, conn):
    bk, _ = placed_week
    mid, case = drafted(conn, bk.core)
    for who in (AMAKA, HADIZA):
        with pytest.raises(Refused) as e:
            decide.approve(conn, bk.core, mid, who)
        assert e.value.code == "not_allowed"
    with pytest.raises(Refused) as e:
        decide.reject(conn, mid, NGOZI, "  ")
    assert e.value.code == "reason_required"

    assert decide.reject(conn, mid, NGOZI, "Too stiff for a first reminder.")["status"] == "rejected"
    assert row(conn, mid)[0] == "rejected" and row(conn, mid)[8] == "Too stiff for a first reminder."
    with pytest.raises(Refused) as e:
        decide.approve(conn, bk.core, mid, NGOZI)
    assert e.value.code == "not_draft"
    # Rejected: nothing is waiting, so the loan is back on the morning list for a new draft.
    assert case.loan_ref in [d["loan_ref"] for d in queue.overview(conn, TODAY, limit=500)["due"]["items"]]


def test_the_drafts_list_shows_what_the_manager_needs(placed_week, conn):
    bk, _ = placed_week
    first, case = drafted(conn, bk.core)
    second, _ = drafted(conn, bk.core)  # the next loan on the list
    page = decide.messages(conn, "draft")
    assert [m["id"] for m in page["items"]] == [first, second]  # oldest first
    m = page["items"][0]
    assert m["loan_ref"] == case.loan_ref and m["passes_policy"] and m["note"] == "n"
    assert m["days_late"] == case.days_late
    assert page["counts"]["draft"] == 2
    decide.approve(conn, bk.core, first, NGOZI)
    assert [m["id"] for m in decide.messages(conn, "approved")["items"]] == [first]


# ---- over HTTP -----------------------------------------------------------------------------------


def test_the_api_approves_edits_and_rejects_with_the_refusals_the_screen_needs(placed_week, conn, schema_url,
                                                                                monkeypatch):
    from fastapi.testclient import TestClient

    bk, _ = placed_week
    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("STAFF_DEMO_PASSWORD", "pw-for-tests")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("FINERACT_URL", raising=False)
    from repaydesk.api import app

    first, case = drafted(conn, bk.core)
    second, _ = drafted(conn, bk.core)
    with TestClient(app) as c:
        def signed_in(who):
            token = c.post("/auth/sign-in", json={"staff_id": who, "password": "pw-for-tests"}).json()["token"]
            return {"Authorization": f"Bearer {token}"}

        ngozi, amaka = signed_in("ngozi"), signed_in("amaka")
        url = f"/collections/messages/{first}/approve"
        assert c.post(url, json={}, headers=ngozi).status_code == 503  # no core banking to check against
        app.state.core = bk.core

        assert c.get("/collections/messages", headers=ngozi).json()["counts"]["draft"] == 2
        assert c.get("/collections/messages", headers=ngozi, params={"status": "lost"}).status_code == 422
        assert c.post(url, json={}, headers=amaka).status_code == 403
        r = c.post(url, json={"body": good(case) + " Pay or the police will come."}, headers=ngozi)
        assert r.status_code == 422 and r.json()["refused"] == "fails_policy"
        r = c.post(url, json={}, headers=ngozi)
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert c.post(url, json={}, headers=ngozi).status_code == 409
        r = c.post(f"/collections/messages/{second}/reject", json={"reason": "Wrong tone."}, headers=ngozi)
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert c.post("/collections/messages/999999/reject", json={"reason": "x"}, headers=ngozi).status_code == 404
    with db.connect(schema_url) as check:
        actions = [a for (a,) in check.execute(
            "SELECT action FROM audit_log WHERE action LIKE 'message.%' ORDER BY id")]
    assert actions[-2:] == ["message.approved", "message.rejected"]
