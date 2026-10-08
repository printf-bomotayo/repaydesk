from collections import Counter
from datetime import date

import pytest

from repaydesk.domain import Channel
from repaydesk.synth.generator import Generator

AS_OF = date(2026, 10, 8)


@pytest.fixture(scope="module")
def book():
    return Generator(42, AS_OF).generate(2000)


def test_same_seed_same_book(book):
    again = Generator(42, AS_OF).generate(2000)
    assert [b.ref for b in again.borrowers] == [b.ref for b in book.borrowers]
    assert again.lines == book.lines
    assert again.truth == book.truth


def test_size_and_shape(book):
    assert len(book.borrowers) == 2000
    assert len({b.ref for b in book.borrowers}) == 2000
    assert len({b.phone for b in book.borrowers}) == 2000
    assert len({loan.ref for loan in book.loans}) == len(book.loans)
    assert len({loan.virtual_account for loan in book.loans}) == len(book.loans)
    assert book.groups and all(len(g.member_refs) >= 5 for g in book.groups)


def test_every_line_is_in_the_week_and_has_truth(book):
    assert {line.line_id for line in book.lines} == {t.line_id for t in book.truth}
    for line in book.lines:
        assert book.week_start <= line.value_date < book.as_of
        assert line.amount > 0


def test_allocations_add_up_and_point_at_real_loans(book):
    loans = {loan.ref for loan in book.loans}
    lines = {line.line_id: line for line in book.lines}
    for t in book.truth:
        if t.allocations:
            assert sum(a.amount for a in t.allocations) == lines[t.line_id].amount
            assert all(a.loan_ref in loans for a in t.allocations)


def test_schedules_sum_to_principal(book):
    for loan in book.loans:
        assert sum(i.principal for i in loan.schedule) == loan.principal
        assert all(i.principal % 100 == 0 and i.interest % 100 == 0 for i in loan.schedule)  # whole naira


def test_history_is_before_the_week(book):
    assert book.history
    assert all(r.paid_on < book.week_start for r in book.history)


def test_the_hard_cases_are_present(book):
    cases = Counter(t.case for t in book.truth)
    for case in ["exact_ref", "name_only", "business_name", "third_party", "group_leader", "not_repayment",
                 "duplicate_delivery", "virtual_account", "pos_phone", "cash_name"]:
        assert cases[case] > 0, case
    assert any(not t.resolvable for t in book.truth)
    assert {line.channel for line in book.lines} == set(Channel)


def test_mama_chika_style_business_names_appear(book):
    lines = {line.line_id: line for line in book.lines}
    business_payers = [lines[t.line_id].payer_name for t in book.truth if t.case == "business_name"]
    assert business_payers and all(p and p == p.upper() for p in business_payers)
