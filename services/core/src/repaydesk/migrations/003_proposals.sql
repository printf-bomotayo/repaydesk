-- A proposal says what a line is: a repayment to one or more loans, or not a repayment at all.
-- Rules make proposals now and the investigator agent later; people approve or reject them
-- (chunk 4). A line has at most one live proposal (pending or approved). A rejected or replaced
-- one stays as history, so a reviewer can see what was proposed before and why it was turned down.

CREATE TABLE proposals (
    id           bigserial PRIMARY KEY,
    line_id      text        NOT NULL REFERENCES payment_lines (id),
    kind         text        NOT NULL CHECK (kind IN ('match', 'exclude')),
    proposed_by  text        NOT NULL,          -- rule:<name> or agent:<name>
    confidence   numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    allocations  jsonb       NOT NULL DEFAULT '[]',  -- [{"loan_ref": ..., "amount_kobo": ...}]
    evidence     jsonb       NOT NULL DEFAULT '{}',  -- what the proposer checked, for the reviewer
    status       text        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'match') = (jsonb_array_length(allocations) > 0))
);

CREATE UNIQUE INDEX proposals_one_live_per_line ON proposals (line_id) WHERE status IN ('pending', 'approved');
CREATE INDEX proposals_queue ON proposals (status, created_at);
