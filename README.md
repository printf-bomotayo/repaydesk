# Repaydesk

**Prototype.** Repayment reconciliation and collections for a microfinance bank, rebuilt
agent-first: rules do the certain work, agents investigate the uncertain work, and people
approve everything that moves money or reaches a customer.

It runs on open-source finance systems: **Apache Fineract** as core banking and **Odoo
Community** as the ledger. Every borrower, loan and payment in it is synthetic.

> Status: milestones 1 and 2 of 6 done (tags `m1`, `m2`); milestone 3 next. Built in chunks, each verified and logged in
> [`CHUNK-LOG.md`](CHUNK-LOG.md). See [the roadmap](#roadmap).

## The problem

A microfinance bank lending to traders at volume receives repayments five ways: transfers
into collection accounts, per-loan virtual accounts, agents' POS terminals, cash at branch
counters, and its app. Each arrives in its own format. Each has to land on the right loan.
The ones that don't say clearly whose they are, such as `NIP/OPAY/MAMA CHIKA PROVISIONS/LN
RPY`, a cash slip with a misspelt name, or one transfer from a group leader covering six
members, go to a suspense account that someone clears by hand, while the borrower looks
late.

Repaydesk takes those lines in, matches what rules can match with certainty, has an agent
investigate the rest with read-only access to core banking, and puts every proposed match in
front of a person. Only an approved match is posted, once, by one service.

## Run it

Needs Docker (8 GB of memory for containers) and Python 3.12.

```sh
python scripts/dev.py up      # or: make up
```

The first run takes 10 to 15 minutes, most of it loading loans into Fineract. It creates `.env` with random passwords, starts the
stack, and seeds it. It sets up branches, loan products and 2,000 borrowers with about
2,100 loans and their repayment history in Fineract, sets up the chart of accounts and
collection journals in Odoo, and writes a week of incoming payments to `data/inbox/`.

| What | Where |
|---|---|
| **Repaydesk** | http://127.0.0.1:18082 (sign in as `amaka` or `kunle`, officers; `tunde`, supervisor; `hadiza`, auditor; password `STAFF_DEMO_PASSWORD` in `.env`) |
| Fineract API | http://127.0.0.1:18080/fineract-provider/api/v1 (tenant `default`); Swagger at `/fineract-provider/swagger-ui/index.html` |
| Fineract staff screens | http://127.0.0.1:18081 (Mifos X web app; login `mifos` / `password`, tenant `default`) |
| Odoo ledger | http://127.0.0.1:18069 (database `ledger`, login `admin` / `admin`; Invoicing → Accounting → Journal Entries) |
| Jaeger traces | http://127.0.0.1:16686 |
| Repaydesk API | http://127.0.0.1:18000/docs (same logins; `POST /auth/sign-in`, then **Authorize** with the token) |
| Postgres | 127.0.0.1:15432 |
| The week's payment files | `data/inbox/` |

To see milestone 2 end to end, run `python scripts/dev.py demo`: a new bank statement arrives, is
ingested and matched by rule, Amaka and Tunde approve through the API, the gateway posts, and the
demo finds each repayment in Fineract and each journal entry in Odoo. Then open **Today**.
[`docs/demo.md`](docs/demo.md) is the same story to click through, for showing someone.

Other tasks: `seed`, `ingest`, `match`, `score`, `demo`, `down`, `reset`, `test`, `itest`, `lint`, `urls`,
and for the web app `web` (live reload on http://127.0.0.1:5173, needs Node 24) and `webtest`
(`python scripts/dev.py <task>`).

On Windows, use `127.0.0.1` rather than `localhost`; `localhost` tries IPv6 first and adds
two seconds to every call.

## What's in the week's files

| Folder | Source | Format |
|---|---|---|
| `northgate/` | Transfers into a collection account | Bank CSV with a preamble, one file a day |
| `coastline/` | Transfers into a second collection account | SWIFT MT940, wrapped `:86:` narrations |
| `payrail/` | Per-loan virtual accounts | Signed webhook deliveries (HMAC-SHA512), some redelivered |
| `termpay/` | Agents' POS terminals | Settlement CSV; the agent may type a phone, a loan ref or nothing |
| `branch-cash/` | Branch counters | Teller deposit sheets, handwritten names |

The cases are mixed in at known rates. Some lines carry a clean loan reference. Others
carry a typo, a name only, a trading name or a relative's name. Some are a group leader
paying for the group, or one transfer covering two loans. Some are credits that aren't
repayments, and a few are lines nobody could place. `data/truth/truth.json` records
what each line really is; it is for evals only and nothing an agent can reach reads it.

## Architecture

```
statement files ─┐                         ┌─► approval queue ─► posting gateway ─┬─► Fineract (repayments)
webhooks ────────┼─► ingestion ─► rules ───┤        ▲   (people)      (.NET, the   ├─► Odoo (journals)
POS, cash ───────┘   (dedupe)              └─► investigator agent      only writer) └─► SMS / WhatsApp (sim)
                                                read-only MCP tools ──► Fineract, Odoo, directory
              OpenTelemetry from every service ─► collector ─► Jaeger (+ Langfuse for model calls)
```

Built so far (milestone 1):

- **Compose stack.** Postgres with one database per system, Fineract, Odoo, the
  OpenTelemetry Collector and Jaeger.
- **Adapters** behind contracts (`services/core/src/repaydesk/adapters/ports.py`).
  `CoreBanking` covers Fineract. `Ledger` covers Odoo. `StatementSource` reads the five
  formats. `PaymentWebhooks` verifies signatures. `Messaging` has an in-memory version for
  now.
- **Contract tests** that pass against both the in-memory reference adapters and the real
  Fineract and Odoo. A bank's own core banking adapter is ready when it passes them too.
- **Synthetic generator** for borrowers, groups, loans, schedules, history and the week's
  messy lines, with the truth kept apart.
- **Repaydesk's database** with an append-only audit log enforced by a trigger.
- **Tracing**: the seed is one trace in Jaeger, with a span per borrower and loan loaded.
- **Fineract's staff screens** (chunk 3a): the Mifos X web app, so a borrower, their schedule,
  arrears and every repayment (with its external id) can be opened in core banking itself.
- **Ingestion** (chunk 2): `python scripts/dev.py ingest` takes in the week's statement files,
  then delivers the provider's webhooks to the API one request at a time. Each line is stored
  once. A redelivered webhook or a resent statement is kept as a duplicate pointing at its
  original, and the database's unique index decides, not a check in code. A forged webhook gets a 401
  and stores nothing.
- **Rule matching** (chunk 3): `python scripts/dev.py match` proposes only what is certain. That
  covers a loan's own virtual account, a quoted loan reference backed by the borrower's surname and an
  amount due or the same branch, a typed phone with one active loan, and known non-repayment
  credits. `python scripts/dev.py score` checks every proposal against the truth file. Rules cover
  about 60% of lines and have made no wrong proposal across 100 generated books (35,053 lines). The rest
  waits for the investigator agent.
- **Approvals** (chunk 4): staff sign in and work a review queue, largest payment first.
  One approval settles a payment up to ₦500,000. Above that, two different people approve, and
  one of them must have a limit that covers the amount: officers ₦500,000, supervisors ₦5,000,000,
  auditors read only. A rejection needs a reason and hands the line to the agent; rules never
  propose it again. An approved match goes to the posting outbox in the same transaction, with the
  idempotency key the gateway will post under. See [ADR 0005](docs/adr/0005-approval-rules.md).
- **The web app** (chunk 5, `services/web`): React and TypeScript, served by nginx on port 18082,
  which passes `/api` to the API on the same origin. Sign-in, then **Payments** (every line,
  filtered and paged, each with its case file: as received, the proposal and its checks in plain
  words, who decided, the posting key and the audit trail with trace links), **Ingestion** (files
  and webhook deliveries, each reconciled) and the **Audit log**.
- **The review queue** (chunk 6): proposals largest first, the payment beside the proposal and
  its checks. Approve or reject with a reason; the buttons say what the signed-in person may do
  and why not, using the same rule the API enforces.
- **Borrowers and loans** (chunk 7): a borrower directory, Repaydesk's copy of the book taken from
  Fineract every 15 minutes by the `directory` service, for search and lists: borrowers by name,
  trading name, phone or loan, and loans by how late. A borrower's or loan's own page reads Fineract
  live (balances, schedule, every repayment and its channel) and shows beside it the payments
  Repaydesk holds but hasn't posted, so a borrower who paid is never shown as unpaid. If Fineract
  doesn't answer, the page says so and shows the copy. The search box opens a loan, customer
  reference or phone directly.
- **The posting gateway** (chunk 8, `services/gateway`, .NET 10): the only writer to Fineract and
  the ledger. Woken by Postgres the moment a match is approved, it rechecks the approval against
  the decisions themselves, posts the repayment to Fineract, then a journal entry to Odoo that
  credits the loan exactly as Fineract split it, and marks the line posted, all under the
  payment's idempotency key. A crash halfway resumes without posting twice; an outage is retried;
  a refusal stops and waits for a person. Each service now has its own Fineract login: read only
  for the API, directory and matching, repayments only for the gateway. See
  [ADR 0007](docs/adr/0007-posting-gateway.md). `python scripts/dev.py gateway` runs its tests.
- **Today, and milestone 2 end to end** (chunk 9): the page staff land on, from the
  [approved mockup](https://claude.ai/artifact/3zvA1ZuM8f1CHQmRi89QiV). The latest statement week
  (money received, the share matched by rule, where the lines are, each channel), the review
  queue's size and how many need two people, unmatched money by age, and the postings: posted
  today, on the way, retrying, and stopped. A stopped posting shows Fineract's or Odoo's reason and,
  once the cause is fixed, a person sends it back with **Retry posting**; the gateway rechecks the
  approval, and the key keeps it from posting twice. `python scripts/dev.py demo` runs the whole
  milestone against the stack.
- **CI**: lint and tests on every push, the gateway's against Postgres. On main, the full stack
  comes up, gets seeded, the contract tests run against it, and the milestone 2 demo runs: a new
  statement ingested, matched, approved by two people where needed, posted, and found in Fineract
  and Odoo.

Decisions are recorded in [`docs/adr/`](docs/adr/).

## Roadmap

| Milestone | Built | Demo at the end |
|---|---|---|
| 1. Foundations and data ✅ | Compose stack, adapters and contract tests, synthetic generator, CI | `make up` seeds 2,000 borrowers and a week of payments; one trace in Jaeger |
| 2. Rules, approvals, gateway ✅ | Ingestion, rule matching, approval queue with role limits, .NET posting gateway, audit | Rule matches approved and visible in Fineract and Odoo |
| 3. Investigator agent, MCP, evals | Read-only MCP servers, investigator on the Claude Agent SDK, eval set | The agent places "MAMA CHIKA PROVISIONS" with its evidence |
| 4. Collections | Arrears buckets, drafting agent under a written policy, SMS/WhatsApp simulators | A portfolio manager approves a morning's drafts |
| 5. Reporting and measurement | Daily close pack, reporting MCP server, baseline | Before-and-after numbers |
| 6. Hosted demo | One small VM behind HTTPS, demo logins, video | A link |

## Path to a live environment

The same images run at every stage; configuration changes, code doesn't.

1. **Laptop**: this compose file, synthetic data, simulators.
2. **Hosted demo**: one small VM, Caddy for HTTPS, Langfuse Cloud's free tier, a spending
   limit on the model key.
3. **Pilot in shadow mode**: the bank's cloud account. Adapters point at a test copy of the
   real core banking system and real statement files; agents propose while staff still
   reconcile by hand.
4. **Live with approval**: approved postings go to production; single sign-on for staff.

What makes stage 3 a configuration change: the adapters, the single writer, read-only agent
tools, tracing everywhere, a switch between the Anthropic API and Amazon Bedrock, and
Terraform for AWS (milestone 6).

## Data and privacy

Everything here is synthetic. No real customer data, no real bank's statements, no real
messages sent. Names come from lists of common Nigerian names combined at random. Phone
numbers use the 07005 prefix and are never dialled. Collection banks and providers are
fictional.

## Tracing model calls

`python scripts/dev.py up-langfuse` adds a self-hosted Langfuse (about 2 GB more). The
collector then sends spans carrying OpenTelemetry GenAI attributes (`gen_ai.*`) to Langfuse
as generations, and everything to Jaeger. Ordinary spans, such as Fineract lookups, stay
out of Langfuse. The hosted demo points the same exporter at Langfuse Cloud instead.
