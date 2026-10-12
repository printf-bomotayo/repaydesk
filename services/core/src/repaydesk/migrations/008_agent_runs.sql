-- Every investigation the agent makes is recorded whole: what it was shown, each tool it called and
-- what came back, what it concluded, what Repaydesk checked before acting on that, what it cost.
-- A reviewer reads the run beside the proposal; an auditor can replay why a line went where it did.

CREATE TABLE agent_runs (
    id                bigserial PRIMARY KEY,
    line_id           text        NOT NULL REFERENCES payment_lines (id),
    agent             text        NOT NULL,                -- investigator
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    model             text        NOT NULL,
    classifier_model  text,
    classification    jsonb,                               -- the narration's triage, before investigating
    outcome           text CHECK (outcome IN ('proposed', 'excluded', 'suspense', 'failed')),
    confidence        numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
    verdict           jsonb,                               -- the agent's answer, as it gave it
    checks            jsonb,                               -- what Repaydesk verified before acting on it
    proposal_id       bigint REFERENCES proposals (id),
    steps             jsonb       NOT NULL DEFAULT '[]',   -- [{tool, input, output, ms}]
    usage             jsonb,                               -- tokens per model
    cost_usd          numeric(10, 6),
    error             text,
    trace_id          text,
    CHECK (outcome IS NULL OR finished_at IS NOT NULL),
    CHECK (outcome NOT IN ('proposed', 'excluded') OR proposal_id IS NOT NULL)
);
CREATE INDEX agent_runs_line ON agent_runs (line_id, id DESC);
