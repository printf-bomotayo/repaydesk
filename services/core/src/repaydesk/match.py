"""Rule matching: propose only what is certain. Everything else waits for the investigator agent.

A rule here must never be wrong, because a wrong match puts one borrower's money on another's
loan, and the first borrower is then chased for a payment they made. So the rules are narrow:

- `virtual_account` — the payment arrived in a loan's own virtual account. One account, one loan.
- `loan_reference`  — the line quotes a loan reference that exists and is active, the payer's or
                      depositor's name includes the borrower's surname, AND one more independent
                      thing agrees: an amount the loan has due, or the same branch. A line from a
                      known branch other than the borrower's is never matched by rule.
                      A reference alone isn't enough: loan numbers are dense, so one mistyped digit
                      names someone else's loan. A reference plus a matching amount isn't either:
                      instalments are standard sizes, so a stranger's loan often has the same
                      amount due (that coincidence put a cash deposit on the wrong loan in
                      testing). A shared first name isn't either: "FRIDAY IKPE" once matched
                      Friday Briggs's loan that way. Nor is a full name: two borrowers called
                      Yetunde Salami, at different branches, met through a mistyped cash slip.
                      A POS line has no name at all, so there the branch and an amount due must
                      both agree.
- `pos_phone`       — the agent typed the borrower's phone, and that borrower has exactly one
                      active loan.
- `not_repayment`   — the narration is a known kind of credit that isn't a repayment (a reversal,
                      the bank's own interest, a float top-up). Proposed for exclusion, still
                      approved by a person.

    python -m repaydesk.match        # propose for every new line
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg
from opentelemetry import trace
from psycopg.types.json import Jsonb

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingReader, LoanView
from repaydesk.domain import Channel, PaymentLine

tracer = trace.get_tracer("repaydesk.match")

# LN-2026-000123, LN2026000123, ln 2026 000123, or just 2026-000123
LOAN_REF = re.compile(r"(?<![0-9A-Z])(?:LN)?[\s\-]*(20\d{2})[\s\-]*(\d{6})(?![0-9])", re.IGNORECASE)
PHONE = re.compile(r"^(?:\+?234|0)(\d{10})$")

# Credits that land in collection accounts but are not repayments. A bank's own list goes here.
NOT_REPAYMENT = [
    (re.compile(r"^REVERSAL\b", re.I), "a reversal of a failed transfer"),
    (re.compile(r"^INTEREST CREDIT\b", re.I), "the bank's interest on the collection account"),
    (re.compile(r"^CHARGE REVERSAL\b", re.I), "a reversed bank charge"),
    (re.compile(r"\bHQ OPERATIONS FLOAT\b", re.I), "an internal float top-up"),
]

NAME_NOISE = {"MR", "MRS", "MISS", "ALHAJI", "HAJIYA", "MAMA", "IYA", "LOAN", "TRF", "FROM", "AND", "THE"}


@dataclass
class Proposal:
    line_id: str
    kind: str  # match | exclude
    rule: str
    allocations: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    confidence: float = 1.0


def loan_refs_in(*texts: str | None) -> list[str]:
    refs = []
    for text in texts:
        for year, number in LOAN_REF.findall(text or ""):
            ref = f"LN-{year}-{number}"
            if ref not in refs:
                refs.append(ref)
    return refs


def name_tokens(text: str | None) -> set[str]:
    words = re.sub(r"[^A-Za-z]+", " ", text or "").upper().split()
    return {w for w in words if len(w) > 2 and w not in NAME_NOISE}


def line_branch(line: PaymentLine) -> str | None:
    """The branch a line came through, where the source says: a cash receipt or a POS terminal."""
    if line.channel is Channel.BRANCH_CASH:
        m = re.match(r"RCP-([A-Z]{3})-", line.bank_reference)
        return m.group(1) if m else None
    if line.channel is Channel.POS and line.terminal_id:
        m = re.match(r"2TP([A-Z]{3})\d", line.terminal_id)
        return m.group(1) if m else None
    return None


def amounts_due(view: LoanView) -> set[int]:
    """Amounts that, paid exactly, would be an obvious payment on this loan."""
    due = {view.arrears, view.outstanding}
    upcoming = [i for i in view.installments if i.outstanding > 0]
    if upcoming:
        due.add(upcoming[0].outstanding)
        due.add(upcoming[0].total)
    return {a for a in due if a > 0}


class RuleMatcher:
    def __init__(self, core: CoreBankingReader, virtual_account_loan: Callable[[str], str | None]):
        self.core = core
        self.virtual_account_loan = virtual_account_loan

    def propose(self, line: PaymentLine) -> Proposal | None:
        for rule in (self._not_repayment, self._virtual_account, self._loan_reference, self._pos_phone):
            proposal = rule(line)
            if proposal:
                return proposal
        return None

    def _match(self, line: PaymentLine, rule: str, loan_ref: str, evidence: dict) -> Proposal:
        return Proposal(line.line_id, "match", rule, [{"loan_ref": loan_ref, "amount_kobo": line.amount}], evidence)

    def _not_repayment(self, line: PaymentLine) -> Proposal | None:
        if line.channel not in (Channel.BANK_CSV, Channel.BANK_MT940):
            return None
        for pattern, meaning in NOT_REPAYMENT:
            if pattern.search(line.narration):
                return Proposal(line.line_id, "exclude", "not_repayment",
                                evidence={"narration_pattern": pattern.pattern, "meaning": meaning})
        return None

    def _virtual_account(self, line: PaymentLine) -> Proposal | None:
        if not line.virtual_account:
            return None
        loan_ref = self.virtual_account_loan(line.virtual_account)
        if not loan_ref:
            return None
        view = self.core.get_loan(loan_ref)
        if not view or view.status == "closed":
            return None
        evidence = {"virtual_account": line.virtual_account, "loan_status": view.status}
        if line.amount > view.outstanding:
            evidence["overpays_by_kobo"] = line.amount - view.outstanding
        return self._match(line, "virtual_account", loan_ref, evidence)

    def _loan_reference(self, line: PaymentLine) -> Proposal | None:
        refs = loan_refs_in(line.customer_reference, line.narration)
        if len(refs) != 1:
            return None  # none, or a line naming two loans: the agent's job
        view = self.core.get_loan(refs[0])
        if not view or view.status != "active":
            return None
        borrower = self.core.get_borrower(view.borrower_ref)
        shared_names = sorted(name_tokens(line.payer_name) & name_tokens(borrower.name if borrower else ""))
        amount_is_due = line.amount in amounts_due(view)
        branch = line_branch(line)
        same_branch = bool(branch and borrower and borrower.branch_code == branch)
        surname = (borrower.name.split()[-1].upper() if borrower and borrower.name else "")
        if branch and not same_branch:
            return None  # paid at another branch: a namesake, or a typo; the agent decides
        named = surname in shared_names and (amount_is_due or same_branch)
        # A POS line carries no name at all; then the terminal's branch and an amount due must both agree.
        nameless = not line.payer_name and same_branch and amount_is_due
        if not (named or nameless):
            return None  # the reference may be a typo for someone else's loan; the agent decides
        evidence = {"quoted_reference": refs[0], "shared_names": shared_names, "amount_is_due": amount_is_due,
                    "same_branch": same_branch}
        if line.amount > view.outstanding:
            evidence["overpays_by_kobo"] = line.amount - view.outstanding
        return self._match(line, "loan_reference", refs[0], evidence)

    def _pos_phone(self, line: PaymentLine) -> Proposal | None:
        if line.channel is not Channel.POS or not line.customer_reference:
            return None
        m = PHONE.match(re.sub(r"[\s\-]", "", line.customer_reference))
        if not m:
            return None
        borrower = self.core.find_by_phone("0" + m.group(1))
        if not borrower:
            return None
        active = [ref for ref in borrower.loan_refs if (v := self.core.get_loan(ref)) and v.status == "active"]
        if len(active) != 1:
            return None  # several loans: which one is a judgement, not a rule
        return self._match(line, "pos_phone", active[0], {"phone_matches": borrower.borrower_ref})


# ---- running over the database ----------------------------------------------------------------


def _line(row) -> PaymentLine:
    (line_id, channel, source_account, value_date, amount, narration, bank_reference, payer_name, payer_account,
     virtual_account, terminal_id, customer_reference) = row
    return PaymentLine(line_id, Channel(channel), source_account, value_date, amount, narration, bank_reference,
                       payer_name, payer_account, virtual_account, terminal_id, customer_reference)


LINE_COLUMNS = ("id, channel, source_account, value_date, amount_kobo, narration, bank_reference, payer_name, "
                "payer_account, virtual_account, terminal_id, customer_reference")


def save(conn: psycopg.Connection, p: Proposal) -> int:
    with conn.transaction():
        pid = conn.execute(
            "INSERT INTO proposals (line_id, kind, proposed_by, confidence, allocations, evidence) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (p.line_id, p.kind, f"rule:{p.rule}", p.confidence, Jsonb(p.allocations), Jsonb(p.evidence)),
        ).fetchone()[0]
        conn.execute("UPDATE payment_lines SET status = 'proposed' WHERE id = %s AND status = 'new'", (p.line_id,))
        db.audit(conn, f"rule:{p.rule}", "proposal.created", f"payment_line:{p.line_id}",
                 {"proposal_id": pid, "kind": p.kind, "allocations": p.allocations}, telemetry.current_trace_id())
    return pid


def run(conn: psycopg.Connection, matcher: RuleMatcher) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = conn.execute(f"SELECT {LINE_COLUMNS} FROM payment_lines WHERE status = 'new' ORDER BY value_date, id")
    with tracer.start_as_current_span("match.rules") as span:
        for row in rows.fetchall():
            line = _line(row)
            with tracer.start_as_current_span("match.line") as s:
                s.set_attributes({"payment.line_id": line.line_id, "payment.channel": line.channel.value})
                proposal = matcher.propose(line)
                outcome = proposal.rule if proposal else "left_for_agent"
                s.set_attribute("match.outcome", outcome)
            if proposal:
                save(conn, proposal)
            counts[outcome] = counts.get(outcome, 0) + 1
        span.set_attributes({f"match.{k}": v for k, v in counts.items()})
    return counts


def virtual_account_lookup(conn: psycopg.Connection) -> Callable[[str], str | None]:
    def lookup(account: str) -> str | None:
        row = conn.execute("SELECT loan_ref FROM virtual_accounts WHERE account_number = %s", (account,)).fetchone()
        return row[0] if row else None

    return lookup


def main() -> int:
    from repaydesk.adapters.fineract import FineractCoreBanking

    telemetry.setup("repaydesk-match")
    try:
        conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
        db.migrate(conn)
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.getenv("FINERACT_USERNAME", "mifos"), os.getenv("FINERACT_PASSWORD", "password"))
        counts = run(conn, RuleMatcher(core, virtual_account_lookup(conn)))
        total = sum(counts.values())
        print(f"[match] {total} new lines: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        return 0
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())

