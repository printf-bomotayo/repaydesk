# 8. What the investigator agent can see, and how

Date: 2026-10-12. Status: accepted.

## Context

From milestone 3, a model investigates the payments rules can't place: whose loan is "MAMA CHIKA
PROVISIONS" paying? To answer, it needs the bank's records: borrowers and their trading names,
loans and what's due, who has paid what before. Those records hold personal data, and the systems
that hold them also post money. The agent must be able to look anything relevant up, and nothing
else: no writes, no numbers it doesn't need.

## Decision

- **Tools, served over MCP.** The agent reads only through two MCP servers in
  `repaydesk.servers`: `bankbook` (borrowers, loans, payments) and `ledger` (journal entries,
  balances). Every tool is a lookup, annotated read-only. The same servers work from Claude
  Desktop or Claude Code (`.mcp.json`), so a person can ask the questions the agent asks and get
  the same answers.
- **Read-only at the source, not by promise.** The bankbook's database session sets
  `default_transaction_read_only`, so even a query that tried to write would be refused. Core
  banking is read with `repaydesk-read` (Fineract's read-only login, ADR 0007). The ledger server
  logs in to Odoo as its own `repaydesk-read`, in Odoo's read-only accounting group: Odoo refuses it
  a journal entry. Neither server holds a credential that can post.
- **Names in, numbers masked.** A payer's name and a narration's words are the evidence, so the
  model reads them. Phone numbers and account numbers reach it as their last four digits
  (`0700 *** 6957`, `****6789`), through `repaydesk.privacy`, in every tool's answer. Where the
  whole number matters (a phone typed at a POS terminal), the tool takes the payment's line id
  and looks the number up itself: `borrowers_by_line_phone` returns the borrowers, never the phone.
- **Fuzzy search in Postgres.** Payers are misspelt, initialled, reordered or use a trading name.
  `pg_trgm` scores names three ways (alike as a whole, and either inside the other) over the
  borrower directory and earlier payments, with GIN indexes. No search service to run.
- **Live where it decides, copied where it searches.** Searches read the directory (up to 15
  minutes old, and each answer says when it was copied). A loan's balance and what's due are read
  from core banking live, because that's what a match is judged on.
- **What the agent never sees.** The truth file (`data/truth/`) is for scoring only; no tool reads
  it. Staff, decisions and the audit log aren't exposed either: the agent investigates payments,
  not people's work.

## Consequences

- Names do go to the model provider. The demo's data is synthetic. A bank would run the model where
  its data may go (Amazon Bedrock in-region, under the bank's agreement); the provider is a
  setting (`MODEL_PROVIDER`), and nothing in the tools changes.
- A tool that needs more than a lookup can do is a new tool, reviewed like code, not a wider
  credential.
- The trigram thresholds are low (0.3 and 0.4) to find initialled names; the score orders what
  comes back, and the agent judges it. Search quality is measured in the agent's evals
  (milestone 3), not assumed.
