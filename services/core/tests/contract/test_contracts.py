"""Contract tests: what any CoreBanking or Ledger adapter must do.

They run against the in-memory reference adapters always, and against Fineract and Odoo
when the stack is up:

    pytest -m integration        (after `make up`; reads FINERACT_URL and ODOO_URL)

A bank's own core banking or ledger adapter is ready when it passes these too.
"""

import os
import uuid
from datetime import date, timedelta

import pytest

from repaydesk.adapters.chart import (
    CHART,
    INTEREST_INCOME,
    PRINCIPAL,
    REPAYMENT_JOURNAL,
    SUSPENSE,
    collection_account_code,
)
from repaydesk.adapters.memory import MemoryCoreBanking, MemoryLedger
from repaydesk.adapters.ports import AlreadyPosted, JournalEntry, JournalLine
from repaydesk.domain import Borrower, Channel, Installment, Loan, bank_today
from repaydesk.synth.generator import add_months
from repaydesk.synth.names import BRANCHES, COLLECTION_ACCOUNTS, PRODUCTS

TODAY = bank_today()  # Fineract's date, not the runner's: they differ from 23:00 UTC


def _fineract():
    from repaydesk.adapters.fineract import FineractCoreBanking

    return FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                               os.getenv("FINERACT_USERNAME", "mifos"), os.getenv("FINERACT_PASSWORD", "password"))


def _odoo():
    from repaydesk.adapters.odoo import OdooLedger

    return OdooLedger(os.environ["ODOO_URL"], os.getenv("ODOO_DB", "ledger"),
                      os.getenv("ODOO_USERNAME", "admin"), os.getenv("ODOO_PASSWORD", "admin"))


core_impls = [pytest.param(lambda: MemoryCoreBanking(TODAY), id="memory"),
              pytest.param(_fineract, id="fineract", marks=pytest.mark.integration)]
ledger_impls = [pytest.param(MemoryLedger, id="memory"),
                pytest.param(_odoo, id="odoo", marks=pytest.mark.integration)]


@pytest.fixture(params=core_impls)
def core(request):
    c = request.param()
    c.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
    return c


@pytest.fixture(params=ledger_impls)
def ledger(request):
    lg = request.param()
    # The demo's own name: these run against the demo's Odoo, and setup renames the company.
    lg.ensure_setup(os.getenv("BANK_NAME", "Demo Microfinance Bank"), COLLECTION_ACCOUNTS)
    return lg


def _unique() -> str:
    return uuid.uuid4().hex[:8].upper()


def _borrower(tag: str) -> Borrower:
    digits = str(int(tag, 16) % 10**6).zfill(6)
    # Letters only: real surnames have no digits, and Fineract's name search is case-sensitive.
    surname = "Contract" + tag.translate(str.maketrans("0123456789", "ghijklmnop")).lower()
    return Borrower(ref=f"CUS-T{tag}", first_name="Ngozi", last_name=surname,
                    phone=f"07009{digits}", branch_code="IKJ", joined_on=TODAY - timedelta(days=400),
                    business_name=f"Ngozi {surname[8:].title()} Provisions")


def _loan(b: Borrower, tag: str, disbursed: date, n: int = 6) -> Loan:
    principal = 600_000_00
    schedule = tuple(Installment(i, add_months(disbursed, i), principal // n, 21_000_00) for i in range(1, n + 1))
    return Loan(f"LN-T-{tag}", b.ref, "SME", principal, disbursed, schedule, "99" + tag[:8])


# ---- core banking ---------------------------------------------------------------------------


def test_borrower_can_be_found_by_phone_and_name(core):
    tag = _unique()
    b = _borrower(tag)
    core.create_borrower(b)
    core.create_borrower(b)  # creating twice is harmless

    by_phone = core.find_by_phone(b.phone)
    assert by_phone and by_phone.borrower_ref == b.ref and by_phone.branch_code == "IKJ"
    assert by_phone.business_name == b.business_name

    assert core.get_borrower(b.ref) == by_phone
    assert core.get_borrower("CUS-NOPE") is None

    hits = core.search_borrowers(f"{b.last_name.upper()} {b.first_name.upper()}")
    assert hits and hits[0].borrower_ref == b.ref


def test_loan_schedule_and_balances(core):
    tag = _unique()
    b = _borrower(tag)
    core.create_borrower(b)
    loan = _loan(b, tag, TODAY.replace(day=min(TODAY.day, 28)) - timedelta(days=5))
    core.create_loan(loan)
    core.create_loan(loan)  # creating twice is harmless

    view = core.get_loan(loan.ref)
    assert view.status == "active" and view.borrower_ref == b.ref and view.product == "SME"
    assert view.outstanding == loan.total_due
    assert [i.total for i in view.installments] == [i.total for i in loan.schedule]
    assert view.arrears == 0 and view.days_late == 0
    assert loan.ref in core.find_by_phone(b.phone).loan_refs


def test_repayment_is_idempotent_and_reduces_the_balance(core):
    tag = _unique()
    b = _borrower(tag)
    core.create_borrower(b)
    loan = _loan(b, tag, TODAY.replace(day=min(TODAY.day, 28)) - timedelta(days=10))
    core.create_loan(loan)

    core.post_repayment(loan.ref, TODAY, 50_000_00, f"PAY-{tag}")
    with pytest.raises(AlreadyPosted):
        core.post_repayment(loan.ref, TODAY, 50_000_00, f"PAY-{tag}")

    assert core.get_loan(loan.ref).outstanding == loan.total_due - 50_000_00
    assert [(r.amount, r.reference) for r in core.list_repayments(loan.ref)] == [(50_000_00, f"PAY-{tag}")]


def test_missed_instalments_show_as_arrears(core):
    tag = _unique()
    b = _borrower(tag)
    core.create_borrower(b)
    disbursed = add_months(TODAY.replace(day=min(TODAY.day, 28)), -2) - timedelta(days=3)
    loan = _loan(b, tag, disbursed)
    core.create_loan(loan)

    view = core.get_loan(loan.ref)
    due = [i for i in loan.schedule if i.due_on < TODAY]
    assert view.arrears == sum(i.total for i in due)
    assert view.days_late == (TODAY - due[0].due_on).days


def test_the_whole_book_lists_for_the_directory(core):
    tag = _unique()
    b = _borrower(tag)
    core.create_borrower(b)
    disbursed = add_months(TODAY.replace(day=min(TODAY.day, 28)), -1) - timedelta(days=3)
    loan = _loan(b, tag, disbursed)
    core.create_loan(loan)
    core.post_repayment(loan.ref, TODAY, 30_000_00, f"PAY-{tag}", channel=Channel.POS)

    listed = next(x for x in core.list_borrowers() if x.ref == b.ref)
    assert (listed.name, listed.phone, listed.branch_code, listed.business_name, listed.active) == (
        b.full_name, b.phone, "IKJ", b.business_name, True)
    assert listed.branch_name == "Ikeja" and listed.joined_on == b.joined_on and listed.core_id

    summary = next(x for x in core.list_loans() if x.ref == loan.ref)
    view = core.get_loan(loan.ref)
    assert (summary.borrower_ref, summary.product, summary.principal, summary.disbursed_on, summary.status) == (
        b.ref, "SME", loan.principal, disbursed, "active")
    assert (summary.outstanding, summary.arrears, summary.days_late) == (view.outstanding, view.arrears, view.days_late)
    assert summary.arrears > 0 and summary.product_name == "SME working capital, 6 months"
    assert [r.channel for r in core.list_repayments(loan.ref)] == ["pos"]


def test_unknown_loan(core):
    assert core.get_loan("LN-NOPE-000000") is None
    with pytest.raises(KeyError):
        core.post_repayment("LN-NOPE-000000", TODAY, 100_00, f"PAY-{_unique()}")


# ---- ledger ---------------------------------------------------------------------------------


def _entry(ref: str, amount: int = 52_500_00) -> JournalEntry:
    return JournalEntry(
        on=TODAY, reference=ref, memo="contract test repayment", journal=REPAYMENT_JOURNAL,
        lines=(
            JournalLine(collection_account_code("bank_csv"), debit=amount),
            JournalLine(PRINCIPAL, credit=amount - 2_500_00),
            JournalLine(INTEREST_INCOME, credit=2_500_00),
        ),
    )


def test_entry_posts_once(ledger):
    ref = f"CT-{_unique()}"
    before = ledger.balance(PRINCIPAL)
    assert ledger.post_entry(_entry(ref))
    with pytest.raises(AlreadyPosted):
        ledger.post_entry(_entry(ref))
    assert ledger.find_entry(ref)
    assert ledger.balance(PRINCIPAL) == before - 50_000_00


def test_unbalanced_entry_is_refused(ledger):
    bad = JournalEntry(TODAY, f"CT-{_unique()}", "bad", REPAYMENT_JOURNAL,
                       (JournalLine(SUSPENSE, debit=100_00), JournalLine(PRINCIPAL, credit=99_00)))
    with pytest.raises(ValueError):
        ledger.post_entry(bad)
    assert ledger.find_entry(bad.reference) is None


def test_an_entry_reads_back_line_by_line(ledger):
    ref = f"CT-{_unique()}"
    ledger.post_entry(_entry(ref))
    got = ledger.read_entry(ref)
    assert got.name and got.state == "posted" and got.entry.on == TODAY
    assert sorted((x.account_code, x.debit, x.credit) for x in got.entry.lines) == sorted(
        (x.account_code, x.debit, x.credit) for x in _entry(ref).lines)
    assert ledger.read_entry(f"CT-{_unique()}") is None


@pytest.mark.integration
def test_odoo_read_login_reads_the_books_and_cannot_post():
    from repaydesk.adapters.odoo import OdooError, OdooLedger

    if not os.getenv("ODOO_READ_PASSWORD"):
        pytest.skip("ODOO_READ_PASSWORD not set")
    admin = _odoo()
    ref = f"CT-{_unique()}"
    admin.post_entry(_entry(ref))
    reader = OdooLedger(os.environ["ODOO_URL"], os.getenv("ODOO_DB", "ledger"),
                        os.getenv("ODOO_READ_USERNAME", "repaydesk-read"), os.environ["ODOO_READ_PASSWORD"])
    assert reader.read_entry(ref).name == admin.read_entry(ref).name
    assert reader.balance(PRINCIPAL) == admin.balance(PRINCIPAL)
    with pytest.raises(OdooError, match="not allowed"):
        reader.post_entry(_entry(f"CT-{_unique()}"))


@pytest.mark.integration
def test_odoo_names_every_account_as_repaydesk_means_it():
    """Odoo's generic chart already uses some of our codes (131000 is its "Tax Paid"). Setup must
    make each one Repaydesk's account, not post repayments to an account named for something else."""
    lg = _odoo()
    lg.ensure_setup(os.getenv("BANK_NAME", "Demo Microfinance Bank"), COLLECTION_ACCOUNTS)
    wanted = {code: (name, kind) for code, name, kind in CHART}
    wanted |= {collection_account_code(k): (f"Collections: {name}", "asset_cash")
               for k, (name, _n) in COLLECTION_ACCOUNTS.items()}
    have = {a["code"]: (a["name"], a["account_type"]) for a in lg._x(
        "account.account", "search_read", [["code", "in", list(wanted)]], fields=["code", "name", "account_type"])}
    assert have == wanted
