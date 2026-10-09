# 6. A copy of the book for finding; core banking for deciding

Date: 2026-10-09. Status: accepted.

## Context

Staff need to find a borrower by part of a name, a trading name, a phone, or a loan, and to list
loans by how late they are. Core banking can't do that well: Fineract's name search is a
case-sensitive substring match, trading names live in a datatable that only takes exact
lookups, and listing the book takes several seconds and many calls. Asking it on every keystroke
or page would be slow for staff and heavy on the bank's most important system.

But a reviewer deciding whether a payment belongs on a loan must see the loan as it is now, not
as it was some minutes ago.

## Decision

- **A borrower directory** in Repaydesk's database: borrowers and loans with their balances,
  copied from core banking every 15 minutes by the `directory` service, through the same adapter
  contract as everything else (`CoreBankingDirectory`). Lists and search read only the copy.
- **Each sync replaces the copy in one transaction**, writing only rows that changed and
  removing what core banking no longer has. One sync runs at a time (a Postgres advisory lock).
  A failed sync is recorded and shown; the previous copy stays.
- **A single borrower's or loan's page reads core banking live**: balances, schedule, repayments
  and their channels. If core banking doesn't answer, the page says so and shows the copy, with
  its time.
- **Payments Repaydesk holds but hasn't posted** (proposed, or approved and waiting for the
  gateway) are shown beside what core banking has posted, so a borrower who paid is never shown as
  unpaid while a reviewer catches up.
- Repaydesk never edits the copy. Core banking stays the system of record (ADR 0001, ADR 0002).

## Consequences

- Lists can be up to 15 minutes behind core banking, and say how old the copy is.
- A bank's own core banking adapter must be able to list its whole book cheaply. The contract
  test `test_the_whole_book_lists_for_the_directory` checks the listing agrees with single reads.
- The investigator agent (milestone 3) gets fuzzy search over the copy, not over core banking.
