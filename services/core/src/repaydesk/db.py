"""Repaydesk's own Postgres database: connection and migrations.

Migrations are plain SQL files in repaydesk/migrations/, applied in name order, each once, each in
its own transaction. No ORM: the queries are few and worth reading.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def connect(url: str) -> psycopg.Connection:
    # Autocommit, with explicit conn.transaction() blocks wherever several statements must land together.
    return psycopg.connect(url, autocommit=True)


def migrate(conn: psycopg.Connection, folder: Path = MIGRATIONS) -> list[str]:
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, "
                     "applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(folder.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied


def audit(conn: psycopg.Connection, actor: str, action: str, subject: str, detail: dict | None = None,
          trace_id: str | None = None) -> None:
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO audit_log (actor, action, subject, detail, trace_id) VALUES (%s, %s, %s, %s, %s)",
        (actor, action, subject, Jsonb(detail or {}), trace_id),
    )
