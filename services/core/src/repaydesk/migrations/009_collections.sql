-- Collections (milestone 4): asking late borrowers to pay, under docs/collections-policy.md.
-- Messages are drafted (by the drafting agent or a person), approved by a portfolio manager, then
-- sent by the gateway; replies come back; a reply with a date becomes a promise to pay.

-- A portfolio manager approves messages, not payments: no payment limit, like an auditor.
ALTER TABLE staff DROP CONSTRAINT staff_role_check;
ALTER TABLE staff ADD CONSTRAINT staff_role_check CHECK (role IN ('officer', 'supervisor', 'auditor', 'manager'));
ALTER TABLE staff ADD CONSTRAINT staff_manager_no_payment_limit CHECK (role <> 'manager' OR limit_kobo = 0);

-- An agent run is about a payment line (the investigator) or a loan (the drafting agent).
ALTER TABLE agent_runs ALTER COLUMN line_id DROP NOT NULL;
ALTER TABLE agent_runs ADD COLUMN loan_ref text;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_about_something CHECK (line_id IS NOT NULL OR loan_ref IS NOT NULL);
CREATE INDEX agent_runs_loan ON agent_runs (loan_ref, id DESC) WHERE loan_ref IS NOT NULL;

CREATE TABLE collection_messages (
    id               bigserial PRIMARY KEY,
    loan_ref         text        NOT NULL,
    borrower_ref     text        NOT NULL,
    bucket           text        NOT NULL CHECK (bucket IN ('reminder', 'follow_up', 'arrangement', 'broken_promise')),
    channel          text        NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    body             text        NOT NULL CHECK (length(body) BETWEEN 1 AND 1000),
    amount_due_kobo  bigint      NOT NULL CHECK (amount_due_kobo > 0),  -- what core banking said was due when drafted
    drafted_by       text        NOT NULL,                              -- agent:collector, or a staff id
    run_id           bigint      REFERENCES agent_runs (id),
    checks           jsonb       NOT NULL DEFAULT '[]',                 -- the policy checks at drafting
    status           text        NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'rejected', 'sent', 'failed')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    decided_by       text        REFERENCES staff (id),
    decided_at       timestamptz,
    reject_reason    text,
    idempotency_key  text        UNIQUE,                                -- CM-<id>, set on approval
    sent_at          timestamptz,
    provider_id      text,
    error            text,
    CHECK (status IN ('draft', 'failed') OR decided_by IS NOT NULL),
    CHECK (status <> 'rejected' OR reject_reason IS NOT NULL),
    CHECK (status <> 'sent' OR (sent_at IS NOT NULL AND provider_id IS NOT NULL))
);
CREATE INDEX collection_messages_loan ON collection_messages (loan_ref, created_at DESC);
CREATE INDEX collection_messages_drafts ON collection_messages (created_at) WHERE status = 'draft';

CREATE TABLE inbound_messages (
    id            bigserial PRIMARY KEY,
    channel       text        NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    from_phone    text        NOT NULL,
    body          text        NOT NULL,
    provider_id   text        NOT NULL UNIQUE,                          -- a redelivery is the same reply
    received_at   timestamptz NOT NULL DEFAULT now(),
    borrower_ref  text,                                                 -- whose phone it is, if anyone's
    handled_at    timestamptz,
    outcome       text                                                  -- promise, question, unclear, ...
);

CREATE TABLE promises (
    id            bigserial PRIMARY KEY,
    loan_ref      text        NOT NULL,
    borrower_ref  text        NOT NULL,
    amount_kobo   bigint      NOT NULL CHECK (amount_kobo > 0),
    due_on        date        NOT NULL,
    made_on       date        NOT NULL,
    source        text        NOT NULL CHECK (source IN ('reply', 'staff')),
    inbound_id    bigint      REFERENCES inbound_messages (id),
    recorded_by   text        NOT NULL,                                 -- agent:collector, or a staff id
    status        text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'kept', 'broken', 'cancelled')),
    resolved_at   timestamptz,
    note          text,
    CHECK (due_on >= made_on),
    CHECK ((status = 'open') = (resolved_at IS NULL))
);
CREATE INDEX promises_open ON promises (loan_ref, due_on) WHERE status = 'open';
