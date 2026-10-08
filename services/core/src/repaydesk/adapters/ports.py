"""The contracts between Repaydesk and the systems around it.

Every outside system sits behind one of these. The demo uses Fineract, Odoo, files and a
simulator; a bank's own core banking system, ledger, bank APIs or SMS provider replace
them by implementing the same contract and passing the same contract tests
(tests/contract/). Nothing above this layer knows which one it is talking to.

Reads and writes are split on purpose. Agents get the read side only, through read-only
MCP tools. The write side is held by the posting gateway and, for setting up a demo, by
the seeder.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from repaydesk.domain import Borrower, Branch, Group, Loan, PaymentLine, Product


@dataclass(frozen=True)
class BorrowerMatch:
    borrower_ref: str
    name: str
    phone: str
    branch_code: str
    business_name: str | None
    loan_refs: tuple[str, ...]


@dataclass(frozen=True)
class DueInstallment:
    number: int
    due_on: date
    total: int
    paid: int

    @property
    def outstanding(self) -> int:
        return self.total - self.paid


@dataclass(frozen=True)
class LoanView:
    ref: str
    borrower_ref: str
    product: str
    principal: int
    disbursed_on: date
    status: str  # active | closed | overpaid
    outstanding: int
    arrears: int
    days_late: int
    installments: tuple[DueInstallment, ...]


@dataclass(frozen=True)
class PostedRepayment:
    loan_ref: str
    on: date
    amount: int
    reference: str


class AlreadyPosted(Exception):
    """A write with this idempotency key was already applied. Not an error for the caller."""


# ---- core banking ---------------------------------------------------------------------------


class CoreBankingReader(Protocol):
    def search_borrowers(self, text: str, limit: int = 10) -> list[BorrowerMatch]: ...

    def find_by_phone(self, phone: str) -> BorrowerMatch | None: ...

    def get_loan(self, loan_ref: str) -> LoanView | None: ...

    def list_repayments(self, loan_ref: str) -> list[PostedRepayment]: ...


class CoreBankingWriter(Protocol):
    def ensure_setup(self, branches: Iterable[Branch], products: Iterable[Product]) -> None: ...

    def create_borrower(self, borrower: Borrower) -> None: ...

    def create_group(self, group: Group) -> None: ...

    def create_loan(self, loan: Loan) -> None:
        """Submit, approve and disburse on the loan's own dates."""
        ...

    def post_repayment(self, loan_ref: str, on: date, amount: int, idempotency_key: str) -> None:
        """Raises AlreadyPosted if this key was posted before."""
        ...


class CoreBanking(CoreBankingReader, CoreBankingWriter, Protocol):
    pass


# ---- ledger ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    debit: int = 0
    credit: int = 0
    label: str = ""


@dataclass(frozen=True)
class JournalEntry:
    on: date
    reference: str  # idempotency key: one entry per reference, ever
    memo: str
    journal: str  # short code, e.g. RPY
    lines: tuple[JournalLine, ...]

    def balanced(self) -> bool:
        return sum(line.debit for line in self.lines) == sum(line.credit for line in self.lines)


class Ledger(Protocol):
    def ensure_setup(self, bank_name: str, collection_accounts: dict[str, tuple[str, str]]) -> None: ...

    def post_entry(self, entry: JournalEntry) -> str:
        """Returns the ledger's id for the entry. Raises AlreadyPosted for a repeated reference."""
        ...

    def balance(self, account_code: str) -> int: ...

    def find_entry(self, reference: str) -> str | None: ...


# ---- incoming payments ----------------------------------------------------------------------


class StatementSource(Protocol):
    """Where statement files come from: a folder in the demo, SFTP or a bank API in production."""

    def pending(self) -> list[Path]: ...

    def read(self, path: Path) -> Iterator[PaymentLine]: ...


class PaymentWebhooks(Protocol):
    def verify(self, body: bytes, signature: str) -> bool: ...

    def to_line(self, body: bytes) -> PaymentLine | None:
        """None for events that aren't incoming credits."""
        ...


# ---- outgoing messages ----------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundMessage:
    to: str
    channel: str  # sms | whatsapp
    body: str
    idempotency_key: str


class Messaging(Protocol):
    def send(self, message: OutboundMessage) -> str:
        """Returns the provider's message id. Raises AlreadyPosted for a repeated key."""
        ...
