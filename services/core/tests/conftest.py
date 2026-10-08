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
