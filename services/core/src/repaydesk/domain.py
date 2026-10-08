"""The few shapes every part of Repaydesk agrees on.

Money is always an integer number of kobo (1 naira = 100 kobo). Adapters convert at the
edge, because Fineract and Odoo both speak decimal naira.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


def naira(kobo: int) -> str:
    return f"₦{kobo / 100:,.2f}"


def to_kobo(amount: float | str) -> int:
    return round(float(amount) * 100)


class Channel(StrEnum):
    """How a repayment reached the bank. Each arrives in its own format."""

    BANK_CSV = "bank_csv"  # transfer into a collection account, CSV statement
    BANK_MT940 = "bank_mt940"  # transfer into a collection account, SWIFT MT940 statement
    VIRTUAL_ACCOUNT = "virtual_account"  # per-loan virtual account, provider webhook
    POS = "pos"  # card payment at an agent's POS terminal, daily settlement file
    BRANCH_CASH = "branch_cash"  # cash at a branch counter, teller's deposit sheet


@dataclass(frozen=True)
class Branch:
    code: str
    name: str
    state: str


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    installments: int
    monthly_flat_rate: float  # e.g. 0.035 = 3.5% of principal per month


@dataclass(frozen=True)
class Installment:
    number: int
    due_on: date
    principal: int
    interest: int

    @property
    def total(self) -> int:
        return self.principal + self.interest


@dataclass(frozen=True)
class Borrower:
    ref: str  # bank's customer reference, e.g. CUS-000123
    first_name: str
    last_name: str
    phone: str
    branch_code: str
    joined_on: date
    business_name: str | None = None
    group_ref: str | None = None
    behaviour: str = "steady"  # steady | late | struggling | defaulted (synthetic only)
    relatives: tuple[str, ...] = ()  # people who sometimes pay on the borrower's behalf

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class Group:
    ref: str
    name: str
    branch_code: str
    leader_ref: str
    member_refs: tuple[str, ...]


@dataclass(frozen=True)
class Loan:
    ref: str  # e.g. LN-2026-000123, also the external id in core banking
    borrower_ref: str
    product: str
    principal: int
    disbursed_on: date
    schedule: tuple[Installment, ...]
    virtual_account: str

    @property
    def total_due(self) -> int:
        return sum(i.total for i in self.schedule)


@dataclass(frozen=True)
class Repayment:
    """A repayment already posted in core banking before the statement week."""

    loan_ref: str
    paid_on: date
    amount: int
    channel: Channel
    reference: str


@dataclass(frozen=True)
class PaymentLine:
    """One incoming credit, as a source presents it. What Repaydesk has to match."""

    line_id: str
    channel: Channel
    source_account: str
    value_date: date
    amount: int
    narration: str
    bank_reference: str
    payer_name: str | None = None
    payer_account: str | None = None
    virtual_account: str | None = None
    terminal_id: str | None = None
    customer_reference: str | None = None  # what an agent or teller typed, if anything


@dataclass(frozen=True)
class Allocation:
    loan_ref: str
    amount: int


@dataclass(frozen=True)
class Truth:
    """What a line really is. Kept apart from the lines and never shown to agents: evals only."""

    line_id: str
    case: str  # exact_ref, virtual_account, name_only, business_name, third_party, group_leader, ...
    allocations: tuple[Allocation, ...] = ()
    resolvable: bool = True  # False: a careful person couldn't tell either; suspense is right
    note: str = ""


@dataclass
class Book:
    """Everything the generator produces for one seed."""

    bank_name: str
    as_of: date
    week_start: date
    branches: list[Branch]
    products: list[Product]
    borrowers: list[Borrower]
    groups: list[Group]
    loans: list[Loan]
    history: list[Repayment] = field(default_factory=list)
    lines: list[PaymentLine] = field(default_factory=list)
    truth: list[Truth] = field(default_factory=list)
    debits: list[PaymentLine] = field(default_factory=list)  # charges and sweeps: in statements, never matched
