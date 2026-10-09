# Posting gateway

The only component that writes to core banking and the ledger ([ADR 0002](../../docs/adr/0002-one-writer.md)).
It drains `posting_outbox`, which the API fills when people approve a match, and posts each row
to Fineract, then to Odoo. How and why: [ADR 0007](../../docs/adr/0007-posting-gateway.md).

```
src/Repaydesk.Gateway/
  Program.cs     host, settings, tracing; `--healthy` answers the container health check
  Drainer.cs     the loop: drain, then wait for NOTIFY posting_outbox or the poll
  Outbox.cs      one row: lock, recheck, post to both books, record it with its audit row
  Mandate.cs     the approval rules (ADR 0005), checked against the decisions themselves
  Ledger.cs      the chart of accounts and the journal entry built from Fineract's split
  Fineract.cs    repayments by external id; which errors are retried and which stop
  Odoo.cs        journal entries over JSON-RPC, found again by reference
tests/           rules, the Fineract client against canned responses, and the drain against Postgres
```

Run the tests with `python scripts/dev.py gateway` (the database tests use the compose Postgres;
without it they skip). In compose it runs as the `gateway` service, with its own Fineract login,
`repaydesk-gateway`, which may read a loan and post a repayment and nothing else.

What a row can become:

| Status | Meaning |
|---|---|
| `pending` | Waiting, or waiting to retry: `next_attempt_at` and `last_error` say when and why |
| `posted` | In Fineract (`core_transactions`) and in the ledger (`ledger_entry`); the line is `posted` |
| `failed` | Stopped for a person: refused by core banking or the ledger, or the approval didn't hold |
