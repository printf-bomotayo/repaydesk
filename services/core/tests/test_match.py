"""Rule matching. The rules must never be wrong; how much they cover is the second question."""

from datetime import date, timedelta

import pytest

from repaydesk.adapters.memory import MemoryCoreBanking
from repaydesk.domain import Channel, PaymentLine
from repaydesk.match import RuleMatcher, amounts_due, loan_refs_in, name_tokens
from repaydesk.scoring import score
from repaydesk.synth.generator import Generator
from repaydesk.synth.names import BRANCHES, PRODUCTS
from repaydesk.synth.writers import source_key

AS_OF = date(2026, 10, 8)


def test_loan_references_are_found_however_they_are_typed():
    assert loan_refs_in("NIP/GTB/ADA OBI/ln-2026-001396 RPYMT") == ["LN-2026-001396"]
    assert loan_refs_in("REPAYMENT LN2026001396") == ["LN-2026-001396"]
    assert loan_refs_in("LN 2026 001396") == ["LN-2026-001396"]
    assert loan_refs_in(None, "2026-001396") == ["LN-2026-001396"]
    assert loan_refs_in("RPY LN-2026-00136") == []  # a dropped digit is not a reference
    assert loan_refs_in("LN-2026-000001 and LN-2026-000002") == ["LN-2026-000001", "LN-2026-000002"]
    assert loan_refs_in("TRF 20261396009988") == []  # a long number is not a reference


def test_name_tokens_ignore_titles_and_initials():
    assert name_tokens("MAMA CHIKA PROVISIONS") == {"CHIKA", "PROVISIONS"}
    assert name_tokens("A. OKAFOR") == {"OKAFOR"}


def _loaded(seed=5, borrowers=1500):
    book = Generator(seed, AS_OF).generate(borrowers)
    core = MemoryCoreBanking(AS_OF)
    core.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
    for b in book.borrowers:
        core.create_borrower(b)
    for loan in book.loans:
        core.create_loan(loan)
    for r in book.history:
        core.post_repayment(r.loan_ref, r.paid_on, r.amount, r.reference)
    va = {loan.virtual_account: loan.ref for loan in book.loans}
    return book, core, RuleMatcher(core, va.get)


@pytest.fixture(scope="module")
def scored():
    book, core, matcher = _loaded()
    truth = []
    keys = {line.line_id: source_key(line) for line in book.lines}
    for t in book.truth:
        truth.append({"source_key": keys[t.line_id], "case": t.case, "resolvable": t.resolvable,
                      "allocations": [{"loan_ref": a.loan_ref, "amount": a.amount} for a in t.allocations]})
    proposals = {}
    for line in book.lines:
        p = matcher.propose(line)
        if p:
            proposals[source_key(line)] = {"kind": p.kind, "allocations": p.allocations}
    return score(truth, proposals), book


def test_rules_are_never_wrong(scored):
    s, _ = scored
    assert s.wrong == [], s.table()


def test_rules_cover_the_certain_cases(scored):
    s, _ = scored
    c = s.by_case
    assert c["virtual_account"].correct == c["virtual_account"].lines
    assert c["not_repayment"].correct == c["not_repayment"].lines
    # Measured over 100 books: pos_phone 90%, exact_ref 71%, cash_ref 82%. The rest is left for the
    # agent on purpose; these floors catch a rule that silently stops matching.
    assert c["pos_phone"].correct >= 0.75 * c["pos_phone"].lines
    assert c["exact_ref"].correct >= 0.6 * c["exact_ref"].lines
    assert c["cash_ref"].correct >= 0.7 * c["cash_ref"].lines


def test_rules_leave_the_judgement_calls_alone(scored):
    s, _ = scored
    for case in ["name_only", "business_name", "third_party", "payer_only", "pidgin", "unresolvable",
                 "group_leader", "split_across_loans", "pos_blank", "cash_name"]:
        if case in s.by_case:
            assert s.by_case[case].proposed == 0, case


def test_a_typo_that_names_another_loan_is_not_matched():
    book, core, matcher = _loaded(seed=9, borrowers=300)
    victim, other = book.loans[0], book.loans[1]
    line = PaymentLine("bank_csv:T1", Channel.BANK_CSV, "1023456789", AS_OF - timedelta(days=2), 123_400_00,
                       "NIP/GTB/SOMEONE ELSE/RPY " + other.ref, "T1", payer_name="SOMEONE ELSE")
    assert other.ref != victim.ref
    assert matcher.propose(line) is None  # right format, real loan, but nothing else agrees


def test_a_typo_whose_amount_happens_to_be_due_is_still_not_matched():
    """The case that once got through: a mistyped reference naming a real loan, with an amount that
    loan happens to have due. Only a shared name may confirm a reference."""
    book, core, matcher = _loaded(seed=9, borrowers=300)
    other = book.loans[1]
    due = sorted(amounts_due(core.get_loan(other.ref)))[0]
    line = PaymentLine("branch_cash:T2", Channel.BRANCH_CASH, "BRANCH-CASH", AS_OF - timedelta(days=1), due,
                       "Cash deposit IBD by Qqqq Zzzz", "T2", payer_name="Qqqq Zzzz",
                       customer_reference=other.ref)
    assert matcher.propose(line) is None


def test_a_shared_first_name_does_not_confirm_a_reference():
    """Found in a 40-book sweep: FRIDAY IKPE quoted a mistyped reference that named Friday Briggs's
    loan, with an amount it had due. The payer's surname contradicts the borrower's."""
    book, core, matcher = _loaded(seed=9, borrowers=300)
    other = book.loans[1]
    borrower = core.get_borrower(other.borrower_ref)
    first = borrower.name.split()[0].upper()
    due = sorted(amounts_due(core.get_loan(other.ref)))[0]
    line = PaymentLine("bank_csv:T3", Channel.BANK_CSV, "1023456789", AS_OF - timedelta(days=1), due,
                       f"NIP/UBA/{first} QQQQ/RPY {other.ref}", "T3", payer_name=f"{first} QQQQ")
    assert matcher.propose(line) is None
    payer = borrower.name.upper()
    with_surname = PaymentLine("bank_csv:T4", Channel.BANK_CSV, "1023456789", AS_OF - timedelta(days=1), due,
                               f"NIP/UBA/{payer}/RPY {other.ref}", "T4", payer_name=payer)
    assert matcher.propose(with_surname) is not None  # and the honest version still matches


def test_a_namesake_at_another_branch_is_not_matched():
    """Found in a 100-book sweep: two borrowers called Yetunde Salami, a cash slip at one's branch
    quoting a mistyped reference that named the other's loan."""
    book, core, matcher = _loaded(seed=9, borrowers=300)
    other = book.loans[1]
    borrower = core.get_borrower(other.borrower_ref)
    elsewhere = next(code for code in ("IKJ", "KAN", "PHC") if code != borrower.branch_code)
    due = sorted(amounts_due(core.get_loan(other.ref)))[0]
    line = PaymentLine("branch_cash:T5", Channel.BRANCH_CASH, "BRANCH-CASH", AS_OF - timedelta(days=1), due,
                       f"Cash deposit {elsewhere} by {borrower.name}", f"RCP-{elsewhere}-00001",
                       payer_name=borrower.name, customer_reference=other.ref)
    assert matcher.propose(line) is None


@pytest.mark.parametrize("seed", range(100, 120))
def test_rules_are_never_wrong_across_many_books(seed):
    """Each book has different names, amounts and typos, so coincidences differ. The rules were
    swept over 100 books while being written; this keeps 20 of them in the suite."""
    book, _, matcher = _loaded(seed, 2000)
    lines_truth = {t.line_id: t for t in book.truth}
    for line in book.lines:
        t = lines_truth[line.line_id]
        if t.case == "duplicate_delivery":
            continue  # ingestion marks these; they never reach the matcher
        p = matcher.propose(line)
        if not p:
            continue
        if p.kind == "exclude":
            assert not t.allocations, (seed, line.line_id)
        else:
            want = sorted((a.loan_ref, a.amount) for a in t.allocations)
            got = sorted((a["loan_ref"], a["amount_kobo"]) for a in p.allocations)
            assert t.resolvable and got == want, (seed, line.line_id, t.case)


def test_amounts_due_include_the_next_instalment_and_the_arrears():
    _, core, _ = _loaded(seed=9, borrowers=300)
    view = next(v for ref in list(core.loans)[:200] if (v := core.get_loan(ref)) and v.arrears > 0)
    due = amounts_due(view)
    assert view.arrears in due and view.outstanding in due


# ---- against the database -----------------------------------------------------------------


@pytest.mark.db
def test_run_proposes_and_a_line_has_one_live_proposal(conn, week_dir):
    import psycopg

    from repaydesk.adapters.statements import FolderStatementSource
    from repaydesk.ingest import ingest_folder
    from repaydesk.match import Proposal, run, save
    from tests.conftest import WEBHOOK_SECRET

    data, book = week_dir
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    core = MemoryCoreBanking(AS_OF)
    core.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
    for b in book.borrowers:
        core.create_borrower(b)
    for loan in book.loans:
        core.create_loan(loan)
    for r in book.history:
        core.post_repayment(r.loan_ref, r.paid_on, r.amount, r.reference)

    counts = run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
    proposed = sum(v for k, v in counts.items() if k != "left_for_agent")
    assert proposed > 0 and counts["left_for_agent"] > 0
    assert conn.execute("SELECT count(*) FROM proposals").fetchone()[0] == proposed
    assert conn.execute("SELECT count(*) FROM payment_lines WHERE status = 'proposed'").fetchone()[0] == proposed
    assert conn.execute("SELECT count(*) FROM audit_log WHERE action = 'proposal.created'").fetchone()[0] == proposed

    # A second run proposes nothing more: proposed lines are no longer 'new'.
    again = run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
    assert set(again) <= {"left_for_agent"}

    # The database refuses a second live proposal for the same line.
    line_id = conn.execute("SELECT line_id FROM proposals LIMIT 1").fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        save(conn, Proposal(line_id, "exclude", "test"))
    # And a match must say which loans; an exclusion must not.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO proposals (line_id, kind, proposed_by, confidence) VALUES (%s, 'match', 't', 1)",
                     (line_id,))
