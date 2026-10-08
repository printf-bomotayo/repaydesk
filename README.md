# Repaydesk

**Prototype.** Repayment reconciliation and collections for a microfinance bank, rebuilt
agent-first: rules do the certain work, agents investigate the uncertain work, and people
approve everything that moves money or reaches a customer.

It runs on open-source finance systems: **Apache Fineract** as core banking and **Odoo
Community** as the ledger. Every borrower, loan and payment in it is synthetic.

> Status: milestone 1 of 6 (foundations and data). See [the roadmap](#roadmap).

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
| Fineract API | http://127.0.0.1:18080/fineract-provider/api/v1 (tenant `default`) |
| Odoo ledger | http://127.0.0.1:18069 (database `ledger`) |
| Jaeger traces | http://127.0.0.1:16686, service `repaydesk-seed` |
| Postgres | 127.0.0.1:15432 |
| The week's payment files | `data/inbox/` |

Other tasks: `seed`, `down`, `reset`, `test`, `itest`, `lint`, `urls`
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
- **CI**: lint and tests on every push. On main, the full stack comes up, gets seeded, and
  the contract tests run against it.

Decisions are recorded in [`docs/adr/`](docs/adr/).

## Roadmap

| Milestone | Built | Demo at the end |
|---|---|---|
| 1. Foundations and data ✅ | Compose stack, adapters and contract tests, synthetic generator, CI | `make up` seeds 2,000 borrowers and a week of payments; one trace in Jaeger |
| 2. Rules, approvals, gateway | Ingestion, rule matching, approval queue with role limits, .NET posting gateway, audit | Rule matches approved and visible in Fineract and Odoo |
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
