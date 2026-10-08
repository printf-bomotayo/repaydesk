-- People decide. A proposal becomes money moving only when enough of the right people approve it:
-- one approval up to the two-person threshold, two different people above it, and at least one of
-- them with a limit that covers the amount. Each decision is a row, kept for good; a reviewer's
-- limit is copied onto their approval, so a later change of limit doesn't rewrite history.
--
-- An approved match is handed to the posting gateway through the outbox (milestone 2, chunk 8), in
-- the same transaction as the approval. The gateway is the only writer to Fineract and Odoo
-- (ADR 0002); this database is how it learns what it may post.

CREATE TABLE staff (
    id             text PRIMARY KEY,             -- a short login, e.g. amaka
    name           text        NOT NULL,
    role           text        NOT NULL CHECK (role IN ('officer', 'supervisor', 'auditor')),
    limit_kobo     bigint      NOT NULL CHECK (limit_kobo >= 0),
    password_hash  text        NOT NULL,
    active         boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (role <> 'auditor' OR limit_kobo = 0)
);

CREATE TABLE decisions (
    id           bigserial PRIMARY KEY,
    proposal_id  bigint      NOT NULL REFERENCES proposals (id),
    staff_id     text        NOT NULL REFERENCES staff (id),
    decision     text        NOT NULL CHECK (decision IN ('approve', 'reject')),
    limit_kobo   bigint      NOT NULL,           -- the decider's limit at the time
    reason       text,
    at           timestamptz NOT NULL DEFAULT now(),
    CHECK ((decision = 'reject') = (reason IS NOT NULL AND length(trim(reason)) > 0)),
    UNIQUE (proposal_id, staff_id)               -- nobody decides the same proposal twice
);

CREATE TABLE posting_outbox (
    id               bigserial PRIMARY KEY,
    proposal_id      bigint      NOT NULL UNIQUE REFERENCES proposals (id),
    line_id          text        NOT NULL REFERENCES payment_lines (id),
    idempotency_key  text        NOT NULL UNIQUE,   -- becomes Fineract's external id and Odoo's ref
    value_date       date        NOT NULL,
    amount_kobo      bigint      NOT NULL CHECK (amount_kobo > 0),
    allocations      jsonb       NOT NULL CHECK (jsonb_array_length(allocations) > 0),
    approved_by      text[]      NOT NULL CHECK (cardinality(approved_by) > 0),
    status           text        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'posted', 'failed')),
    attempts         integer     NOT NULL DEFAULT 0,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    claimed_at       timestamptz,
    posted_at        timestamptz
);
CREATE INDEX posting_outbox_pending ON posting_outbox (created_at) WHERE status = 'pending';
