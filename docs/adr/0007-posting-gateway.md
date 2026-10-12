# 7. How the posting gateway posts

Date: 2026-10-09. Status: accepted.

## Context

ADR 0002 makes the posting gateway the only writer to core banking and the ledger. An approved
match reaches it through `posting_outbox`, written in the same transaction as the approval
(ADR 0005). The gateway then has to write to two systems that know nothing of each other,
Fineract and Odoo, with no transaction spanning them. Either can be slow or down, the gateway
can die between the two writes, and a bank may run more than one copy of it.

## Decision

- **One row at a time, locked while it posts.** The gateway takes the oldest ready row with
  `FOR UPDATE SKIP LOCKED` and holds the lock while it talks to Fineract and Odoo. What happened
  is written in that same transaction, with its audit row. A crash releases the lock and leaves
  the row pending; two gateways each take a different row.
- **Look before writing.** Each write is looked up by its idempotency key first: the repayment
  by Fineract's transaction external id, the journal entry by its reference. A retry finds what
  an earlier attempt posted instead of posting again. A split posts one repayment per loan,
  keyed `RD-P110-1`, `RD-P110-2`; a single loan keeps the plain key, `RD-P110`.
- **Recheck the approval.** Before anything moves, the gateway rereads the proposal, the line and
  the decisions, and applies ADR 0005's rules itself. An outbox row the decisions don't support
  is refused and never posted, whoever wrote it.
- **Fineract first, and the ledger follows its split.** The repayment goes to Fineract; the
  journal entry then credits principal, interest, charges and overpayment exactly as Fineract
  applied it, so the two books can't disagree about the split.
- **Retry what might pass; stop what won't.** A timeout, a 5xx or a login problem is retried with
  backoff (10 seconds up to every 10 minutes), indefinitely, with the error on the row. Fineract
  or Odoo refusing the posting itself (a closed loan, a date before disbursement) fails the row:
  the gateway stops and a person decides. Anything already posted in Fineract is kept on the row.
- **Woken by Postgres.** A trigger raises `NOTIFY posting_outbox` when a row is ready, so an
  approval posts within seconds; a 15-second poll catches retries and missed notifications.
- **Each service has its own Fineract login.** The seed creates `repaydesk-read`
  (`ALL_FUNCTIONS_READ`) for the API, the directory and rule matching, and `repaydesk-gateway`
  (`READ_LOAN`, `REPAYMENT_LOAN`, `READ_PAYMENTTYPE`) for the gateway. The admin login is for the
  seed and for people. Odoo has a read-only accounting group (the ledger MCP server's login, ADR
  0008), but nothing between that and full accounting rights that would let a login post only
  repayments, so the seed and the gateway share its admin login, and nothing else in Repaydesk has it.

## Consequences

- A posting can be in Fineract and not yet in Odoo for as long as Odoo is down. The row says so
  (pending, with the error and Fineract's repayment), and the posting completes when Odoo answers.
- A failed row needs a person. Milestone 2's Today page lists them; until then they show on the
  payment's case file.
- The chart codes live in two places, `adapters/chart.py` and the gateway's `Chart`. A code
  missing from the ledger fails the posting; it can't land on the wrong account.
- The gateway holds a database transaction open during HTTP calls, a second or two each. At a
  bank's volume, several gateways share the work through `SKIP LOCKED`.
