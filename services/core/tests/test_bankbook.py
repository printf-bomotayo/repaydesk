"""The investigator's lookups and the MCP servers that serve them: what they find, what they hide,
and that they can't change anything."""

import json
import re
from datetime import date

import anyio
import psycopg
import pytest
from mcp.client.client import Client

from repaydesk import directory
from repaydesk.adapters.memory import MemoryLedger
from repaydesk.adapters.ports import JournalEntry, JournalLine
from repaydesk.adapters.statements import FolderStatementSource
from repaydesk.bankbook import Bankbook, NotFound, read_only
from repaydesk.ingest import ingest_folder
from repaydesk.match import RuleMatcher, run
from repaydesk.privacy import mask_account, mask_phone, mask_text, phones_in
from repaydesk.servers import bankbook as bankbook_server
from repaydesk.servers import ledger as ledger_server
from tests.conftest import WEBHOOK_SECRET
from tests.test_records import _core

# ---- what a model may read -------------------------------------------------------------------


def test_phone_and_account_numbers_are_masked_and_words_kept():
    narration = "POS 2TPOSH03 539912******8841 ref typed 0700 512 8830 from acct 0123456789 NGT282773322650"
    masked = mask_text(narration)
    assert masked == "POS 2TPOSH03 539912******8841 ref typed 0700 *** 8830 from acct ****6789 NGT282773322650"
    assert mask_text("+2347005128830 paid LN-2026-001209") == "0700 *** 8830 paid LN-2026-001209"
    assert phones_in("ref 07005128830", "call +234 700 512 8830", None) == ["07005128830"]
    assert mask_phone("07005566957") == "0700 *** 6957" and mask_account("9940217315") == "****7315"
    assert mask_text(None) is None and mask_phone("") == ""


# ---- the bankbook, over a generated week -------------------------------------------------------

db_test = pytest.mark.db


@pytest.fixture
def book_and_data(conn, week_dir):
    data, book = week_dir
    core = _core(book)
    directory.sync(conn, core)
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
    return Bankbook(read_only(conn), core), book


def _texts(result) -> str:
    return json.dumps(result)


PHONE_DIGITS = re.compile(r"(?<!\d)0[789][01]\d{8}(?!\d)")


@db_test
def test_names_are_found_misspelt_initialled_reordered_or_by_trading_name(book_and_data):
    bk, book = book_and_data
    someone = next(b for b in book.borrowers if b.business_name)
    first, last = someone.first_name, someone.last_name

    def refs(text, **kw):
        return [b["borrower_ref"] for b in bk.search_borrowers(text, **kw)["borrowers"]]

    assert someone.ref in refs(f"{last} {first}".upper())[:3]  # reordered, as a bank prints it
    assert someone.ref in refs(f"{last} {first[0]}")  # initialled
    assert someone.ref in refs(someone.business_name.upper())[:3]  # their trading name
    assert someone.ref in refs(f"TRF FROM {someone.business_name.upper()}/")[:5]  # buried in a narration
    found = bk.search_borrowers(someone.full_name)["borrowers"][0]
    assert found["borrower_ref"] == someone.ref and found["match_score"] == 1.0
    assert found["loans"] and found["loans"][0]["outstanding"]["naira"].startswith("₦")
    assert all(PHONE_DIGITS.search(_texts(b)) is None for b in bk.search_borrowers(someone.full_name)["borrowers"])
    other_branch = next(b.branch_code for b in book.borrowers if b.branch_code != someone.branch_code)
    assert someone.ref not in refs(someone.full_name, branch_code=other_branch)
    with pytest.raises(NotFound):
        bk.search_borrowers("a")


@db_test
def test_a_phone_typed_at_a_terminal_finds_its_borrower_without_showing_the_number(book_and_data, conn):
    bk, book = book_and_data
    line_id, typed = conn.execute(
        "SELECT l.id, l.customer_reference FROM payment_lines l JOIN borrowers b ON b.phone = l.customer_reference "
        "WHERE l.channel = 'pos' ORDER BY l.id LIMIT 1").fetchone()
    line = bk.payment_line(line_id)
    assert line["has_phone"] and typed not in _texts(line) and line["reference_typed"].endswith(typed[-4:])
    found = bk.borrowers_by_line_phone(line_id)
    assert found["phones_on_line"] == 1 and len(found["borrowers"]) >= 1
    assert typed not in _texts(found)
    assert {b.ref for b in book.borrowers if b.phone == typed} == {b["borrower_ref"] for b in found["borrowers"]}


@db_test
def test_a_loan_is_read_live_with_the_amounts_that_would_fit(book_and_data):
    bk, book = book_and_data
    loan = next(x for x in book.loans)
    got = bk.loan(loan.ref)
    assert got["read_live_from_core_banking"] and got["borrower_ref"] == loan.borrower_ref
    assert got["borrower_name"] == next(b.full_name for b in book.borrowers if b.ref == loan.borrower_ref)
    if got["next_instalments"]:
        assert got["next_instalments"][0]["still_owed"] in got["amounts_that_would_fit"]
    with pytest.raises(NotFound, match="No loan LN-2099-000001"):
        bk.loan("LN-2099-000001")


@db_test
def test_history_shows_how_a_loan_was_paid_and_who_paid_it(book_and_data, conn):
    bk, book = book_and_data
    loan_ref, payer = conn.execute(
        "SELECT a->>'loan_ref', l.payer_name FROM proposals p JOIN payment_lines l ON l.id = p.line_id "
        "CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a "
        "WHERE p.kind = 'match' AND l.payer_name IS NOT NULL ORDER BY p.id LIMIT 1").fetchone()
    history = bk.payment_history(loan_ref)
    assert payer in [h["payer_name"] for h in history["lines_matched_by_repaydesk"]]
    earlier = bk.find_payments(payer)["payments"]
    assert any(loan_ref in p["matched_to"] for p in earlier)
    paid_before = book.history[0]
    posted = bk.payment_history(paid_before.loan_ref)["repayments_in_core_banking"]
    assert {"on": paid_before.paid_on.isoformat(), "kobo": paid_before.amount} in [
        {"on": r["on"], "kobo": r["amount"]["kobo"]} for r in posted]


@db_test
def test_the_session_refuses_writes(book_and_data):
    bk, _ = book_and_data
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        bk.conn.execute("DELETE FROM proposals")


# ---- the MCP servers ---------------------------------------------------------------------------


@db_test
def test_the_bankbook_server_offers_only_reads_and_says_what_it_couldnt_find(book_and_data):
    bk, book = book_and_data
    server = bankbook_server.build(bk)
    someone = book.borrowers[0]

    async def session():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            found = await client.call_tool("search_borrowers", {"text": someone.full_name})
            missing = await client.call_tool("get_loan", {"loan_ref": "LN-2099-000001"})
            bad = await client.call_tool("get_loan", {})
            return tools, found, missing, bad

    tools, found, missing, bad = anyio.run(session)
    assert {t.name for t in tools} == {"get_payment_line", "search_borrowers", "borrowers_by_line_phone",
                                        "get_borrower", "get_group", "get_loan", "payment_history", "find_payments"}
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
    assert all(len(t.description) > 60 for t in tools)
    assert not found.is_error and json.loads(found.content[0].text)["borrowers"][0]["borrower_ref"] == someone.ref
    assert missing.is_error and "No loan LN-2099-000001 in core banking." in missing.content[0].text
    assert bad.is_error  # a missing argument is the model's to correct


def test_the_ledger_server_reads_entries_and_balances():
    ledger = MemoryLedger()
    ledger.ensure_setup("Demo Microfinance Bank", {"bank_csv": ("Northgate Bank", "1023456789")})
    ledger.post_entry(JournalEntry(date(2026, 10, 10), "RD-P219", "Repayment RD-P219", "RPY", (
        JournalLine("110100", debit=3_360_000), JournalLine("131000", credit=2_800_000, label="principal"),
        JournalLine("410000", credit=560_000, label="interest"))))
    server = ledger_server.build(ledger_server.Books(ledger))

    async def session():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            entry = await client.call_tool("find_journal_entry", {"reference": "RD-P219"})
            none = await client.call_tool("find_journal_entry", {"reference": "RD-P999"})
            balances = await client.call_tool("account_balances", {})
            return tools, entry, none, balances

    tools, entry, none, balances = anyio.run(session)
    assert all(t.annotations.read_only_hint for t in tools)
    got = json.loads(entry.content[0].text)
    assert [(x["account"], x["credit"]["kobo"]) for x in got["lines"]] == [("110100", 0), ("131000", 2_800_000),
                                                                            ("410000", 560_000)]
    assert got["lines"][1]["account_name"] == "Loans to customers: principal"
    assert none.is_error and "No journal entry with reference RD-P999" in none.content[0].text
    by_code = {a["account"]: a["balance"]["kobo"] for a in json.loads(balances.content[0].text)["accounts"]}
    assert by_code["110100"] == 3_360_000 and by_code["131000"] == -2_800_000
