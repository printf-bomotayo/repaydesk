"""Approvals: who may approve what, and what an approval sets in motion."""

import threading
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from repaydesk import approvals, db, staff
from repaydesk.approvals import Prior, judge
from repaydesk.match import Proposal, save
from repaydesk.staff import LIMITS, Staff

OFFICER = Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"])
OFFICER2 = Staff("kunle", "Kunle Adebayo", "officer", LIMITS["officer"])
SUPERVISOR = Staff("tunde", "Tunde Bakare", "supervisor", LIMITS["supervisor"])
AUDITOR = Staff("hadiza", "Hadiza Lawal", "auditor", 0)

N = 100  # kobo per naira


# ---- the rules, without a database ------------------------------------------------------------


def test_one_approval_settles_a_line_within_the_threshold():
    v = judge(OFFICER, 126_000 * N, [])
    assert v.allowed and v.settles


def test_the_threshold_itself_needs_one_approval_and_a_kobo_more_needs_two():
    assert judge(OFFICER, 500_000 * N, []).settles
    over = judge(OFFICER, 500_000 * N + 1, [])
    assert over.allowed and not over.settles


def test_an_officer_vouches_first_and_a_supervisor_completes():
    amount = 2_500_000 * N
    assert judge(OFFICER, amount, []).allowed
    v = judge(SUPERVISOR, amount, [Prior(OFFICER.id, OFFICER.limit_kobo)])
    assert v.allowed and v.settles


def test_two_officers_cannot_settle_what_neither_may_approve():
    amount = 875_000 * N
    v = judge(OFFICER2, amount, [Prior(OFFICER.id, OFFICER.limit_kobo)])
    assert not v.allowed and v.code == "over_limit"


def test_a_supervisor_first_lets_an_officer_give_the_second_approval():
    """At least one approver's limit must cover the amount, not necessarily the last one's."""
    v = judge(OFFICER, 875_000 * N, [Prior(SUPERVISOR.id, SUPERVISOR.limit_kobo)])
    assert v.allowed and v.settles


def test_nobody_approves_twice():
    v = judge(SUPERVISOR, 875_000 * N, [Prior(SUPERVISOR.id, SUPERVISOR.limit_kobo)])
    assert not v.allowed and v.code == "same_person"


def test_auditors_decide_nothing():
    assert judge(AUDITOR, 1 * N, []).code == "read_only"


def test_above_every_limit_waits_for_a_bigger_limit():
    v = judge(SUPERVISOR, 6_000_000 * N, [Prior(OFFICER.id, OFFICER.limit_kobo)])
    assert not v.allowed and v.code == "over_limit"


# ---- tokens ------------------------------------------------------------------------------------


def test_tokens_expire_and_cannot_be_forged():
    t = staff.issue_token("amaka", "s3cret", now=1_000_000)
    assert staff.read_token(t, "s3cret", now=1_000_000 + 60) == "amaka"
    assert staff.read_token(t, "s3cret", now=1_000_000 + staff.TOKEN_HOURS * 3600 + 1) is None
    assert staff.read_token(t, "other", now=1_000_000) is None
    body, sig = t.split(".")
    forged = staff._b64(staff._unb64(body).replace(b"amaka", b"tunde")) + "." + sig
    assert staff.read_token(forged, "s3cret", now=1_000_000) is None
    assert staff.read_token("garbage", "s3cret") is None


def test_passwords_are_hashed_and_checked():
    h = staff.hash_password("pa55")
    assert "pa55" not in h and staff.check_password("pa55", h) and not staff.check_password("pa56", h)


# ---- against the database ------------------------------------------------------------------------

PASSWORD = "test-staff-password"


def _line(conn, ref: str, amount: int) -> str:
    line_id = f"bank_csv:{ref}"
    conn.execute(
        "INSERT INTO payment_lines (id, channel, source_account, value_date, amount_kobo, narration, "
        "bank_reference, dedupe_key) VALUES (%s, 'bank_csv', '1023456789', %s, %s, %s, %s, %s)",
        (line_id, date(2026, 10, 7), amount, f"NIP/TEST/{ref}", ref, line_id),
    )
    return line_id


def _proposal(conn, ref: str, amount: int, kind: str = "match", split: int = 1) -> int:
    line_id = _line(conn, ref, amount)
    if kind == "exclude":
        return save(conn, Proposal(line_id, "exclude", "not_repayment"))
    part = amount // split
    allocs = [{"loan_ref": f"LN-2026-00{1000 + i}", "amount_kobo": part + (amount - part * split if i == 0 else 0)}
              for i in range(split)]
    return save(conn, Proposal(line_id, "match", "loan_reference", allocations=allocs))


@pytest.fixture
def staffed(conn):
    staff.ensure_demo_staff(conn, PASSWORD)
    return conn


def _one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


@pytest.mark.db
def test_an_approved_match_reaches_the_outbox_in_the_same_step(staffed):
    conn = staffed
    pid = _proposal(conn, "A1", 126_000 * N)
    out = approvals.approve(conn, pid, OFFICER)
    assert out.status == "approved" and out.outbox_id
    row = conn.execute("SELECT idempotency_key, amount_kobo, approved_by, status, allocations "
                       "FROM posting_outbox WHERE id = %s", (out.outbox_id,)).fetchone()
    assert row[0] == f"RD-P{pid}" and row[1] == 126_000 * N and row[2] == ["amaka"] and row[3] == "pending"
    assert row[4][0]["loan_ref"] == "LN-2026-001000"
    assert _one(conn, "SELECT status FROM payment_lines WHERE id = 'bank_csv:A1'") == "approved"
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
    assert actions[-2:] == ["proposal.approved", "posting.queued"]


@pytest.mark.db
def test_a_large_payment_takes_two_people(staffed):
    conn = staffed
    pid = _proposal(conn, "B1", 875_000 * N, split=5)
    assert approvals.approve(conn, pid, OFFICER).status == "first_approval"
    assert _one(conn, "SELECT count(*) FROM posting_outbox") == 0
    with pytest.raises(approvals.Refused) as e:
        approvals.approve(conn, pid, OFFICER)
    assert e.value.code == "same_person"
    with pytest.raises(approvals.Refused) as e:
        approvals.approve(conn, pid, OFFICER2)
    assert e.value.code == "over_limit"
    out = approvals.approve(conn, pid, SUPERVISOR)
    assert out.status == "approved"
    assert _one(conn, "SELECT approved_by FROM posting_outbox WHERE proposal_id = %s", pid) == ["amaka", "tunde"]


@pytest.mark.db
def test_an_approved_exclusion_marks_the_line_and_posts_nothing(staffed):
    conn = staffed
    pid = _proposal(conn, "C1", 2_500_000 * N, kind="exclude")
    approvals.approve(conn, pid, OFFICER)
    approvals.approve(conn, pid, SUPERVISOR)
    assert _one(conn, "SELECT status FROM payment_lines WHERE id = 'bank_csv:C1'") == "excluded"
    assert _one(conn, "SELECT count(*) FROM posting_outbox") == 0


@pytest.mark.db
def test_a_rejection_needs_a_reason_and_hands_the_line_to_the_agent(staffed):
    from repaydesk.adapters.memory import MemoryCoreBanking
    from repaydesk.match import RuleMatcher, run

    conn = staffed
    pid = _proposal(conn, "D1", 60_500 * N)
    with pytest.raises(approvals.Refused) as e:
        approvals.reject(conn, pid, OFFICER, "  ")
    assert e.value.code == "reason_required"
    with pytest.raises(approvals.Refused):
        approvals.reject(conn, pid, AUDITOR, "Wrong borrower")
    approvals.reject(conn, pid, OFFICER, "Wrong borrower")
    assert _one(conn, "SELECT status FROM payment_lines WHERE id = 'bank_csv:D1'") == "new"
    assert _one(conn, "SELECT reason FROM decisions WHERE proposal_id = %s", pid) == "Wrong borrower"
    with pytest.raises(approvals.Refused) as e:
        approvals.approve(conn, pid, SUPERVISOR)
    assert e.value.code == "not_pending"

    class Eager(RuleMatcher):  # a rule that would propose the same thing again
        def propose(self, line):
            return Proposal(line.line_id, "exclude", "not_repayment")

    counts = run(conn, Eager(MemoryCoreBanking(date(2026, 10, 8)), {}.get))
    assert counts == {}  # the rejected line isn't offered to rules at all
    assert _one(conn, "SELECT count(*) FROM proposals WHERE line_id = 'bank_csv:D1'") == 1


@pytest.mark.db
def test_the_database_refuses_what_the_code_would_never_do(staffed):
    conn = staffed
    pid = _proposal(conn, "E1", 10_000 * N)
    with pytest.raises(psycopg.errors.CheckViolation):  # a rejection with no reason
        conn.execute("INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo) "
                     "VALUES (%s, 'amaka', 'reject', 0)", (pid,))
    conn.execute("INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo) "
                 "VALUES (%s, 'amaka', 'approve', 0)", (pid,))
    with pytest.raises(psycopg.errors.UniqueViolation):  # the same person twice
        conn.execute("INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo) "
                     "VALUES (%s, 'amaka', 'approve', 0)", (pid,))
    with pytest.raises(psycopg.errors.CheckViolation):  # an auditor with a limit
        conn.execute("INSERT INTO staff (id, name, role, limit_kobo, password_hash) "
                     "VALUES ('x', 'X', 'auditor', 1, 'h')")


@pytest.mark.db
def test_two_people_approving_at_once_queue_one_posting(schema_url):
    with db.connect(schema_url) as c:
        db.migrate(c)
        staff.ensure_demo_staff(c, PASSWORD)
        pid = _proposal(c, "F1", 300_000 * N)
    barrier = threading.Barrier(2)
    results = []

    def click(who):
        with db.connect(schema_url) as c:
            barrier.wait()
            try:
                results.append(approvals.approve(c, pid, who).status)
            except approvals.Refused as e:
                results.append(e.code)

    threads = [threading.Thread(target=click, args=(w,)) for w in (OFFICER, SUPERVISOR)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["approved", "not_pending"]
    with db.connect(schema_url) as c:
        assert _one(c, "SELECT count(*) FROM posting_outbox") == 1
        assert _one(c, "SELECT count(*) FROM decisions") == 1


# ---- over HTTP -----------------------------------------------------------------------------------


@pytest.fixture
def client(schema_url, monkeypatch):
    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("STAFF_DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from repaydesk.api import app

    with TestClient(app) as c, db.connect(schema_url) as conn:
        yield c, conn


def _as(c, staff_id):
    r = c.post("/auth/sign-in", json={"staff_id": staff_id, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.db
def test_the_queue_needs_a_signed_in_person(client):
    c, _ = client
    assert c.get("/queue").status_code == 401
    assert c.get("/queue", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert c.post("/auth/sign-in", json={"staff_id": "amaka", "password": "wrong"}).status_code == 401
    assert c.post("/auth/sign-in", json={"staff_id": "nobody", "password": PASSWORD}).status_code == 401
    assert c.get("/me", headers=_as(c, "Amaka")).json()["role"] == "officer"


@pytest.mark.db
def test_a_morning_in_the_queue(client):
    c, conn = client
    small = _proposal(conn, "G1", 126_000 * N)
    group = _proposal(conn, "G2", 875_000 * N, split=5)
    reversal = _proposal(conn, "G3", 2_500_000 * N, kind="exclude")
    amaka, tunde, hadiza = _as(c, "amaka"), _as(c, "tunde"), _as(c, "hadiza")

    q = c.get("/queue", headers=amaka).json()
    assert [i["proposal_id"] for i in q["items"]] == [reversal, group, small]  # largest first
    assert q["counts"] == {"all": 3, "two_person": 2, "exclusions": 1} and q["total"] == 3

    def ids(**params):
        return [i["proposal_id"] for i in c.get("/queue", headers=amaka, params=params).json()["items"]]

    assert ids(view="two_person") == [reversal, group]
    assert ids(view="exclusions") == [reversal]
    assert ids(limit=1, offset=1) == [group]
    assert c.get("/queue", headers=amaka, params={"view": "mine"}).status_code == 422
    by_id = {i["proposal_id"]: i for i in q["items"]}
    assert by_id[group]["approvals_needed"] == 2 and by_id[group]["you"] == {
        "can_approve": True, "settles": False, "refused": None, "message": None}
    assert all(not i["you"]["can_approve"] for i in c.get("/queue", headers=hadiza).json()["items"])
    assert c.post(f"/proposals/{small}/approve", headers=hadiza).json()["refused"] == "read_only"

    assert c.post(f"/proposals/{group}/approve", headers=amaka).json()["status"] == "first_approval"
    mine = {i["proposal_id"]: i for i in c.get("/queue", headers=amaka).json()["items"]}[group]
    assert mine["approvals"][0]["name"] == "Amaka Nwosu" and mine["you"]["refused"] == "same_person"
    r = c.post(f"/proposals/{group}/approve", headers=tunde)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["outbox_id"]

    r = c.post(f"/proposals/{reversal}/reject", headers=tunde, json={"reason": ""})
    assert r.status_code == 422 and r.json()["refused"] == "reason_required"
    r = c.post(f"/proposals/{reversal}/reject", headers=tunde, json={"reason": "Needs more evidence"})
    assert r.status_code == 200
    assert c.post(f"/proposals/{reversal}/approve", headers=amaka).status_code == 409

    detail = c.get(f"/proposals/{group}", headers=hadiza).json()
    assert [d["staff_id"] for d in detail["decisions"]] == ["amaka", "tunde"]
    assert detail["posting"]["idempotency_key"] == f"RD-P{group}" and detail["posting"]["status"] == "pending"
    assert c.get("/proposals/999999", headers=hadiza).status_code == 404
    assert [i["proposal_id"] for i in c.get("/queue", headers=amaka).json()["items"]] == [small]


def _gateway_stops(conn, outbox_id: int, action: str, error: str) -> None:
    """What the gateway writes when a posting stops: the row failed, and its audit entry."""
    line_id = _one(conn, "UPDATE posting_outbox SET status = 'failed', attempts = attempts + 1, last_error = %s "
                         "WHERE id = %s RETURNING line_id", error, outbox_id)
    db.audit(conn, "service:gateway", action, f"payment_line:{line_id}", {"outbox_id": outbox_id, "error": error})


@pytest.mark.db
def test_a_person_sends_a_failed_posting_back_and_the_gateway_wakes(client, schema_url):
    c, conn = client
    closed = approvals.approve(conn, _proposal(conn, "R1", 60_500 * N), OFFICER).outbox_id
    forged = approvals.approve(conn, _proposal(conn, "R2", 40_000 * N), OFFICER).outbox_id
    waiting = approvals.approve(conn, _proposal(conn, "R3", 30_000 * N), OFFICER).outbox_id
    _gateway_stops(conn, closed, "posting.failed", "Fineract refused: loan is closed")
    _gateway_stops(conn, forged, "posting.refused", "refused: approved_by names someone who didn't decide")
    amaka, hadiza = _as(c, "amaka"), _as(c, "hadiza")

    stopped = {p["outbox_id"]: p for p in c.get("/today", headers=hadiza).json()["postings"]["stopped"]}
    assert set(stopped) == {closed, forged}
    assert not stopped[closed]["refused"] and stopped[forged]["refused"]
    assert stopped[closed]["last_error"] == "Fineract refused: loan is closed"
    assert c.get("/summary", headers=hadiza).json()["failed_postings"] == 2

    r = c.post(f"/postings/{closed}/retry", headers=hadiza)
    assert r.status_code == 403 and r.json()["refused"] == "read_only"
    r = c.post(f"/postings/{forged}/retry", headers=amaka)
    assert r.status_code == 409 and r.json()["refused"] == "refused_by_gateway"
    r = c.post(f"/postings/{waiting}/retry", headers=amaka)
    assert r.status_code == 409 and r.json()["refused"] == "not_failed"
    assert c.post("/postings/999999/retry", headers=amaka).status_code == 404

    with psycopg.connect(schema_url, autocommit=True) as listener:
        listener.execute("LISTEN posting_outbox")
        r = c.post(f"/postings/{closed}/retry", headers=amaka)
        assert r.status_code == 200 and r.json() == {"outbox_id": closed, "status": "retrying"}
        woke = [n.payload for n in listener.notifies(timeout=5, stop_after=1)]
    assert woke == [str(closed)]
    row = conn.execute("SELECT status, next_attempt_at, attempts, last_error FROM posting_outbox WHERE id = %s",
                       (closed,)).fetchone()
    assert row == ("pending", None, 1, "Fineract refused: loan is closed")  # the history stays on the row
    last = conn.execute("SELECT actor, action, detail FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert last[:2] == ("amaka", "posting.retry_requested") and last[2]["outbox_id"] == closed
    assert c.post(f"/postings/{closed}/retry", headers=amaka).status_code == 409  # already back in the tray


@pytest.mark.db
def test_today_counts_the_week_and_the_work_waiting(client):
    c, conn = client
    _proposal(conn, "T1", 126_000 * N)
    _proposal(conn, "T2", 875_000 * N, split=5)
    _line(conn, "T3", 36_300 * N)  # nothing proposed: unmatched money
    posted = approvals.approve(conn, _proposal(conn, "T4", 60_500 * N), OFFICER).outbox_id
    t = c.get("/today", headers=_as(c, "hadiza")).json()

    assert t["week"] == {"start": "2026-10-01", "end": "2026-10-07"}  # the seven days ending on the latest line
    assert t["received"] == {"lines": 4, "amount_kobo": (126_000 + 875_000 + 36_300 + 60_500) * N, "duplicates": 0}
    assert t["by_status"]["proposed"]["lines"] == 2 and t["by_status"]["approved"]["lines"] == 1
    assert t["matched_by_rule"] == 3
    assert t["channels"] == [{"channel": "bank_csv", "lines": 4, "by_rule": 3,
                              "amount_kobo": (126_000 + 875_000 + 36_300 + 60_500) * N}]
    assert t["review"] == {"pending": 2, "two_person": 1, "amount_kobo": (126_000 + 875_000) * N,
                           "two_person_above_kobo": approvals.TWO_PERSON_ABOVE_KOBO}
    assert t["unmatched"]["lines"] == 1 and t["unmatched"]["amount_kobo"] == 36_300 * N
    assert sum(b["lines"] for b in t["unmatched"]["bands"]) == 1
    assert t["postings"] | {"stopped": []} == {"posted_today": 0, "waiting": 1, "retrying": 0, "failed": 0,
                                               "stopped": []}
    assert posted and t["postings"]["stopped"] == []
    assert t["agent"] == {"runs": 0, "proposed": 0, "suspense": 0, "failed": 0, "cost_usd": 0.0, "latest": None}


@pytest.mark.db
def test_an_investigation_reads_back_whole_with_what_people_decided_since(client, placed_week):
    from repaydesk.investigator import Allocation, Finding, Run, Triage, Verdict, record

    c, conn = client
    bk, book = placed_week
    open_lines = conn.execute("SELECT id, amount_kobo FROM payment_lines WHERE status = 'new' ORDER BY id LIMIT 2")
    (first, amount), (second, amount2) = open_lines.fetchall()
    loan = next(x.ref for x in book.loans if (v := bk.core.get_loan(x.ref)) and v.status == "active")
    triage = Triage(kind="names_a_business", names=[], business="IFEOMA PHARMACY", group=None, paid_for=None,
                    reference=None, language="english")
    matched = Run(first, triage=triage, steps=[{"tool": "search_borrowers", "input": {"text": "IFEOMA PHARMACY"},
                                                "output": "{\"borrowers\": []}", "error": False, "ms": 62}],
                  verdict=Verdict(outcome="match", allocations=[Allocation(loan_ref=loan, amount_kobo=amount)],
                                  confidence=0.85, reason="Trading name and amount agree.",
                                  findings=[Finding(fact="Owes exactly the amount.", bearing="for")],
                                  candidates=[loan]))
    held = Run(second, verdict=Verdict(outcome="suspense", allocations=[], confidence=0.3,
                                       reason="Two borrowers fit equally.", findings=[], candidates=[]))
    m = record(conn, matched, amount, bk.core)
    s = record(conn, held, amount2, bk.core)
    hadiza = _as(c, "hadiza")

    page = c.get("/runs", headers=hadiza).json()
    assert [r["id"] for r in page["items"]] == [s["run_id"], m["run_id"]]  # newest first
    assert page["counts"] == {"proposed": 1, "excluded": 0, "suspense": 1, "failed": 0} and page["all_runs"] == 2
    assert page["items"][1]["tool_calls"] == 1 and page["items"][1]["proposal_status"] == "pending"
    assert [r["id"] for r in c.get("/runs", headers=hadiza, params={"outcome": "suspense"}).json()["items"]] == [
        s["run_id"]]
    assert c.get("/runs", headers=hadiza, params={"outcome": "guessed"}).status_code == 422

    detail = c.get(f"/runs/{m['run_id']}", headers=hadiza).json()
    assert detail["line"]["id"] == first and detail["triage"]["business"] == "IFEOMA PHARMACY"
    assert detail["steps"][0]["tool"] == "search_borrowers" and detail["verdict"]["reason"].startswith("Trading")
    assert all(ch["ok"] for ch in detail["checks"]) and detail["proposal"]["status"] == "pending"
    approvals.approve(conn, m["proposal_id"], SUPERVISOR)
    after = c.get(f"/runs/{m['run_id']}", headers=hadiza).json()["proposal"]
    assert after["status"] == "approved" and after["decisions"][0]["name"] == "Tunde Bakare"
    assert c.get("/runs/999999", headers=hadiza).status_code == 404
    assert c.get("/runs").status_code == 401
