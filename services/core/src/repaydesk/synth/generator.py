"""Synthetic loan book and a week of incoming repayments, messy the way real ones are.

Same seed, same book: every random choice goes through one `random.Random`. Nothing here is
real customer data. Phone numbers use the 07005 prefix and are never dialled: messaging is
simulated.

The output has three parts that stay separate:
- the book (borrowers, groups, loans, posted history) that gets loaded into Fineract;
- the payment lines for the statement week, as each source would present them;
- the truth for each line, used only by evals and never shown to an agent.
"""

from __future__ import annotations

import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta

from repaydesk.domain import (
    Allocation,
    Book,
    Borrower,
    Branch,
    Channel,
    Group,
    Installment,
    Loan,
    PaymentLine,
    Repayment,
    Truth,
    bank_today,
)
from repaydesk.synth import names

BEHAVIOURS = [("steady", 0.70), ("late", 0.15), ("struggling", 0.10), ("defaulted", 0.05)]
CHANNELS = [
    (Channel.VIRTUAL_ACCOUNT, 0.40),
    (Channel.BANK_CSV, 0.20),
    (Channel.BANK_MT940, 0.15),
    (Channel.POS, 0.10),
    (Channel.BRANCH_CASH, 0.15),
]
# How a bank transfer's narration identifies (or fails to identify) the loan.
TRANSFER_CASES = [
    ("exact_ref", 0.30),
    ("ref_typo", 0.05),
    ("name_only", 0.25),
    ("business_name", 0.15),
    ("third_party", 0.10),
    ("payer_only", 0.08),
    ("pidgin", 0.04),
    ("unresolvable", 0.03),
]


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def round_to(kobo: int, naira_step: int) -> int:
    step = naira_step * 100
    return max(step, round(kobo / step) * step)


@dataclass
class _Payment:
    """A payment the borrower actually made, before we decide how it shows up."""

    loan: Loan
    on: date
    amount: int
    borrower: Borrower


class Generator:
    def __init__(self, seed: int = 42, as_of: date | None = None, bank_name: str = "Demo Microfinance Bank"):
        self.r = random.Random(seed)
        self.as_of = as_of or bank_today()
        self.week_start = self.as_of - timedelta(days=7)
        self.bank_name = bank_name
        self._counters: dict[str, int] = defaultdict(int)
        self._phones: set[str] = set()

    # ---- helpers -------------------------------------------------------------------------------

    def _pick(self, weighted):
        items, weights = zip(*weighted, strict=True)
        return self.r.choices(items, weights=weights)[0]

    def _next(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]

    def _phone(self) -> str:
        while True:
            p = "07005" + "".join(self.r.choice("0123456789") for _ in range(6))
            if p not in self._phones:
                self._phones.add(p)
                return p

    def _name(self, region: str) -> tuple[str, str]:
        mix = names.BRANCH_MIX[region]
        ethnicity = self._pick(list(mix.items()))
        return self.r.choice(names.FIRST_NAMES[ethnicity]), self.r.choice(names.LAST_NAMES[ethnicity])

    def _date_between(self, start: date, end: date) -> date:
        return start + timedelta(days=self.r.randint(0, max(0, (end - start).days)))

    # ---- the book ------------------------------------------------------------------------------

    def generate(self, borrowers: int = 2000) -> Book:
        branches = [b for b, _ in names.BRANCHES]
        regions = {b.code: region for b, region in names.BRANCHES}
        book = Book(
            bank_name=self.bank_name,
            as_of=self.as_of,
            week_start=self.week_start,
            branches=branches,
            products=list(names.PRODUCTS),
            borrowers=[],
            groups=[],
            loans=[],
        )

        group_sizes = [self.r.randint(5, 8) for _ in range(max(1, borrowers // 60))]
        individuals = borrowers - sum(group_sizes)

        for _ in range(individuals):
            branch = self.r.choice(branches)
            book.borrowers.append(self._borrower(branch, regions[branch.code]))

        for size in group_sizes:
            self._group(book, self.r.choice(branches), regions, size)

        for b in book.borrowers:
            if b.group_ref:
                continue
            product = "QUICK" if self.r.random() < 0.45 else "SME"
            book.loans.append(self._loan(b, product))
            if product == "SME" and self.r.random() < 0.12:  # some SME borrowers also take a quick loan
                book.loans.append(self._loan(b, "QUICK"))

        payments = self._payments(book)
        self._present(book, payments)
        self._not_repayments(book)
        book.lines.sort(key=lambda line: (line.value_date, line.channel, line.line_id))
        return book

    def _borrower(self, branch: Branch, region: str, group_ref: str | None = None) -> Borrower:
        first, last = self._name(region)
        business = None
        if self.r.random() < 0.55:
            business = self.r.choice(names.BUSINESS_PATTERNS).format(first=first)
        relatives = []
        for _ in range(self.r.choice([0, 1, 1, 2])):
            rel_first, rel_last = self._name(region)
            relatives.append(f"{rel_first} {last if self.r.random() < 0.7 else rel_last}")
        n = self._next("borrower")
        return Borrower(
            ref=f"CUS-{n:06d}",
            first_name=first,
            last_name=last,
            phone=self._phone(),
            branch_code=branch.code,
            joined_on=self.as_of - timedelta(days=self.r.randint(200, 1500)),
            business_name=business,
            group_ref=group_ref,
            behaviour=self._pick(BEHAVIOURS),
            relatives=tuple(relatives),
        )

    def _group(self, book: Book, branch: Branch, regions: dict[str, str], size: int) -> None:
        n = self._next("group")
        ref = f"GRP-{n:04d}"
        # Core banking wants unique group names; real ones are told apart by branch and cycle.
        base = names.GROUP_NAMES[(n - 1) % len(names.GROUP_NAMES)]
        cycle = (n - 1) // len(names.GROUP_NAMES) + 1
        name = f"{base} {branch.name}" if cycle == 1 else f"{base} {branch.name} {cycle}"
        members = [self._borrower(branch, regions[branch.code], ref) for _ in range(size)]
        book.borrowers.extend(members)
        book.groups.append(Group(ref, name, branch.code, members[0].ref, tuple(m.ref for m in members)))
        # A group borrows together: same product, same day, so their instalments fall due together.
        disbursed = self._disbursement("SME")
        for m in members:
            principal = self.r.randint(10, 50) * 6000  # ₦60,000 to ₦300,000
            book.loans.append(self._loan(m, "SME", principal=principal * 100, disbursed=disbursed))

    def _disbursement(self, product: str, behaviour: str = "steady") -> date:
        if product == "QUICK":
            if behaviour == "defaulted":
                return self._date_between(self.as_of - timedelta(days=95), self.as_of - timedelta(days=40))
            start, end = self.as_of - timedelta(days=45), self.as_of - timedelta(days=3)
        else:
            start, end = self.as_of - timedelta(days=185), self.as_of - timedelta(days=10)
        d = self._date_between(start, end)
        return d.replace(day=min(d.day, 28))  # keeps every due date on the same day of the month

    def _loan(self, b: Borrower, product_code: str, principal: int | None = None,
              disbursed: date | None = None) -> Loan:
        product = next(p for p in names.PRODUCTS if p.code == product_code)
        if principal is None:
            if product_code == "QUICK":
                principal = self.r.randint(20, 150) * 1000 * 100  # ₦20,000 to ₦150,000
            else:
                principal = self.r.choice([50, 75, 100, 150, 200, 300, 500, 800]) * 6000 * 100  # ₦300k to ₦4.8m
        disbursed = disbursed or self._disbursement(product_code, b.behaviour)
        n = product.installments
        per_principal = principal // n
        interest = round(principal * product.monthly_flat_rate)
        schedule = tuple(
            Installment(
                number=i,
                due_on=add_months(disbursed, i),
                principal=per_principal if i < n else principal - per_principal * (n - 1),
                interest=interest,
            )
            for i in range(1, n + 1)
        )
        seq = self._next("loan")
        return Loan(
            ref=f"LN-{disbursed.year}-{seq:06d}",
            borrower_ref=b.ref,
            product=product_code,
            principal=principal,
            disbursed_on=disbursed,
            schedule=schedule,
            virtual_account="99" + f"{self.r.randint(0, 99_999_999):08d}",
        )

    # ---- who paid what, when -------------------------------------------------------------------

    def _payments(self, book: Book) -> list[_Payment]:
        """Walk every instalment due so far. Payments before the week become posted history;
        payments inside the week become this week's lines."""
        by_ref = {b.ref: b for b in book.borrowers}
        week: list[_Payment] = []
        for loan in book.loans:
            b = by_ref[loan.borrower_ref]
            stop_after = self.r.randint(0, 1) if b.behaviour == "defaulted" else None
            for inst in loan.schedule:
                if inst.due_on > self.as_of + timedelta(days=3):
                    break
                if stop_after is not None and inst.number > stop_after:
                    break
                for on, amount in self._pay(b.behaviour, inst):
                    if on >= self.as_of:
                        continue
                    if on < self.week_start:
                        channel = self._pick(CHANNELS)
                        book.history.append(
                            Repayment(loan.ref, on, amount, channel, f"HIST-{self._next('hist'):07d}")
                        )
                    else:
                        week.append(_Payment(loan, on, amount, b))
        return week

    def _pay(self, behaviour: str, inst: Installment) -> list[tuple[date, int]]:
        due, amount = inst.due_on, inst.total
        r = self.r
        if behaviour == "steady":
            if r.random() < 0.05:  # rounds up: an overpayment
                return [(due + timedelta(days=r.randint(-3, 1)), round_to(amount + 50_000, 1000))]
            return [(due + timedelta(days=r.randint(-3, 1)), amount)]
        if behaviour == "late":
            on = due + timedelta(days=r.randint(3, 20))
            if r.random() < 0.25:
                part = round_to(amount * r.uniform(0.6, 0.9), 500)
                return [(on, part), (on + timedelta(days=r.randint(4, 10)), amount - part)]
            return [(on, amount)]
        if behaviour == "struggling":
            if r.random() < 0.4:
                return []
            return [(due + timedelta(days=r.randint(0, 14)), round_to(amount * r.uniform(0.4, 0.8), 500))]
        return [(due + timedelta(days=r.randint(0, 5)), amount)]  # a defaulter's early instalments

    # ---- how each payment shows up -------------------------------------------------------------

    def _present(self, book: Book, payments: list[_Payment]) -> None:
        by_ref = {b.ref: b for b in book.borrowers}
        groups = {g.ref: g for g in book.groups}
        remaining = list(payments)

        # A group leader often collects the members' instalments and sends one transfer.
        by_group: dict[str, list[_Payment]] = defaultdict(list)
        for p in remaining:
            if p.borrower.group_ref:
                by_group[p.borrower.group_ref].append(p)
        for group_ref, members in by_group.items():
            if len(members) < 2 or self.r.random() > 0.6:
                continue
            g = groups[group_ref]
            leader = by_ref[g.leader_ref]
            on = max(p.on for p in members)
            narration = self.r.choice(
                [f"{g.name.upper()} WK CONTRIB", f"GRP RPYMT {g.name.upper()}", f"{g.name} group loan"]
            )
            line = self._transfer_line(on, sum(p.amount for p in members), leader.full_name.upper(), narration)
            self._add(book, line, "group_leader", [Allocation(p.loan.ref, p.amount) for p in members])
            for p in members:
                remaining.remove(p)

        # A borrower with two loans sometimes pays both in one transfer.
        by_borrower: dict[str, list[_Payment]] = defaultdict(list)
        for p in remaining:
            by_borrower[p.borrower.ref].append(p)
        for _ref, ps in by_borrower.items():
            if len({p.loan.ref for p in ps}) < 2 or self.r.random() > 0.7:
                continue
            b = ps[0].borrower
            line = self._transfer_line(
                max(p.on for p in ps), sum(p.amount for p in ps), b.full_name.upper(), "LOAN REPAYMENTS"
            )
            self._add(book, line, "split_across_loans", [Allocation(p.loan.ref, p.amount) for p in ps])
            for p in ps:
                remaining.remove(p)

        for p in remaining:
            channel = self._pick(CHANNELS)
            if channel is Channel.VIRTUAL_ACCOUNT:
                self._virtual_account(book, p)
            elif channel is Channel.POS:
                self._pos(book, p)
            elif channel is Channel.BRANCH_CASH:
                self._branch_cash(book, p, by_ref)
            else:
                self._bank_transfer(book, p, channel)

    def _add(self, book: Book, line: PaymentLine, case: str, allocations, resolvable=True, note="") -> None:
        book.lines.append(line)
        book.truth.append(Truth(line.line_id, case, tuple(allocations), resolvable, note))

    def _line_id(self) -> str:
        return f"PL-{self._next('line'):06d}"

    def _transfer_line(self, on: date, amount: int, payer: str, text: str, channel: Channel | None = None):
        channel = channel or self.r.choice([Channel.BANK_CSV, Channel.BANK_MT940])
        bank = self.r.choice(names.SENDER_BANKS)
        if channel is Channel.BANK_CSV:
            narration = f"NIP/{bank}/{payer}/{text}"
            ref = f"NGT{self.r.randint(10**11, 10**12 - 1)}"
        else:
            narration = f"TRF FROM {payer}/{text}"
            ref = f"CST{self.r.randint(10**9, 10**10 - 1)}"
        return PaymentLine(
            line_id=self._line_id(),
            channel=channel,
            source_account=names.COLLECTION_ACCOUNTS[channel.value][1],
            value_date=on,
            amount=amount,
            narration=narration,
            bank_reference=ref,
            payer_name=payer,
            payer_account=f"{self.r.randint(10**9, 10**10 - 1)}",
        )

    def _name_variant(self, b: Borrower) -> str:
        first, last = b.first_name.upper(), b.last_name.upper()
        options = [
            f"{first} {last}",
            f"{last} {first}",
            f"{first[0]}. {last}",
            f"{last} {first[0]}",
            f"{first} {last[:-1]}",  # truncated by the sending bank
            f"{first} {self.r.choice(['A', 'O', 'C', 'I'])}. {last}",  # a middle initial we don't hold
        ]
        return self.r.choice(options)

    def _ref_variant(self, ref: str) -> str:
        return self.r.choice([ref, ref.replace("-", ""), ref.replace("-", " "), ref.lower(), ref[3:]])

    def _typo(self, ref: str) -> str:
        digits = [i for i, c in enumerate(ref) if c.isdigit()][-6:]
        i = self.r.choice(digits)
        if self.r.random() < 0.5:
            return ref[:i] + ref[i + 1 :]  # a digit dropped
        j = i + 1 if i + 1 in digits else i - 1
        chars = list(ref)
        chars[i], chars[j] = chars[j], chars[i]  # two digits swapped
        return "".join(chars)

    def _bank_transfer(self, book: Book, p: _Payment, channel: Channel) -> None:
        b, loan = p.borrower, p.loan
        case = self._pick(TRANSFER_CASES)
        if case == "business_name" and not b.business_name:
            case = "name_only"
        if case == "third_party" and not b.relatives:
            case = "name_only"
        resolvable, note = True, ""
        payer = self._name_variant(b)
        r = self.r
        if case == "exact_ref":
            text = r.choice(["{ref} RPYMT", "REPAYMENT {ref}", "{ref}", "LOAN {ref}"]).format(
                ref=self._ref_variant(loan.ref)
            )
        elif case == "ref_typo":
            typo = self._typo(loan.ref)
            text, note = f"RPY {typo}", f"reference typed as {typo}"
        elif case == "name_only":
            text = r.choice(["LOAN RPY", "loan repayment", "REPAYMNT", "MONTHLY LOAN", "LN RPY"])
        elif case == "business_name":
            payer = b.business_name.upper()
            text = r.choice(["LN RPY", "LOAN", "REPAYMENT", ""])
        elif case == "third_party":
            payer = r.choice(b.relatives).upper()
            text = r.choice(
                [f"LOAN FOR {b.first_name.upper()}", "for mummy loan", f"repayment for {b.first_name}", "LOAN RPY"]
            )
        elif case == "payer_only":
            text = ""
        elif case == "pidgin":
            text = r.choice(["my loan money", "balance for loan abeg", "loan money wey remain", "dis na my loan pay"])
        else:  # unresolvable: nothing ties it to anyone
            payer = r.choice(["POS TRF", "MOBILE TRF", f"0803****{r.randint(1000, 9999)}", "CUSTOMER"])
            text = r.choice(["PAYMENT", "TRF", ""])
            resolvable, note = False, "no name, reference or account a person could trace"
        line = self._transfer_line(p.on, p.amount, payer, text, channel)
        self._add(book, line, case, [Allocation(loan.ref, p.amount)], resolvable, note)

    def _virtual_account(self, book: Book, p: _Payment) -> None:
        payer = self._name_variant(p.borrower) if self.r.random() < 0.8 else self.r.choice(
            p.borrower.relatives or (p.borrower.full_name,)
        ).upper()
        event_id = f"evt_{self.r.getrandbits(64):016x}"
        line = PaymentLine(
            line_id=self._line_id(),
            channel=Channel.VIRTUAL_ACCOUNT,
            source_account=names.COLLECTION_ACCOUNTS["virtual_account"][1],
            value_date=p.on,
            amount=p.amount,
            narration=f"Transfer from {payer}",
            bank_reference=event_id,
            payer_name=payer,
            payer_account=f"{self.r.randint(10**9, 10**10 - 1)}",
            virtual_account=p.loan.virtual_account,
        )
        self._add(book, line, "virtual_account", [Allocation(p.loan.ref, p.amount)])
        if self.r.random() < 0.03:  # the provider redelivers the same event
            dup = replace(line, line_id=self._line_id())
            self._add(book, dup, "duplicate_delivery", [], note=f"same event as {line.line_id}")

    def _pos(self, book: Book, p: _Payment) -> None:
        b = p.borrower
        roll = self.r.random()
        if roll < 0.5:
            customer_ref, case, resolvable = b.phone, "pos_phone", True
        elif roll < 0.8:
            customer_ref, case, resolvable = self._ref_variant(p.loan.ref), "pos_ref", True
        else:
            customer_ref, case, resolvable = None, "pos_blank", False
        terminal = f"2TP{b.branch_code}{self.r.randint(1, 9):02d}"
        line = PaymentLine(
            line_id=self._line_id(),
            channel=Channel.POS,
            source_account=names.COLLECTION_ACCOUNTS["pos"][1],
            value_date=p.on,
            amount=p.amount,
            narration=f"POS PURCHASE {terminal} {b.branch_code} AGENT",
            bank_reference=f"{self.r.randint(10**11, 10**12 - 1)}",
            terminal_id=terminal,
            customer_reference=customer_ref,
        )
        note = "" if resolvable else "agent left the reference blank"
        self._add(book, line, case, [Allocation(p.loan.ref, p.amount)], resolvable, note)

    def _branch_cash(self, book: Book, p: _Payment, by_ref: dict[str, Borrower]) -> None:
        b = p.borrower
        roll = self.r.random()
        if roll < 0.45:
            ref, case = self._ref_variant(p.loan.ref), "cash_ref"
        elif roll < 0.55:
            ref, case = self._typo(p.loan.ref), "cash_ref_typo"
        else:
            ref, case = None, "cash_name"
        depositor = self._name_variant(b).title() if self.r.random() < 0.85 else self.r.choice(
            b.relatives or (b.full_name,)
        )
        line = PaymentLine(
            line_id=self._line_id(),
            channel=Channel.BRANCH_CASH,
            source_account=names.COLLECTION_ACCOUNTS["branch_cash"][1],
            value_date=p.on,
            amount=p.amount,
            narration=f"Cash deposit {b.branch_code} by {depositor}",
            bank_reference=f"RCP-{b.branch_code}-{self._next('receipt:' + b.branch_code):05d}",
            payer_name=depositor,
            customer_reference=ref,
        )
        self._add(book, line, case, [Allocation(p.loan.ref, p.amount)])

    def _not_repayments(self, book: Book) -> None:
        """Credits that land in collection accounts but aren't repayments, plus statement debits."""
        days = [self.week_start + timedelta(days=i) for i in range(7)]
        credits = [
            ("REVERSAL NIP/FAILED TRF/{n}", (500_000, 5_000_000)),
            ("INTEREST CREDIT", (1_000, 50_000)),
            ("TRF FROM HQ OPERATIONS FLOAT", (50_000_000, 200_000_000)),
            ("CHARGE REVERSAL", (5_000, 20_000)),
        ]
        for _ in range(max(3, len(book.lines) // 40)):
            text, (lo, hi) = self.r.choice(credits)
            line = self._transfer_line(
                self.r.choice(days), round_to(self.r.randint(lo, hi), 1), "", text.format(n=self.r.randint(1000, 9999))
            )
            line = replace(line, narration=text.format(n=self.r.randint(1000, 9999)), payer_name=None)
            self._add(book, line, "not_repayment", [], note="not a loan repayment")
        for _ in range(max(2, len(book.lines) // 60)):
            text = self.r.choice(["SMS ALERT CHARGES", "COT", "SWEEP TO HQ SETTLEMENT", "STAMP DUTY"])
            amount = 5_000 if text != "SWEEP TO HQ SETTLEMENT" else round_to(self.r.randint(10**8, 5 * 10**8), 1000)
            line = self._transfer_line(self.r.choice(days), amount, "", text)
            book.debits.append(replace(line, narration=text, payer_name=None, line_id=f"DR-{self._next('dr'):05d}"))
