"""The web app's reads: paged, filtered, and telling a line's whole story."""

from datetime import date
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from repaydesk import approvals, db, staff
from repaydesk.adapters.memory import MemoryCoreBanking
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.ingest import ingest_folder, store_line
from repaydesk.match import RuleMatcher, run
from repaydesk.staff import LIMITS, Staff
from repaydesk.synth.names import BRANCHES, PRODUCTS
from tests.conftest import WEBHOOK_SECRET

pytestmark = pytest.mark.db

PASSWORD = "test-staff-password"
AS_OF = date(2026, 10, 8)


@pytest.fixture
def loaded(schema_url, week_dir, monkeypatch):
    """A week ingested, its webhooks stored, rules run, and one line approved."""
    data, book = week_dir
    with db.connect(schema_url) as conn:
        db.migrate(conn)
        staff.ensure_demo_staff(conn, PASSWORD)
        ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
        for line in book.lines:
            if line.channel.value == "virtual_account":
                with conn.transaction():
                    store_line(conn, line)
        core = MemoryCoreBanking(AS_OF)
        core.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
        for b in book.borrowers:
            core.create_borrower(b)
        for loan in book.loans:
            core.create_loan(loan)
        for r in book.history:
            core.post_repayment(r.loan_ref, r.paid_on, r.amount, r.reference)
        run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
        pid, line_id = conn.execute(
            "SELECT p.id, p.line_id FROM proposals p JOIN payment_lines l ON l.id = p.line_id "
            "WHERE p.kind = 'match' AND l.amount_kobo <= 50000000 ORDER BY p.id LIMIT 1").fetchone()
        approvals.approve(conn, pid, Staff("amaka", "Amaka Nwosu", "officer", LIMITS["officer"]))

    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("STAFF_DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from repaydesk.api import app

    with TestClient(app) as c:
        token = c.post("/auth/sign-in", json={"staff_id": "hadiza", "password": PASSWORD}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c, line_id


def test_reads_need_a_signed_in_person(loaded):
    c, _ = loaded
    for path in ("/lines", "/lines/x", "/ingestion", "/audit", "/summary"):
        assert c.get(path, headers={"Authorization": ""}).status_code == 401, path
    assert c.get("/config", headers={"Authorization": ""}).json()["bank_name"]


def test_lines_page_filter_and_count(loaded):
    c, _ = loaded
    everything = c.get("/lines", params={"limit": 100}).json()
    total = everything["total"]
    counts = everything["counts"]
    assert total == sum(counts.values()) - counts["duplicate"] > 100  # copies aren't payments
    assert len(everything["items"]) == 100
    assert all(i["status"] != "duplicate" for i in everything["items"])

    pages, offset = [], 0
    while True:
        page = c.get("/lines", params={"limit": 40, "offset": offset}).json()
        pages += [i["id"] for i in page["items"]]
        if len(page["items"]) < 40:
            break
        offset += 40
    assert len(pages) == len(set(pages)) == total  # no line twice, none missing

    proposed = c.get("/lines", params={"status": "proposed", "limit": 200}).json()
    assert proposed["total"] == everything["counts"]["proposed"]
    assert all(i["status"] == "proposed" and i["proposed_by"] for i in proposed["items"])
    assert c.get("/lines", params={"status": "nonsense"}).status_code == 422

    cash = c.get("/lines", params={"channel": "branch_cash"}).json()
    assert cash["total"] and all(i["channel"] == "branch_cash" for i in cash["items"])


def test_search_finds_a_line_by_the_loan_it_was_matched_to(loaded):
    c, line_id = loaded
    loan_ref = c.get(f"/lines/{line_id}").json()["proposals"][0]["allocations"][0]["loan_ref"]
    found = c.get("/lines", params={"q": loan_ref.lower()}).json()
    assert line_id in [i["id"] for i in found["items"]]


def test_a_line_tells_its_whole_story(loaded):
    c, line_id = loaded
    line = c.get(f"/lines/{line_id}").json()
    assert line["status"] == "approved"
    assert line["arrived_by"]["kind"] in ("file", "webhook")
    assert [d["name"] for d in line["proposals"][0]["decisions"]] == ["Amaka Nwosu"]
    assert line["posting"]["idempotency_key"] == f"RD-P{line['proposals'][0]['id']}"
    actions = [t["action"] for t in line["trail"]]
    assert actions[0] == "proposal.created" and actions[-2:] == ["proposal.approved", "posting.queued"]
    assert c.get("/lines/bank_csv:NOPE").status_code == 404


def test_a_duplicate_points_at_its_original_and_back(loaded):
    c, _ = loaded
    dup = c.get("/lines", params={"status": "duplicate"}).json()["items"]
    if not dup:
        pytest.skip("this week has no duplicates")
    original = c.get(f"/lines/{dup[0]['duplicate_of']}").json()
    assert dup[0]["id"] in original["duplicates"]
    # Through the web app's /api prefix, with the id's '#' and ':' encoded as the browser sends them.
    copy = c.get("/api/lines/" + quote(dup[0]["id"], safe=""))
    assert copy.status_code == 200 and copy.json()["id"] == dup[0]["id"]


def test_ingestion_reconciles_files_to_lines(loaded):
    c, _ = loaded
    ing = c.get("/ingestion").json()
    assert ing["files"] and all(f["line_count"] == f["stored"] + f["duplicates"] for f in ing["files"])
    assert sum(s["files"] for s in ing["sources"]) == len(ing["files"])


def test_the_audit_log_pages_by_id_and_filters(loaded):
    c, _ = loaded
    first = c.get("/audit", params={"limit": 50}).json()
    assert len(first["items"]) == 50 and first["next_before"]
    ids = [i["id"] for i in first["items"]]
    assert ids == sorted(ids, reverse=True)
    second = c.get("/audit", params={"limit": 50, "before": first["next_before"]}).json()
    assert max(i["id"] for i in second["items"]) < min(ids)

    approved = c.get("/audit", params={"action": "proposal.approved"}).json()["items"]
    assert len(approved) == 1 and approved[0]["actor"] == "amaka" and approved[0]["actor_name"] == "Amaka Nwosu"
    assert "proposal.created" in first["actions"]
    by_rule = c.get("/audit", params={"action": "proposal.*", "actor": "rule:virtual_account"}).json()["items"]
    assert by_rule and all(i["actor"] == "rule:virtual_account" for i in by_rule)
