"""Write a week to files, read it back with the production parsers, and compare."""

import json
from collections import Counter
from datetime import date

import pytest

from repaydesk.adapters.statements import FolderStatementSource, UnreadableStatement
from repaydesk.adapters.webhooks import PayRailWebhooks
from repaydesk.synth.generator import Generator
from repaydesk.synth.writers import sign, source_key, write_week

SECRET = "test-secret"


@pytest.fixture(scope="module")
def week(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    book = Generator(7, date(2026, 10, 8)).generate(600)
    write_week(book, data, SECRET)
    source = FolderStatementSource(data / "inbox", SECRET)
    parsed = [line for path in source.pending() for line in source.read(path)]
    return book, data, parsed


def test_every_credit_comes_back_once(week):
    book, _, parsed = week
    assert len(parsed) == len(book.lines)
    assert len({p.line_id for p in parsed}) == len(parsed)


def test_amounts_dates_and_references_survive(week):
    book, _, parsed = week
    want = Counter((source_key(x), x.amount, x.value_date) for x in book.lines)
    got = Counter((p.line_id.split("#")[0], p.amount, p.value_date) for p in parsed)
    assert got == want


def test_debits_are_skipped(week):
    book, _, parsed = week
    debit_refs = {d.bank_reference for d in book.debits}
    assert not debit_refs & {p.bank_reference for p in parsed}


def test_payer_names_are_extracted_from_transfers(week):
    book, _, parsed = week
    by_key = {p.line_id: p for p in parsed}
    for x in book.lines:
        if x.channel.value.startswith("bank_") and x.payer_name:
            assert by_key[source_key(x)].payer_name == x.payer_name


def test_truth_keys_match_parsed_ids(week):
    _, data, parsed = week
    truth = json.loads((data / "truth" / "truth.json").read_text(encoding="utf-8"))
    assert {t["source_key"] for t in truth["lines"]} == {p.line_id for p in parsed}


def test_redelivered_webhooks_get_their_own_id(week):
    book, _, parsed = week
    repeats = [p for p in parsed if "#" in p.line_id]
    assert len(repeats) == sum(t.case == "duplicate_delivery" for t in book.truth)
    assert all(p.channel.value == "virtual_account" for p in repeats)


def test_tampered_webhook_is_rejected(week, tmp_path):
    _, data, _ = week
    src = next((data / "inbox" / "payrail").glob("*.jsonl"))
    first = json.loads(src.read_text(encoding="utf-8").splitlines()[0])
    first["body"] = first["body"].replace('"amount":', '"amount":1')
    (tmp_path / "payrail").mkdir()
    (tmp_path / "payrail" / "x.jsonl").write_text(json.dumps(first) + "\n", encoding="utf-8")
    source = FolderStatementSource(tmp_path, SECRET)
    with pytest.raises(UnreadableStatement, match="bad signature"):
        list(source.read(tmp_path / "payrail" / "x.jsonl"))


def test_webhook_verify_and_ignore_other_events():
    hooks = PayRailWebhooks(SECRET)
    body = json.dumps({"event": "transfer.reversed", "id": "evt_1", "data": {}}).encode()
    assert hooks.verify(body, sign(SECRET, body))
    assert not hooks.verify(body, sign("other", body))
    assert hooks.to_line(body) is None
