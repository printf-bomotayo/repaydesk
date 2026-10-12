# Evals

How well the investigator agent places the payments rules can't. One eval, one question: given a
hard payment, does the agent's checked answer name the right loans, to the kobo, or hold the
payment in suspense when nobody could tell?

- **Cases**: [`cases.md`](cases.md) (and `cases.json`, which the runner reads). 60 payments from the
  seeded week (seed 42, 2,000 borrowers, as of 8 October 2026):
  - every one of the rare kinds: group leaders 8, someone paying for someone else 10, a mistyped
    reference 6, a mistyped reference on a cash sheet 5, payer only 6, Pidgin 3, a split 1;
  - the three nobody could place: 2 blank POS lines, 1 unresolvable;
  - 6 each of names only, trading names and names on cash sheets, in a fixed arbitrary order.

  The expected answers are the generator's truth, written when each payment was made up, never a
  model's output.
- **Run** dry against the seeded stack: the real investigator, the real MCP tools, Repaydesk's real
  checks, and nothing written. `python scripts/dev.py eval` (about $2.40 at list prices; `--limit 5`
  for a pilot), or the **Investigator eval** workflow in GitHub Actions (manual; needs the
  `ANTHROPIC_API_KEY` repository secret). Reports land in [`reports/`](reports/).
- **Grading**, per case:
  - **correct**: the same loans and amounts to the kobo, or suspense for a payment nobody could
    place;
  - **wrong**: any other match, or calling a repayment "not a repayment". A reviewer must catch
    these, so it's the number that matters most;
  - **held**: a placeable payment sent to suspense. It costs a phone call, not money;
  - **failed**: no answer at all (an outage, a refusal).
- **Drift.** Core banking's "today" moves on, so a loan's arrears grow between runs and an amount
  that fitted may stop fitting. Compare runs made on the same stack on the same day, or a fresh
  stack (the workflow seeds one).
- **Noise.** With 60 cases and one pass, accuracy moves about ±12 points (95%) from chance alone.
  A change of a few cases between runs is not a finding; a wrong answer always is.

## What the truth can't see

The generator gives the same trading name to many borrowers: 46 trade as "Success Motors Spares".
Some payments it marks placeable can't be placed by a careful person from the payment alone.
Holding those is the right behaviour, and shows here as "held", not "wrong".

## Runs

| When (UTC) | What changed | Correct | Wrong | Held | Failed | Cost | Report |
|---|---|---:|---:|---:|---:|---:|---|
| 12 Oct 10:39 | First run: the chunk 11 agent | 31 | 5 | 24 | 0 | $2.13 | [report](reports/2026-10-12-1039-claude-sonnet-5-5.md) |
| 12 Oct 10:46 | Tools `loans_fitting` and `nearby_loan_refs`; three prompt rules (check every candidate, doubt a reference whose borrower doesn't fit, no splits chosen to make a total) | 30 | 3 | 27 | 0 | $2.42 | [report](reports/2026-10-12-1046-claude-sonnet-5-5.md) |
| 12 Oct 10:50 | `search_borrowers` says when its list is cut short | 15 of the 26 that ran | 0 | 11 | 34 (API credit ran out) | $1.76 | removed: incomplete |

The second run's changes were diagnosed on these same cases, so it may flatter them. The third
run stopped when the API key's credit ran out; it is to be run again in full.
