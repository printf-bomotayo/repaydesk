"""Ingestion against a real Postgres: every line once, duplicates kept and pointed at their original."""

import json
import shutil
import threading
from collections import Counter

import psycopg
import pytest
from fastapi.testclient import TestClient

from repaydesk import db
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.domain import Channel
from repaydesk.ingest import FILE_FOLDERS, ingest_folder, store_line
from repaydesk.synth.writers import sign
from tests.conftest import WEBHOOK_SECRET

pytestmark = pytest.mark.db


def _truth(data):
    return json.loads((data / "truth" / "truth.json").read_text(encoding="utf-8"))["lines"]


def _file_truth(data):
    return [t for t in _truth(data) if not t["source_key"].startswith(Channel.VIRTUAL_ACCOUNT.value)]


def test_files_ingest_once(conn, week_dir):
    data, _ = week_dir
    source = FolderStatementSource(data / "inbox", WEBHOOK_SECRET)

    first = ingest_folder(conn, source)
    assert first and not any(r.skipped for r in first)
    assert {r.path.split("_")[0] for r in first} <= {"northgate", "coastline", "termpay", "cash"}
    assert sum(r.stored for r in first) == len(_file_truth(data))
    assert sum(r.duplicates for r in first) == 0

    again = ingest_folder(conn, source)
    assert all(r.skipped for r in again)  # same content, same hash: nothing re-read
    assert conn.execute("SELECT count(*) FROM payment_lines").fetchone()[0] == len(_file_truth(data))


def test_every_file_line_is_known_by_its_truth_key(conn, week_dir):
    data, _ = week_dir
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    ids = {r[0] for r in conn.execute("SELECT id FROM payment_lines")}
    assert ids == {t["source_key"] for t in _file_truth(data)}


def test_a_resent_statement_becomes_duplicates_of_the_originals(conn, week_dir, tmp_path):
    data, _ = week_dir
    inbox = tmp_path / "inbox"
    for folder in FILE_FOLDERS:
        (inbox / folder).mkdir(parents=True)
    original = sorted((data / "inbox" / "northgate").glob("*.csv"))[2]
    shutil.copy(original, inbox / "northgate" / original.name)
    source = FolderStatementSource(inbox, WEBHOOK_SECRET)
    stored = ingest_folder(conn, source)[0].stored

    # The bank sends the same day again with a different preamble: new file, same lines.
    resent = inbox / "northgate" / original.name.replace(".csv", "_resent.csv")
    resent.write_text(original.read_text(encoding="utf-8").replace("Period:", "Statement period:"), encoding="utf-8")
    result = [r for r in ingest_folder(conn, source) if not r.skipped][0]

    assert result.stored == 0 and result.duplicates == stored
    rows = conn.execute("SELECT id, status, duplicate_of FROM payment_lines WHERE status = 'duplicate'").fetchall()
    assert len(rows) == stored
    assert all(dup_id == f"{orig}#2" for dup_id, _, orig in rows)


def test_two_deliveries_at_the_same_moment_make_one_original(schema_url, week_dir):
    _, book = week_dir
    line = next(x for x in book.lines if x.channel is Channel.VIRTUAL_ACCOUNT)
    with db.connect(schema_url) as c:
        db.migrate(c)
    barrier = threading.Barrier(6)
    errors = []

    def deliver():
        try:
            with db.connect(schema_url) as c:
                barrier.wait()
                for attempt in range(5):
                    try:
                        with c.transaction():
                            store_line(c, line)
                        return
                    except psycopg.errors.UniqueViolation:  # lost the race for the next #n; try again
                        if attempt == 4:
                            raise
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    with db.connect(schema_url) as c:
        statuses = Counter(r[0] for r in c.execute("SELECT status FROM payment_lines"))
    assert statuses == {"new": 1, "duplicate": 5}


def test_ingestion_is_audited_and_the_audit_log_cannot_be_edited(conn, week_dir):
    data, _ = week_dir
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    n = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'statement.ingested'").fetchone()[0]
    assert n == conn.execute("SELECT count(*) FROM statement_files").fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'someone-else'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM audit_log")


# ---- the webhook endpoint ------------------------------------------------------------------


@pytest.fixture
def api(schema_url, monkeypatch):
    monkeypatch.setenv("REPAYDESK_DATABASE_URL", schema_url)
    monkeypatch.setenv("PAYRAIL_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from repaydesk.api import app

    with TestClient(app) as client:
        yield client, schema_url


def _deliveries(data):
    path = next((data / "inbox" / "payrail").glob("*.jsonl"))
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def test_webhook_stores_then_recognises_a_redelivery(api, week_dir):
    client, url = api
    data, _ = week_dir
    d = _deliveries(data)[0]
    first = client.post("/webhooks/payrail", content=d["body"].encode(), headers=d["headers"])
    again = client.post("/webhooks/payrail", content=d["body"].encode(), headers=d["headers"])
    assert first.status_code == 200 and first.json()["outcome"] == "stored"
    assert again.status_code == 200 and again.json()["outcome"] == "duplicate"
    assert again.json()["line_id"] == first.json()["line_id"] + "#2"


def test_webhook_with_a_bad_signature_is_refused_and_stores_nothing(api, week_dir):
    client, url = api
    data, _ = week_dir
    d = _deliveries(data)[0]
    forged = d["body"].replace('"amount":', '"amount":9')
    r = client.post("/webhooks/payrail", content=forged.encode(), headers=d["headers"])
    assert r.status_code == 401 and "amount" not in r.text
    with db.connect(url) as c:
        assert c.execute("SELECT count(*) FROM payment_lines").fetchone()[0] == 0
        assert c.execute("SELECT outcome, signature_ok FROM webhook_deliveries").fetchall() == [("rejected", False)]


def test_webhook_ignores_events_that_are_not_credits(api):
    client, _ = api
    body = json.dumps({"event": "virtual_account.created", "id": "evt_x", "data": {}}).encode()
    r = client.post("/webhooks/payrail", content=body, headers={"x-payrail-signature": sign(WEBHOOK_SECRET, body)})
    assert r.status_code == 200 and r.json()["outcome"] == "ignored"


def test_replaying_the_week_matches_the_truth(api, week_dir):
    client, url = api
    data, _ = week_dir
    outcomes = Counter()
    for d in _deliveries(data):
        r = client.post("/webhooks/payrail", content=d["body"].encode(), headers=d["headers"])
        outcomes[r.json()["outcome"]] += 1
    truth = [t for t in _truth(data) if t["source_key"].startswith(Channel.VIRTUAL_ACCOUNT.value)]
    assert outcomes["duplicate"] == sum(t["case"] == "duplicate_delivery" for t in truth)
    assert outcomes["stored"] == len(truth) - outcomes["duplicate"]
    with db.connect(url) as c:
        ids = {r[0] for r in c.execute("SELECT id FROM payment_lines")}
    assert ids == {t["source_key"] for t in truth}
