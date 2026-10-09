"""The borrower directory and the borrower and loan pages: copied from core banking, searched
here, and a single record read live with what Repaydesk holds beside it."""

from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from repaydesk import approvals, directory, records, staff
from repaydesk.adapters.memory import MemoryCoreBanking
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.ingest import ingest_folder
from repaydesk.match import RuleMatcher, run
from repaydesk.staff import LIMITS, Staff
from repaydesk.synth.names import BRANCHES, PRODUCTS
from tests.conftest import WEBHOOK_SECRET

pytestmark = pytest.mark.db

PASSWORD = "test-staff-password"
AS_OF = date(2026, 10, 8)
BRANCH_NAMES = {b.code: b.name for b, _ in BRANCHES}
AMAKA = Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"])


def _core(book) -> MemoryCoreBanking:
    core = MemoryCoreBanking(AS_OF)
    core.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
    for b in book.borrowers:
        core.create_borrower(b)
    for g in book.groups:
        core.create_group(g)
    for loan in book.loans:
        core.create_loan(loan)
    for r in book.history:
        core.post_repayment(r.loan_ref, r.paid_on, r.amount, r.reference, channel=r.channel)
    return core


@pytest.fixture
def book(week_dir):
    return week_dir[1]


@pytest.fixture
def held(conn, week_dir, book):
    """The book copied, a week's files ingested, rules run: some lines held for loans."""
    data, _ = week_dir
    core = _core(book)
    staff.ensure_demo_staff(conn, PASSWORD)
    directory.sync(conn, core)
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO virtual_accounts (account_number, loan_ref, borrower_ref) VALUES (%s, %s, %s)",
                        [(x.virtual_account, x.ref, x.borrower_ref) for x in book.loans])
    return core


def _held_loan(conn) -> tuple[str, int, int]:
    """A loan with exactly one line held for it: (loan_ref, proposal_id, amount)."""
    return conn.execute(
        "SELECT a->>'loan_ref', p.id, (a->>'amount_kobo')::bigint FROM proposals p "
        "CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a "
        "WHERE p.status = 'pending' AND p.kind = 'match' AND jsonb_array_length(p.allocations) = 1 "
        "AND (SELECT count(*) FROM proposals q, jsonb_array_elements(q.allocations) b "
        "     WHERE q.status = 'pending' AND b->>'loan_ref' = a->>'loan_ref') = 1 "
        "ORDER BY p.id LIMIT 1").fetchone()


# ---- the sync --------------------------------------------------------------------------------------


def test_a_sync_copies_the_book_and_then_only_what_changed(conn, book):
    core = _core(book)
    first = directory.sync(conn, core)
    assert first == {"borrowers": len(book.borrowers), "loans": len(book.loans),
                     "changed": len(book.borrowers) + len(book.loans)}
    assert directory.sync(conn, core)["changed"] == 0

    loan = book.loans[0]
    before = core.get_loan(loan.ref).outstanding
    core.post_repayment(loan.ref, AS_OF, 10_000_00, "TEST-PAY-1")
    gone = book.borrowers[-1]
    del core.borrowers[gone.ref]
    core.loans = {k: v for k, v in core.loans.items() if v.borrower_ref != gone.ref}
    gone_loans = sum(1 for x in book.loans if x.borrower_ref == gone.ref)

    again = directory.sync(conn, core)
    assert again["changed"] == 1 + 1 + gone_loans  # the paid loan, the borrower removed, their loans removed
    assert conn.execute("SELECT outstanding_kobo FROM loans WHERE ref = %s", (loan.ref,)).fetchone()[0] \
        == before - 10_000_00
    assert conn.execute("SELECT count(*) FROM borrowers WHERE ref = %s", (gone.ref,)).fetchone()[0] == 0

    member = next(m for g in book.groups for m in g.member_refs if m != gone.ref)
    group = next(g for g in book.groups if member in g.member_refs)
    assert conn.execute("SELECT group_name, branch_name FROM borrowers WHERE ref = %s", (member,)).fetchone() \
        == (group.name, BRANCH_NAMES[next(x for x in book.borrowers if x.ref == member).branch_code])


def test_a_failed_sync_is_recorded_and_leaves_the_last_copy(conn, book):
    core = _core(book)
    directory.sync(conn, core)

    class Down:
        def list_borrowers(self):
            raise ConnectionError("core banking is down")

        def list_loans(self):
            return iter(())

    with pytest.raises(ConnectionError):
        directory.sync(conn, Down())
    last = directory.last_sync(conn)
    assert last["borrowers"] == len(book.borrowers) and last["failed_since"]
    assert conn.execute("SELECT count(*) FROM borrowers").fetchone()[0] == len(book.borrowers)

    directory.sync(conn, core)
    assert directory.last_sync(conn)["failed_since"] is None


def test_only_one_sync_runs_at_a_time(conn, schema_url, book):
    with psycopg.connect(schema_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (directory.LOCK,))
        assert directory.sync(conn, _core(book)) is None
    assert directory.sync(conn, _core(book)) is not None


# ---- borrowers and loans -----------------------------------------------------------------------------


def test_borrowers_are_found_by_name_phone_trading_name_or_loan(conn, held, book):
    b = next(x for x in book.borrowers if x.business_name)
    loan = next(x for x in book.loans if x.borrower_ref == b.ref)
    for q in (b.last_name.lower(), "+234" + b.phone[1:], b.business_name.upper(), loan.ref, b.ref):
        refs = [i["ref"] for i in records.borrowers(conn, q=q, limit=200)["items"]]
        assert b.ref in refs, q

    page = records.borrowers(conn, view="arrears", limit=200)
    assert page["counts"]["all"] == len(book.borrowers)
    assert page["total"] == page["counts"]["arrears"] > 0
    assert all(i["arrears_kobo"] > 0 for i in page["items"])
    held_page = records.borrowers(conn, view="held", limit=200)
    assert held_page["total"] > 0 and all(i["held"]["lines"] > 0 for i in held_page["items"])
    one = held_page["items"][0]
    assert type(one["arrears_kobo"]) is int and type(one["held"]["amount_kobo"]) is int  # kobo, never a decimal


def test_a_borrower_shows_live_balances_and_every_payment_posted_or_held(conn, held, book):
    loan_ref, _pid, amount = _held_loan(conn)
    borrower_ref = conn.execute("SELECT borrower_ref FROM loans WHERE ref = %s", (loan_ref,)).fetchone()[0]
    page = records.borrower(conn, borrower_ref, held)

    assert page["live"] is True
    posted = [p for p in page["payments"] if p["status"] == "posted"]
    assert len(posted) == sum(1 for r in book.history if r.loan_ref in {x["ref"] for x in page["loans"]})
    assert all(p["channel"] for p in posted)
    pending = [p for p in page["payments"] if p["status"] != "posted"]
    assert [(p["loan_ref"], p["amount_kobo"]) for p in pending] == [(loan_ref, amount)]
    assert pending[0]["line_id"]
    assert [p["on"] for p in page["payments"]] == sorted((p["on"] for p in page["payments"]), reverse=True)


def test_when_core_banking_does_not_answer_the_page_shows_the_directory_copy(conn, held):
    loan_ref, _pid, _amount = _held_loan(conn)

    class Down:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise ConnectionError("down")
            return fail

    page = records.loan(conn, loan_ref, Down())
    copy = conn.execute("SELECT outstanding_kobo FROM loans WHERE ref = %s", (loan_ref,)).fetchone()[0]
    assert page["live"] is False and page["schedule"] is None and page["outstanding_kobo"] == copy
    assert page["held_kobo"] > 0  # what Repaydesk holds doesn't depend on core banking
    assert records.loan(conn, loan_ref, None)["live"] is False


def test_a_loan_shows_its_schedule_and_what_posting_held_lines_would_leave(conn, held):
    loan_ref, pid, amount = _held_loan(conn)
    page = records.loan(conn, loan_ref, held)
    view = held.get_loan(loan_ref)
    assert page["outstanding_kobo"] == view.outstanding
    assert [(s["due_kobo"], s["paid_kobo"]) for s in page["schedule"]] == [(i.total, i.paid) for i in view.installments]
    assert page["held_kobo"] == amount
    assert page["money_in"][-1]["status"] == "proposed"
    assert page["virtual_account"]["number"]

    approvals.reject(conn, pid, AMAKA, "Wrong borrower")
    assert records.loan(conn, loan_ref, held)["held_kobo"] == 0  # a rejected proposal holds nothing


def test_loans_by_how_late(conn, held, book):
    page = records.loans(conn, limit=200)
    assert all(type(i["held_kobo"]) is int for i in page["items"])
    c = page["counts"]
    assert c["all"] == len(book.loans) == page["total"]
    assert c["all"] == c["current"] + sum(c[b] for b in records.LOAN_BANDS if b != "all")
    active = [i for i in page["items"] if i["status"] == "active"]
    assert [i["days_late"] for i in active] == sorted((i["days_late"] for i in active), reverse=True)
    for band, (lo, hi) in {"late_1_7": (1, 7), "late_8_30": (8, 30), "late_31_90": (31, 90)}.items():
        items = records.loans(conn, band=band, limit=200)["items"]
        assert all(lo <= i["days_late"] <= hi for i in items)
    assert records.loans(conn, q=book.loans[3].ref.replace("-", " ").lower())["items"][0]["ref"] == book.loans[3].ref


def test_the_search_box_opens_a_record_when_it_is_one(conn, held, book):
    loan = book.loans[5]
    b = book.borrowers[7]
    assert records.find(conn, loan.ref.lower().replace("-", "")) == {"to": "loan", "ref": loan.ref}
    assert records.find(conn, b.ref.lower()) == {"to": "borrower", "ref": b.ref}
    assert records.find(conn, f"+234 {b.phone[1:4]} {b.phone[4:]}") == {"to": "borrower", "ref": b.ref}
    assert records.find(conn, "LN-2026-999999") == {"to": "payments", "q": "LN-2026-999999"}
    assert records.find(conn, "SHEHU") == {"to": "payments", "q": "SHEHU"}


# ---- over HTTP -----------------------------------------------------------------------------------------


def test_borrower_and_loan_endpoints(conn, held, schema_url, monkeypatch, book):
    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("STAFF_DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("FINERACT_URL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from repaydesk.api import app

    with TestClient(app) as c:
        assert c.get("/loans").status_code == 401
        token = c.post("/auth/sign-in", json={"staff_id": "hadiza", "password": PASSWORD}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"

        # Without core banking configured, single records come from the directory and say so.
        loan = book.loans[0]
        assert c.get(f"/api/loans/{loan.ref}").json()["live"] is False
        app.state.core = held
        got = c.get(f"/api/loans/{loan.ref}").json()
        assert got["live"] is True and got["borrower_ref"] == loan.borrower_ref
        assert c.get(f"/borrowers/{loan.borrower_ref}").json()["loans"][0]["ref"]
        assert c.get("/loans", params={"band": "late_8_30"}).json()["items"] is not None
        assert c.get("/borrowers", params={"view": "held", "limit": 5}).json()["limit"] == 5
        assert c.get("/find", params={"q": loan.ref}).json() == {"to": "loan", "ref": loan.ref}
        assert c.get("/loans/LN-2026-999999").status_code == 404
        assert c.get("/borrowers/CUS-NOPE").status_code == 404
        assert c.get("/loans", params={"band": "nope"}).status_code == 422
        assert c.get("/borrowers", params={"view": "nope"}).status_code == 422

