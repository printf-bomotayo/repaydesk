"""The investigator agent: Repaydesk's checks on its answers, what a run records and sets in motion,
and one whole investigation against a scripted model, through the real tool runner and the real MCP
server. No network and no API key: the model's replies come from a stub transport."""

import json

import anyio
import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from mcp.client.client import Client

from repaydesk import investigator as inv
from repaydesk.adapters.memory import MemoryCoreBanking
from repaydesk.investigator import Allocation, Finding, Run, Triage, Verdict, check, record
from repaydesk.servers import bankbook as bankbook_server
from repaydesk.synth.writers import source_key


def _verdict(outcome="match", allocations=(), confidence=0.9) -> Verdict:
    return Verdict(outcome=outcome, allocations=[Allocation(loan_ref=r, amount_kobo=a) for r, a in allocations],
                   confidence=confidence, reason="test", findings=[Finding(fact="a fact", bearing="for")],
                   candidates=[r for r, _ in allocations])


def _active_loan(book, core: MemoryCoreBanking) -> str:
    return next(x.ref for x in book.loans if (v := core.get_loan(x.ref)) and v.status == "active")


# ---- checks: the agent's answer isn't taken on trust ----------------------------------------------


@pytest.mark.db
def test_an_answer_that_doesnt_add_up_or_names_a_closed_loan_waits_in_suspense(placed_week):
    bk, book = placed_week
    loan = _active_loan(book, bk.core)
    assert check(_verdict(allocations=[(loan, 100_00)]), 100_00, bk.core)[0] == "match"
    assert check(_verdict(allocations=[(loan, 99_00)]), 100_00, bk.core)[0] == "suspense"  # a kobo out is out
    assert check(_verdict(allocations=[(loan, 50_00), (loan, 50_00)]), 100_00, bk.core)[0] == "suspense"
    assert check(_verdict(allocations=[("LN-2099-000001", 100_00)]), 100_00, bk.core)[0] == "suspense"
    assert check(_verdict(allocations=[(loan, 100_00)], confidence=0.69), 100_00, bk.core)[0] == "suspense"
    assert check(_verdict(allocations=[]), 100_00, bk.core)[0] == "suspense"
    assert check(_verdict("not_repayment"), 100_00, bk.core)[0] == "exclude"
    assert check(_verdict("not_repayment", confidence=0.5), 100_00, bk.core)[0] == "suspense"
    action, checks = check(_verdict(allocations=[("LN-2099-000001", 100_00)]), 100_00, bk.core)
    assert {"check": "loan_active", "ok": False, "detail": "LN-2099-000001: not found in core banking"} in checks


# ---- what a run sets in motion ---------------------------------------------------------------------


def _open_line(conn) -> tuple[str, int]:
    return conn.execute("SELECT id, amount_kobo FROM payment_lines WHERE status = 'new' ORDER BY id LIMIT 1"
                        ).fetchone()


@pytest.mark.db
def test_a_match_becomes_a_proposal_for_people_never_a_posting(placed_week, conn):
    bk, book = placed_week
    line_id, amount = _open_line(conn)
    loan = _active_loan(book, bk.core)
    run = Run(line_id, triage=Triage(kind="names_a_business", names=[], business="X", group=None, paid_for=None,
                                     reference=None, language="english"),
              verdict=_verdict(allocations=[(loan, amount)]), steps=[{"tool": "get_loan", "input": {}, "output": "{}"}])
    run.add_usage(inv.INVESTIGATOR_MODEL, type("U", (), {"input_tokens": 1000, "output_tokens": 100})())
    out = record(conn, run, amount, bk.core)
    assert out["outcome"] == "proposed" and out["proposal_id"]
    p = conn.execute("SELECT proposed_by, status, kind, allocations, evidence->>'run_id' FROM proposals WHERE id = %s",
                     (out["proposal_id"],)).fetchone()
    assert p[:3] == ("agent:investigator", "pending", "match") and p[3] == [{"loan_ref": loan, "amount_kobo": amount}]
    assert int(p[4]) == out["run_id"]
    assert conn.execute("SELECT status FROM payment_lines WHERE id = %s", (line_id,)).fetchone()[0] == "proposed"
    assert conn.execute("SELECT count(*) FROM posting_outbox WHERE line_id = %s", (line_id,)).fetchone()[0] == 0
    r = conn.execute("SELECT outcome, steps, cost_usd, classification->>'kind' FROM agent_runs WHERE id = %s",
                     (out["run_id"],)).fetchone()
    assert r[0] == "proposed" and r[1][0]["tool"] == "get_loan" and float(r[2]) == 0.003  # 1000 in, 100 out
    assert r[3] == "names_a_business"
    actor, action = conn.execute("SELECT actor, action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert (actor, action) == ("agent:investigator", "proposal.created")


@pytest.mark.db
def test_doubt_goes_to_suspense_a_failure_leaves_the_line_and_a_placed_line_is_left_alone(placed_week, conn):
    bk, book = placed_week
    line_id, amount = _open_line(conn)
    out = record(conn, Run(line_id, verdict=_verdict("suspense", confidence=0.4)), amount, bk.core)
    assert out["outcome"] == "suspense" and out["proposal_id"] is None
    assert conn.execute("SELECT status FROM payment_lines WHERE id = %s", (line_id,)).fetchone()[0] == "suspense"
    last_action = conn.execute("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last_action == "investigation.suspense"

    other, amount2 = _open_line(conn)
    out = record(conn, Run(other, error="APIConnectionError: no route"), amount2, bk.core)
    assert out["outcome"] == "failed"
    assert conn.execute("SELECT status FROM payment_lines WHERE id = %s", (other,)).fetchone()[0] == "new"
    assert conn.execute("SELECT error FROM agent_runs WHERE id = %s", (out["run_id"],)).fetchone()[0].startswith(
        "APIConnectionError")

    placed = conn.execute("SELECT id, amount_kobo FROM payment_lines WHERE status = 'proposed' LIMIT 1").fetchone()
    loan = _active_loan(book, bk.core)
    out = record(conn, Run(placed[0], verdict=_verdict(allocations=[(loan, placed[1])])), placed[1], bk.core)
    assert out["outcome"] == "superseded" and out["proposal_id"] is None
    assert conn.execute("SELECT count(*) FROM proposals WHERE line_id = %s AND proposed_by = %s",
                        (placed[0], "agent:investigator")).fetchone()[0] == 0


# ---- one whole investigation, against a scripted model -------------------------------------------


class ScriptedModel:
    """Answers the Messages API the way the models would: triage, one lookup, then a verdict."""

    def __init__(self, payer: str, verdict: dict):
        self.payer, self.verdict, self.requests = payer, verdict, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "beta": request.headers.get("anthropic-beta", "")})
        last = body["messages"][-1]["content"]
        answered = isinstance(last, list) and any(b.get("type") == "tool_result" for b in last)
        if "haiku" in body["model"]:
            content, stop = [{"type": "text", "text": json.dumps({
                "kind": "names_a_business", "names": [], "business": self.payer, "group": None, "paid_for": None,
                "reference": None, "language": "english"})}], "end_turn"
        elif not answered:
            content, stop = [{"type": "tool_use", "id": "toolu_01", "name": "search_borrowers",
                              "input": {"text": self.payer}}], "tool_use"
        else:
            content, stop = [{"type": "text", "text": json.dumps(self.verdict)}], "end_turn"
        return httpx2.Response(200, json={
            "id": f"msg_{len(self.requests)}", "type": "message", "role": "assistant", "model": body["model"],
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 150, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})


@pytest.mark.db
def test_a_whole_investigation_through_the_tool_runner_and_the_mcp_server(placed_week, conn):
    bk, book = placed_week
    truth = next(t for t in book.truth if t.case == "business_name" and len(t.allocations) == 1)
    original = next(x for x in book.lines if x.line_id == truth.line_id)
    line_id = source_key(original)
    assert conn.execute("SELECT status FROM payment_lines WHERE id = %s", (line_id,)).fetchone()[0] == "new"
    loan_ref = truth.allocations[0].loan_ref
    model = ScriptedModel(original.payer_name, {
        "outcome": "match", "allocations": [{"loan_ref": loan_ref, "amount_kobo": original.amount}],
        "confidence": 0.88, "reason": "Trading name and amount agree.",
        "findings": [{"fact": "The trading name matches.", "bearing": "for"}], "candidates": [loan_ref]})
    client = AsyncAnthropic(api_key="test", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(model)))

    async def investigate():
        async with Client(bankbook_server.build(bk)) as mcp:
            return await inv.investigate_lines(conn, bk, inv.Investigator(client, mcp), [line_id])

    [out] = anyio.run(investigate)
    assert out["outcome"] == "proposed" and out["confidence"] == 0.88

    triage, first, second = model.requests
    assert triage["body"]["model"] == inv.CLASSIFIER_MODEL and second["body"]["model"] == inv.INVESTIGATOR_MODEL
    assert {t["name"] for t in first["body"]["tools"]} == {
        "get_payment_line", "search_borrowers", "borrowers_by_line_phone", "get_borrower", "get_group", "get_loan",
        "loans_fitting", "nearby_loan_refs", "payment_history", "find_payments"}  # the bankbook's, nothing else
    assert first["body"]["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in first["beta"]
    assert first["body"]["output_config"]["effort"] == "medium"
    shown = json.dumps(first["body"]["messages"][0]["content"])
    assert original.payer_name in shown and (original.payer_account or "") not in shown.replace("****", "")
    result = next(b for b in second["body"]["messages"][-1]["content"] if b["type"] == "tool_result")
    assert truth.allocations[0].loan_ref in json.dumps(result)  # the search found the borrower's loan

    run = conn.execute("SELECT steps, usage, cost_usd, verdict->>'reason' FROM agent_runs WHERE id = %s",
                       (out["run_id"],)).fetchone()
    assert [s["tool"] for s in run[0]] == ["search_borrowers"] and loan_ref in run[0][0]["output"]
    assert run[1][inv.INVESTIGATOR_MODEL]["input"] == 2400 and run[1][inv.CLASSIFIER_MODEL]["input"] == 1200
    assert run[3] == "Trading name and amount agree."
    assert conn.execute("SELECT status FROM payment_lines WHERE id = %s", (line_id,)).fetchone()[0] == "proposed"
