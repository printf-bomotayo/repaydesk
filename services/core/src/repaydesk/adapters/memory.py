"""In-memory adapters: the reference behaviour for the contract tests, and fast fakes for unit tests.

A real adapter is correct when it passes the same contract tests these pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date

from repaydesk.adapters.ports import (
    AlreadyPosted,
    BorrowerMatch,
    DirectoryBorrower,
    DueInstallment,
    JournalEntry,
    LedgerEntry,
    LoanSummary,
    LoanView,
    OutboundMessage,
    PostedRepayment,
)
from repaydesk.domain import Borrower, Branch, Channel, Group, Loan, Product, bank_today


def normalise(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def apply_payments(loan: Loan, paid_total: int, today: date) -> tuple[tuple[DueInstallment, ...], int, int]:
    """Spread what was paid over instalments, oldest first. Returns (instalments, arrears, days late)."""
    left = paid_total
    rows = []
    for inst in loan.schedule:
        paid = min(left, inst.total)
        left -= paid
        rows.append(DueInstallment(inst.number, inst.due_on, inst.total, paid))
    late = [r for r in rows if r.due_on < today and r.outstanding > 0]
    arrears = sum(r.outstanding for r in late)
    days = (today - late[0].due_on).days if late else 0
    return tuple(rows), arrears, days


class MemoryCoreBanking:
    def __init__(self, today: date | None = None):
        self.today = today or bank_today()
        self.borrowers: dict[str, Borrower] = {}
        self.groups: dict[str, Group] = {}
        self.loans: dict[str, Loan] = {}
        self.repayments: dict[str, list[PostedRepayment]] = {}
        self._keys: set[str] = set()
        self.branches: list[Branch] = []
        self.products: list[Product] = []

    # writer
    def ensure_setup(self, branches: Iterable[Branch], products: Iterable[Product]) -> None:
        self.branches = list(branches)
        self.products = list(products)

    def create_borrower(self, borrower: Borrower) -> None:
        self.borrowers.setdefault(borrower.ref, borrower)

    def create_group(self, group: Group) -> None:
        self.groups.setdefault(group.ref, group)

    def create_loan(self, loan: Loan) -> None:
        if loan.borrower_ref not in self.borrowers:
            raise KeyError(f"unknown borrower {loan.borrower_ref}")
        self.loans.setdefault(loan.ref, loan)
        self.repayments.setdefault(loan.ref, [])

    def post_repayment(self, loan_ref: str, on: date, amount: int, idempotency_key: str,
                       channel: Channel | None = None) -> None:
        if idempotency_key in self._keys:
            raise AlreadyPosted(idempotency_key)
        if loan_ref not in self.loans:
            raise KeyError(f"unknown loan {loan_ref}")
        if amount <= 0:
            raise ValueError("amount must be positive")
        self._keys.add(idempotency_key)
        self.repayments[loan_ref].append(
            PostedRepayment(loan_ref, on, amount, idempotency_key, channel.value if channel else None))

    # reader
    def _match(self, b: Borrower) -> BorrowerMatch:
        loans = tuple(ref for ref, loan in self.loans.items() if loan.borrower_ref == b.ref)
        return BorrowerMatch(b.ref, b.full_name, b.phone, b.branch_code, b.business_name, loans)

    def search_borrowers(self, text: str, limit: int = 10) -> list[BorrowerMatch]:
        words = normalise(text).split()
        if not words:
            return []
        scored = []
        for b in self.borrowers.values():
            hay = normalise(f"{b.full_name} {b.business_name or ''}").split()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, b.ref))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self._match(self.borrowers[ref]) for _, ref in scored[:limit]]

    def get_borrower(self, borrower_ref: str) -> BorrowerMatch | None:
        b = self.borrowers.get(borrower_ref)
        return self._match(b) if b else None

    def find_by_phone(self, phone: str) -> BorrowerMatch | None:
        digits = "".join(c for c in phone if c.isdigit())
        for b in self.borrowers.values():
            if b.phone == digits:
                return self._match(b)
        return None

    def get_loan(self, loan_ref: str) -> LoanView | None:
        loan = self.loans.get(loan_ref)
        if not loan:
            return None
        paid = sum(r.amount for r in self.repayments[loan_ref])
        rows, arrears, days = apply_payments(loan, paid, self.today)
        outstanding = loan.total_due - paid
        status = "active" if outstanding > 0 else ("closed" if outstanding == 0 else "overpaid")
        return LoanView(
            loan.ref, loan.borrower_ref, loan.product, loan.principal, loan.disbursed_on,
            status, max(outstanding, 0), arrears, days, rows,
        )

    def list_repayments(self, loan_ref: str) -> list[PostedRepayment]:
        return sorted(self.repayments.get(loan_ref, []), key=lambda r: r.on)

    # directory
    def list_borrowers(self) -> Iterator[DirectoryBorrower]:
        branch_names = {b.code: b.name for b in self.branches}
        member_of = {ref: g for g in self.groups.values() for ref in g.member_refs}
        for b in self.borrowers.values():
            g = member_of.get(b.ref)
            yield DirectoryBorrower(b.ref, b.ref, b.full_name, b.phone, b.branch_code, branch_names.get(b.branch_code),
                                    b.business_name, g.ref if g else None, g.name if g else None, b.joined_on, True)

    def list_loans(self) -> Iterator[LoanSummary]:
        product_names = {p.code: p.name for p in self.products}
        for ref, loan in self.loans.items():
            v = self.get_loan(ref)
            yield LoanSummary(ref, ref, loan.borrower_ref, loan.product, product_names.get(loan.product, loan.product),
                              loan.principal, loan.disbursed_on, v.status, v.outstanding, v.arrears, v.days_late)


class MemoryLedger:
    def __init__(self):
        self.accounts: dict[str, str] = {}
        self.entries: dict[str, JournalEntry] = {}

    def ensure_setup(self, bank_name: str, collection_accounts: dict[str, tuple[str, str]]) -> None:
        from repaydesk.adapters.chart import CHART, collection_account_code

        self.accounts.update({code: name for code, name, _ in CHART})
        for key, (name, _number) in collection_accounts.items():
            self.accounts[collection_account_code(key)] = name

    def post_entry(self, entry: JournalEntry) -> str:
        if entry.reference in self.entries:
            raise AlreadyPosted(entry.reference)
        if not entry.balanced():
            raise ValueError(f"unbalanced entry {entry.reference}")
        for line in entry.lines:
            if line.account_code not in self.accounts:
                raise KeyError(f"unknown account {line.account_code}")
        self.entries[entry.reference] = entry
        return entry.reference

    def balance(self, account_code: str) -> int:
        return sum(
            line.debit - line.credit
            for e in self.entries.values()
            for line in e.lines
            if line.account_code == account_code
        )

    def find_entry(self, reference: str) -> str | None:
        return reference if reference in self.entries else None

    def read_entry(self, reference: str) -> LedgerEntry | None:
        entry = self.entries.get(reference)
        return LedgerEntry(reference, "posted", entry) if entry else None


class MemoryMessaging:
    def __init__(self):
        self.sent: dict[str, OutboundMessage] = {}

    def send(self, message: OutboundMessage) -> str:
        if message.idempotency_key in self.sent:
            raise AlreadyPosted(message.idempotency_key)
        self.sent[message.idempotency_key] = message
        return f"sim-{len(self.sent):06d}"
