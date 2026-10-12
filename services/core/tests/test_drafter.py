"""The drafting agent, against a scripted model: a draft that passes, one repaired after failing a
check, one that still fails (saved, unapprovable as it stands), and a loan with nothing due any more.
No network and no API key: the model's replies come from a stub transport."""

import json
from datetime import date

import anyio
import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from repaydesk.collections import drafter, queue

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 8)


class Scripted:
    """Answers each request with the next scripted draft, and keeps what it was asked."""

    def __init__(self, *bodies: str):
        self.bodies, self.requests = list(bodies), []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        draft = {"body": self.bodies.pop(0), "note": "Firm but friendly, as the policy asks."}
        return httpx2.Response(200, json={
            "id": f"msg_{len(self.requests)}", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(draft)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 900, "output_tokens": 120}})


def client(model: Scripted) -> drafter.Drafter:
    return drafter.Drafter(AsyncAnthropic(api_key="test",
                                          http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(model))))


def good(case: drafter.Case) -> str:
    return (f"Good morning {case.first_name}, this is Demo Microfinance Bank. Your loan {case.loan_ref} has "
            f"{case.brief()['amount_due']} due. Please pay into your loan's virtual account or at any branch with "
            f"the loan reference. Reply here if you'd like to talk.")


def first_due(conn, n=0):
    return queue.overview(conn, TODAY, limit=50)["due"]["items"][n]


def run_morning(conn, core, model, items):
    return anyio.run(drafter.draft_morning, conn, core, client(model), items)


def test_a_good_draft_waits_for_the_manager_with_its_checks(placed_week, conn):
    bk, _ = placed_week
    item = first_due(conn)
    case = drafter.gather(conn, bk.core, item)
    model = Scripted(good(case))
    [out] = run_morning(conn, bk.core, model, [item])
    assert out["outcome"] == "drafted" and out["passes_policy"] and out["attempts"] == 1
    row = conn.execute("SELECT status, body, drafted_by, channel, bucket, amount_due_kobo, checks, run_id "
                       "FROM collection_messages WHERE id = %s", (out["message_id"],)).fetchone()
    assert row[:3] == ("draft", good(case), "agent:collector") and row[5] == case.amount_due_kobo
    assert row[3] == drafter.CHANNEL[case.bucket] and row[4] == case.bucket and all(c["ok"] for c in row[6])
    run = conn.execute("SELECT agent, loan_ref, line_id, outcome, verdict->>'note' FROM agent_runs WHERE id = %s",
                       (row[7],)).fetchone()
    assert run == ("collector", case.loan_ref, None, "drafted", "Firm but friendly, as the policy asks.")
    asked = model.requests[0]
    assert asked["model"] == drafter.DRAFTER_MODEL and "tools" not in asked  # nothing to look up
    brief = asked["messages"][0]["content"]
    assert case.loan_ref in brief and case.first_name in brief and case.brief()["amount_due"] in brief
    assert conn.execute("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "message.drafted"
    # The loan now has a draft waiting, so it leaves the morning list.
    assert item["loan_ref"] not in [d["loan_ref"] for d in queue.overview(conn, TODAY, limit=500)["due"]["items"]]


def test_a_draft_that_fails_a_check_is_repaired_once(placed_week, conn):
    bk, _ = placed_week
    item = first_due(conn)
    case = drafter.gather(conn, bk.core, item)
    model = Scripted(good(case) + " Or the police will hear of it.", good(case))
    [out] = run_morning(conn, bk.core, model, [item])
    assert out["passes_policy"] and out["attempts"] == 2
    repair = model.requests[1]["messages"]
    assert len(repair) == 3 and "no_threats" in repair[2]["content"] and "police" in repair[2]["content"]


def test_a_draft_that_still_fails_is_saved_with_its_failures(placed_week, conn):
    bk, _ = placed_week
    item = first_due(conn)
    case = drafter.gather(conn, bk.core, item)
    wrong = good(case).replace(case.brief()["amount_due"], "₦1")
    [out] = run_morning(conn, bk.core, Scripted(wrong, wrong), [item])
    assert out["outcome"] == "drafted" and not out["passes_policy"] and out["attempts"] == 2
    checks = conn.execute("SELECT checks FROM collection_messages WHERE id = %s", (out["message_id"],)).fetchone()[0]
    assert [c["check"] for c in checks if not c["ok"]] == ["amount_due"]


def test_nothing_is_drafted_for_a_loan_with_nothing_due(placed_week, conn):
    bk, book = placed_week
    paid = next(x.ref for x in book.loans if (v := bk.core.get_loan(x.ref)) and v.status == "active" and v.arrears == 0)
    item = {**first_due(conn), "loan_ref": paid}
    model = Scripted()
    [out] = run_morning(conn, bk.core, model, [item])
    assert out["outcome"].startswith("skipped") and model.requests == []
