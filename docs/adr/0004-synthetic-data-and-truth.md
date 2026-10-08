# 4. Synthetic data, with the truth kept apart

Date: 2026-10-08. Status: accepted.

## Decision

- All borrowers, loans and payments are generated (`repaydesk.synth`). No real customer
  data is used anywhere. Phone numbers use the 07005 prefix and messaging is simulated.
- The generator records what every payment line really is (which loans, how much, and
  whether a careful person could tell at all) in `data/truth/truth.json`.
- The truth is for evals only. Nothing an agent can reach reads `data/truth/`, and the MCP
  servers will not expose it.
- The same random seed gives the same book, so an eval run is repeatable and a regression
  is a real regression.

## Consequences

The hard cases are designed in at known rates: names without references, trading names,
relatives paying, group leaders paying for members, one transfer for two loans, typos,
redelivered webhooks, credits that aren't repayments, and lines nobody could place.
Accuracy can be measured per case, not just overall.
