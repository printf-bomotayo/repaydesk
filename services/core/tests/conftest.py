"""Shared fixtures.

Database tests run against a real Postgres, each in its own throwaway schema, so they see the
real constraints, indexes and triggers. Set REPAYDESK_TEST_DATABASE_URL to run them (CI does;
locally `python scripts/dev.py test` points it at the compose database). Without it they skip.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from urllib.parse import quote

import psycopg
import pytest

from repaydesk import db
from repaydesk.synth.generator import Generator
from repaydesk.synth.writers import write_week

WEBHOOK_SECRET = "test-secret"


@pytest.fixture
def schema_url():
    url = os.getenv("REPAYDESK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("REPAYDESK_TEST_DATABASE_URL not set")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in url else "?"
    yield f"{url}{sep}options={quote(f'-csearch_path={schema}')}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def conn(schema_url):
    c = db.connect(schema_url)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture(scope="session")
def week_dir(tmp_path_factory):
    """A small generated week on disk, shared by the tests that only read it."""
    data = tmp_path_factory.mktemp("week")
    book = Generator(11, date(2026, 10, 8)).generate(800)
    write_week(book, data, WEBHOOK_SECRET)
    return data, book


def _core(book):
    """The generated book loaded into the in-memory core banking adapter, history and all."""
    from repaydesk.adapters.memory import MemoryCoreBanking
    from repaydesk.synth.names import BRANCHES, PRODUCTS

    core = MemoryCoreBanking(date(2026, 10, 8))  # the generated week's as-of date
    core.ensure_setup([b for b, _ in BRANCHES], PRODUCTS)
    for b in book.borrowers:
        core.create_borrower(b)
    for g in book.groups:
        core.create_group(g)
    for loan in book.loans:
        core.create_loan(loan)
    for r in book.history:
        core.post_repayment(r.loan_ref, r.paid_on, r.amount, r.reference, channel=r.channel)
    return core


@pytest.fixture
def placed_week(conn, schema_url, week_dir):
    """A generated week as the agent finds it: the book in core banking and the directory, the
    week's files ingested, rules run. Returns a read-only bankbook over it, and the book."""
    from repaydesk import directory
    from repaydesk.adapters.statements import FolderStatementSource
    from repaydesk.bankbook import Bankbook, read_only
    from repaydesk.ingest import ingest_folder
    from repaydesk.match import RuleMatcher, run

    data, book = week_dir
    core = _core(book)
    directory.sync(conn, core)
    ingest_folder(conn, FolderStatementSource(data / "inbox", WEBHOOK_SECRET))
    run(conn, RuleMatcher(core, {x.virtual_account: x.ref for x in book.loans}.get))
    reader = db.connect(schema_url)  # a separate session, read-only as the agent's is
    yield Bankbook(read_only(reader), core), book
    reader.close()
