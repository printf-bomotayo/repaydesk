-- Approved collections messages are the gateway's outbox: it sends each one, inside contact hours,
-- to the messaging provider (a simulator in this prototype), and records what came back.
ALTER TABLE collection_messages
    ADD COLUMN to_phone        text,                    -- the borrower's number, from the directory, at approval
    ADD COLUMN send_after      timestamptz,             -- the first moment inside contact hours, at approval
    ADD COLUMN attempts        integer NOT NULL DEFAULT 0,
    ADD COLUMN next_attempt_at timestamptz,             -- set when a send is to be tried again
    ADD COLUMN drafted_body    text;                    -- what the drafter wrote, when a person edited it

ALTER TABLE collection_messages ADD CONSTRAINT collection_messages_ready_to_send
    CHECK (status NOT IN ('approved', 'sent') OR (to_phone IS NOT NULL AND send_after IS NOT NULL
                                                   AND idempotency_key IS NOT NULL));

CREATE INDEX collection_messages_outbox ON collection_messages (coalesce(next_attempt_at, send_after))
    WHERE status = 'approved';

-- The gateway LISTENs, as it does for postings, so an approval inside hours goes out at once.
CREATE FUNCTION message_outbox_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('message_outbox', NEW.id::text);
    RETURN NEW;
END;
$$;

CREATE TRIGGER collection_messages_notify AFTER UPDATE OF status ON collection_messages
    FOR EACH ROW WHEN (NEW.status = 'approved' AND OLD.status IS DISTINCT FROM 'approved')
    EXECUTE FUNCTION message_outbox_notify();
