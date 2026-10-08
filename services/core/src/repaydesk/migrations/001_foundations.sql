-- Repaydesk's own database. Fineract and Odoo stay the systems of record; this holds what
-- Repaydesk adds: incoming lines, their status, approvals (milestone 2) and the audit log.

CREATE TABLE seed_runs (
    id            bigserial PRIMARY KEY,
    random_seed   integer     NOT NULL,
    borrowers     integer     NOT NULL,
    as_of         date        NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    counts        jsonb
);

-- Which loan a virtual account belongs to. In production this comes from the provider
-- when the account is issued; here the seeder writes it.
CREATE TABLE virtual_accounts (
    account_number text PRIMARY KEY,
    loan_ref       text NOT NULL,
    borrower_ref   text NOT NULL,
    provider       text NOT NULL DEFAULT 'payrail',
    issued_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX virtual_accounts_loan ON virtual_accounts (loan_ref);

CREATE TABLE statement_files (
    id           bigserial PRIMARY KEY,
    source       text        NOT NULL,
    file_name    text        NOT NULL,
    sha256       text        NOT NULL UNIQUE,  -- the same file twice is refused
    line_count   integer,
    received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payment_lines (
    id                  text PRIMARY KEY,      -- source:reference, e.g. bank_csv:NGT123...
    channel             text        NOT NULL,
    file_id             bigint REFERENCES statement_files (id),
    source_account      text        NOT NULL,
    value_date          date        NOT NULL,
    amount_kobo         bigint      NOT NULL CHECK (amount_kobo > 0),
    narration           text        NOT NULL,
    bank_reference      text        NOT NULL,
    payer_name          text,
    payer_account       text,
    virtual_account     text,
    terminal_id         text,
    customer_reference  text,
    status              text        NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'proposed', 'approved', 'posted', 'suspense', 'excluded', 'duplicate')),
    received_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payment_lines_status ON payment_lines (status, value_date);

-- Append-only. Every decision and every write to another system leaves a row here.
CREATE TABLE audit_log (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    actor     text        NOT NULL,   -- a person's id, or service:<name>, or agent:<name>
    action    text        NOT NULL,
    subject   text        NOT NULL,
    detail    jsonb       NOT NULL DEFAULT '{}',
    trace_id  text
);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();
