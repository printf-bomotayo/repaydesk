# 1. Fineract and Odoo stand in for the bank's own systems

Date: 2026-10-08. Status: accepted.

## Context

Repaydesk has to be demonstrable without access to any bank's core banking system or
ledger, yet it should behave like it would against them: real loan schedules, real arrears,
real double-entry postings. Mocks would hide the integration work that is most of the job.

## Decision

- **Apache Fineract** (1.12.1) is the core banking system. It is open-source, built for
  microfinance, and has the concepts that matter here: offices, clients, groups, loan
  products, schedules, delinquency and repayments with external ids.
- **Odoo Community** (18.0) is the ledger. It has a real chart of accounts, journals and
  posted entries, reached over JSON-RPC.
- Both sit behind the `CoreBanking` and `Ledger` contracts in
  `services/core/src/repaydesk/adapters/ports.py`. The contract tests in
  `services/core/tests/contract/` run against an in-memory reference adapter and against
  the real systems.

## Consequences

- Swapping in a bank's system means writing one adapter and passing the same tests.
- Fineract's limits are handled in the adapter, not worked around above it: its name search
  is case-sensitive, and it has no trading-name field (a `client_business` datatable holds
  it). Fuzzy search will come from a borrower directory read model (milestone 3).
- Fineract's own accounting is off (`accountingRule: none`). Odoo is the only ledger, so
  there is one place where the books must balance.
- ERPNext stays the fallback ledger if Odoo's accounting setup proves too heavy.
