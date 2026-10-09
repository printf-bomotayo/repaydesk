-- The posting gateway (services/gateway, .NET) drains posting_outbox: it posts each approved
-- match to Fineract and Odoo and records what each system called it. It holds the row's lock
-- while it posts, so 'claimed' is never needed: a crash rolls back to 'pending', and the
-- idempotency keys make the retry find what was already posted instead of posting again.

ALTER TABLE posting_outbox DROP CONSTRAINT posting_outbox_status_check;
ALTER TABLE posting_outbox ADD CONSTRAINT posting_outbox_status_check
    CHECK (status IN ('pending', 'posted', 'failed'));
ALTER TABLE posting_outbox DROP COLUMN claimed_at;

ALTER TABLE posting_outbox
    ADD COLUMN next_attempt_at   timestamptz,   -- after a transient failure: not before then
    -- Fineract's repayments, one per allocation: [{"loan_ref", "external_id", "transaction_id",
    -- "principal_kobo", "interest_kobo", "charges_kobo", "overpayment_kobo"}]
    ADD COLUMN core_transactions jsonb,
    ADD COLUMN ledger_entry      text;          -- Odoo's name for the journal entry, e.g. RPY/2026/00012

ALTER TABLE posting_outbox ADD CONSTRAINT posting_outbox_posted_has_both
    CHECK (status <> 'posted' OR (posted_at IS NOT NULL AND core_transactions IS NOT NULL AND ledger_entry IS NOT NULL));

DROP INDEX posting_outbox_pending;
CREATE INDEX posting_outbox_ready ON posting_outbox (coalesce(next_attempt_at, created_at)) WHERE status = 'pending';

-- Wakes the gateway as soon as a posting is ready, instead of at its next poll.
CREATE FUNCTION posting_outbox_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('posting_outbox', NEW.id::text);
    RETURN NULL;
END $$;

CREATE TRIGGER posting_outbox_ready AFTER INSERT OR UPDATE OF status ON posting_outbox
    FOR EACH ROW WHEN (NEW.status = 'pending') EXECUTE FUNCTION posting_outbox_notify();
