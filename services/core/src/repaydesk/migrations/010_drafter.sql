-- The drafting agent's runs end in a draft, not a proposal: one more outcome.
ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_outcome_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_outcome_check
    CHECK (outcome IN ('proposed', 'excluded', 'suspense', 'failed', 'drafted'));
