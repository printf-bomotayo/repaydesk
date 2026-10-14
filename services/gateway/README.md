# Gateway

The only component that writes to core banking and the ledger, or talks to borrowers
([ADR 0002](../../docs/adr/0002-one-writer.md)). It drains two outboxes:

- `posting_outbox`, which the API fills when people approve a match: each row is posted to
  Fineract, then to Odoo. How and why: [ADR 0007](../../docs/adr/0007-posting-gateway.md).
- Approved `collection_messages`, which a portfolio manager approves: each is sent to the
  messaging provider (the simulator, in the prototype), inside the collections policy's contact
  hours.

```
src/Repaydesk.Gateway/
  Program.cs     host, settings, tracing; `--healthy` answers the container health check
  Drainer.cs     the loop: drain both outboxes, then wait for NOTIFY posting_outbox / message_outbox or the poll
  Outbox.cs      one posting: lock, recheck, post to both books, record it with its audit row
  Messages.cs    one message: lock, recheck, send inside contact hours, record it with its audit row
  Mandate.cs     the approval rules (ADR 0005), checked against the decisions themselves
  Ledger.cs      the chart of accounts and the journal entry built from Fineract's split
  Fineract.cs    repayments by external id; which errors are retried and which stop
  Odoo.cs        journal entries over JSON-RPC, found again by reference
tests/           rules, the Fineract and messaging clients against canned responses, and both drains against Postgres
```

Run the tests with `python scripts/dev.py gateway` (the database tests use the compose Postgres;
without it they skip). In compose it runs as the `gateway` service, with its own Fineract login,
`repaydesk-gateway`, which may read a loan and post a repayment and nothing else.

What a posting can become:

| Status | Meaning |
|---|---|
| `pending` | Waiting, or waiting to retry: `next_attempt_at` and `last_error` say when and why |
| `posted` | In Fineract (`core_transactions`) and in the ledger (`ledger_entry`); the line is `posted` |
| `failed` | Stopped for a person: refused by core banking or the ledger, or the approval didn't hold |

What an approved message can become. Before sending, the gateway checks again what may have
changed since approval: the approver's right to approve messages, the policy checks, two messages a
week, and a promise to pay made since.

| Status | Meaning |
|---|---|
| `approved` | Waiting for `send_after`, for contact hours (08:00 to 18:00 Lagos, Monday to Saturday), or to retry (`next_attempt_at`, `error`) |
| `sent` | Accepted by the provider: `provider_id`. The provider dedupes on `CM-<id>`, so a send repeated after a crash is the same message |
| `failed` | Stopped for a person: refused by the provider (a number that can't receive), or the recheck didn't hold |
