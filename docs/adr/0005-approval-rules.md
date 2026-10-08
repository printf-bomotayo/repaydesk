# 5. Who may approve what

Date: 2026-10-09. Status: accepted.

## Context

Every proposal, a rule's or the agent's, is approved by a person before money moves (ADR 0002).
Banks set limits on what one person may approve alone, and need a second pair of eyes above a
threshold. The rules have to be simple enough to show on the screen before anyone clicks.

## Decision

- Roles: **officer** (limit ₦500,000), **supervisor** (₦5,000,000), **auditor** (reads everything,
  decides nothing).
- Up to the two-person threshold, ₦500,000 (`TWO_PERSON_ABOVE_KOBO`), one approval settles a
  proposal. Above it, two different people approve.
- The approval that settles a proposal needs at least one approver whose limit covers the amount.
  Anyone allowed to decide may give a first approval, so an officer can vouch and a supervisor
  completes. Two officers can't settle what neither may approve alone.
- Above every limit, a proposal waits. Nobody can raise their own limit by approving.
- A rejection needs a reason. The line goes back to waiting and rules don't propose it again; a
  person has said the obvious answer is wrong, so it goes to the investigator.
- Exclusions (credits that aren't repayments) follow the same limits, by the line's amount. A ₦2.5m
  reversal wrongly excluded is as costly as one wrongly posted.
- Each decision records the decider's limit at the time. A later change of limit doesn't rewrite
  who was allowed to approve what.
- The database enforces what it can: one decision per person per proposal, a reason on every
  rejection, no limit on an auditor, one posting per proposal.

## Consequences

- `approvals.judge` is a pure function, shared by the queue (to show the button, or why not) and
  the approve call (to enforce it), so the screen and the rule can't disagree.
- Limits and the threshold are configuration. A bank with different numbers changes them, not
  code.
- Sign-in is a stand-in for the bank's single sign-on (plan, stage 4). It has no lockout after
  failed attempts; the hosted demo has throwaway logins and nothing worth guessing at.
