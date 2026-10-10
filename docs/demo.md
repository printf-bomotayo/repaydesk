# Milestone 2, shown to someone

About five minutes. The stack is up and seeded (`python scripts/dev.py up`). Sign-in names are
in the README; every person and payment is synthetic.

## 1. The morning (Today)

Sign in as **Amaka** (officer, approves up to ₦500,000). She lands on **Today**:

- the latest statement week: what came in, from which channels, and the share rules matched
  with certainty;
- the review queue's size, and how many payments need two people;
- money no rule could place, by age (the investigator agent's work, from milestone 3);
- postings to Fineract and the ledger: posted today, on the way, and any that stopped.

## 2. A statement arrives

In a terminal, run `python scripts/dev.py demo`. A new Northgate Bank statement for today lands
in the inbox. It holds four repayments quoting live loans (one over ₦500,000), a reversal of a
failed transfer, and a transfer that names nobody. The demo then does what staff would do in the
next steps, through the same API, and checks both books. It prints each step, and ends with each
repayment's Fineract transaction and Odoo journal entry.

To approve by hand instead, which is better on camera, run
`python scripts/dev.py demo --for-review`. It stops once the rules have proposed, and you carry on
in the browser below.

## 3. Review and approve

**Review queue**, largest first. Each card shows the payment as the bank sent it beside the
proposal and what the rule checked: the loan reference quoted, the payer's name against the
borrower's, the amount against the instalment due.

- A small repayment: **Approve**. It goes straight to the posting gateway.
- The large one: Amaka gives the first approval; it waits. Sign in as **Tunde** (supervisor) for
  the second.
- The reversal: approve the exclusion. Nothing is posted for it.

## 4. Posted, in both books

Open a payment from **Payments**. Its case file shows the trail: received, proposed, approved,
handed to the gateway, posted. The posting panel names Fineract's repayment (with its split
into principal and interest) and Odoo's journal entry.

- In Fineract's staff screens (link on the loan's page), the repayment carries the payment's key,
  `RD-P…`, as its external id.
- In Odoo (Invoicing → Accounting → Journal Entries, journal RPY), the entry has the same key as
  its reference, and credits the loan exactly as Fineract split it.

Back on **Today**, the postings count has moved.

## 5. When a posting stops (optional)

To show a person handling a posting the books refused:

1. Stop the gateway (`docker compose stop gateway`) and approve a match.
2. In Fineract's staff screens, write that loan off.
3. Start the gateway (`docker compose start gateway`). Fineract refuses the repayment ("Loan must
   be Active"); the posting stops, and **Today** lists it with the reason.
4. Undo the write-off in Fineract, then press **Retry posting** on Today. The gateway posts within
   a second or two. The case file's trail reads: failed, sent back by Amaka, posted. Fineract
   holds the repayment once.

An auditor (**Hadiza**) sees all of this and can retry nothing.
