"""The eval's own arithmetic: which cases go in, how an answer is graded, and what the report says.
No model is called here; a run's results are made up to test the scoring."""

import json
from pathlib import Path

import pytest

from repaydesk.evals import CASES, grade, interval, markdown, select, summary

PLACEABLE = {"id": "bank_csv:NGT1", "case": "name_only", "placeable": True,
             "expected": [["LN-2026-000001", 60_500_00]]}
UNPLACEABLE = {"id": "pos:1", "case": "pos_blank", "placeable": False, "expected": None}


def test_an_answer_is_correct_only_to_the_kobo():
    assert grade(PLACEABLE, "match", [["LN-2026-000001", 60_500_00]]) == "correct"
    assert grade(PLACEABLE, "match", [["LN-2026-000001", 60_499_00]]) == "wrong"
    assert grade(PLACEABLE, "match", [["LN-2026-000002", 60_500_00]]) == "wrong"
    assert grade(PLACEABLE, "exclude", []) == "wrong"  # calling a repayment "not a repayment" loses it
    assert grade(PLACEABLE, "suspense", []) == "held"
    assert grade(PLACEABLE, None, []) == "failed"
    assert grade(UNPLACEABLE, "suspense", []) == "correct"
    assert grade(UNPLACEABLE, "match", [["LN-2026-000501", 106_050_00]]) == "wrong"
    split = dict(PLACEABLE, expected=[["LN-2026-000581", 605_000_00], ["LN-2026-000582", 131_250_00]])
    assert grade(split, "match", [["LN-2026-000582", 131_250_00], ["LN-2026-000581", 605_000_00]]) == "correct"


def test_the_interval_is_wide_for_sixty_cases():
    lo, hi = interval(31, 60)
    assert 0.38 < lo < 0.40 and 0.63 < hi < 0.65
    assert interval(0, 0) == (0.0, 0.0) and interval(60, 60)[1] == pytest.approx(1.0)


def _result(case, g, action, cost=0.03):
    return {"id": case["id"], "case": case["case"], "placeable": case["placeable"], "expected": case["expected"],
            "grade": g, "action": action, "allocations": case["expected"] or [] if action == "match" else [],
            "said": action, "said_allocations": [], "confidence": 0.8, "reason": "because | a pipe",
            "failed_checks": [],
            "triage": "names_a_person", "tool_calls": 3, "tools": [], "error": None, "cost_usd": cost, "usage": {},
            "seconds": 10.0}


def test_the_summary_separates_wrong_from_held_and_the_report_says_so():
    results = [_result(PLACEABLE, "correct", "match"), _result(dict(PLACEABLE, id="b"), "wrong", "match"),
               _result(dict(PLACEABLE, id="c"), "held", "suspense"), _result(UNPLACEABLE, "correct", "suspense")]
    s = summary(results)
    assert (s["correct"], s["wrong"], s["held"], s["failed"]) == (2, 1, 1, 0)
    assert s["precision"] == 0.5  # of the two proposed, one was right
    assert s["placed_correctly"] == pytest.approx(1 / 3) and s["held_when_unplaceable"] == 1.0
    assert s["cost_usd"] == 0.12 and s["tool_calls_per_case"] == 3.0
    md = markdown(results, s, {"date": "2026-10-12", "investigator": "m", "classifier": "c", "min_confidence": 0.7})
    assert "| **Wrong** (a reviewer must catch these) | **1** |" in md
    assert "because / a pipe" in md  # a reason can't break the table


def test_the_committed_case_set_is_the_one_select_makes():
    """evals/cases.json is the reviewed set; selecting again from the seeded truth must give the same cases."""
    truth_file = CASES.parents[1] / "data" / "truth" / "truth.json"
    if not truth_file.exists():
        pytest.skip("no seeded week on disk")
    truth = json.loads(truth_file.read_text(encoding="utf-8"))
    if truth["as_of"] != "2026-10-08":
        pytest.skip("a week seeded on another date")
    committed = json.loads(Path(CASES).read_text(encoding="utf-8"))
    assert select(truth) == committed
    kinds = {c["case"] for c in committed["cases"]}
    assert len(committed["cases"]) == 60 and {"pos_blank", "unresolvable", "group_leader"} <= kinds
    assert all(c["expected"] is None for c in committed["cases"] if not c["placeable"])
