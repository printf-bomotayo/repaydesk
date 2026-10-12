# 9. How the investigator agent works

Date: 2026-10-12. Status: accepted.

## Context

Rules place the certain cases (milestone 2: 218 of 351 lines, none wrong). The rest are the
lines a reconciliation officer spends the day on: a payer's trading name, an initial, a relative at
the branch counter, a group leader paying for five members, Pidgin. Milestone 3 hands those to a
model, without letting a model move money or overrule the controls people work under.

## Decision

- **Two models, each sized to its step.** Claude Haiku 5.5 triages the narration (what kind of
  payment it describes, the names written), at effort `low`. Claude Sonnet 5.5 investigates, at
  effort `medium`. Both are settings (`CLASSIFIER_MODEL`, `INVESTIGATOR_MODEL`).
- **The Anthropic SDK's tool runner, not the Claude Agent SDK.** The investigator's only tools are
  the bankbook MCP server's read-only lookups (ADR 0008), converted with the SDK's MCP helpers. The
  Agent SDK brings Claude Code's file, shell and web tools, which would each have to be switched
  off, and a runtime in the container. The tool runner is a loop over exactly the tools given, and
  runs unchanged on Amazon Bedrock (`MODEL_PROVIDER=bedrock`).
- **A fixed answer.** The investigator answers in a structured shape:
  - an outcome: a match, not a repayment, or suspense;
  - allocations in kobo;
  - a confidence;
  - a reason for the reviewer;
  - its findings, each marked for, against or neutral;
  - the candidates it considered.
- **The answer is checked, not trusted.** Before anything is saved, Repaydesk checks that:
  - the allocations add up to the payment to the kobo;
  - each loan is active in core banking, read live;
  - no loan appears twice;
  - the confidence clears `AGENT_MIN_CONFIDENCE` (0.7).

  An answer that fails becomes suspense, with the failed check on the run.
- **People decide.** A match or an exclusion becomes a proposal by `agent:investigator` in the
  same review queue, under the same limits and two-person rule as a rule's (ADR 0005). Nothing an
  agent concludes is posted until a person approves it. Suspense waits for a person.
- **Every run is kept.** `agent_runs` holds the whole run:
  - the triage;
  - each tool call with what it returned;
  - the answer and the checks;
  - the tokens per model, and the cost at list prices;
  - any error, and the trace id.

  A failed run (an outage, a refusal) changes nothing: the line stays open for the next attempt.
- **Prompting for doubt.** The prompt says what payments here look like and how to weigh evidence:
  the amount and the payer's history are strong, a common name alone is weak. When two borrowers
  fit about equally, it is told to answer suspense, because a wrong match moves someone else's
  money, and suspense costs a phone call.
- **Refusals fall back.** On the Claude API, requests carry server-side fallbacks
  (`fallbacks: "default"`), so a request a safety classifier declines is rerun on a fallback model
  in the same call. Bedrock doesn't offer this; a declined run there fails and is retried later.

## Consequences

- About 2.5 US cents a line at list prices: 15 live runs cost $0.37 in all, most of it Sonnet's
  first turn, with the cached prompt and tools after.
- Quality is measured, not assumed: chunk 12 adds a fixed eval set of hard lines and a report.
- The prompt is product code. A change to it is reviewed like code and run against the evals.
