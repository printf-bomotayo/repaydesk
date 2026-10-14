-- Borrowers' replies (chunk 17): what each was about, how Repaydesk read it, and who dealt with it.
-- `outcome` is how it was read; `handled_*` says who dealt with it: rule:replies for a promise it
-- recorded itself, or the person who recorded a promise or closed it.
ALTER TABLE inbound_messages
    ADD COLUMN message_id  bigint REFERENCES collection_messages (id),  -- the message it answers, if any
    ADD COLUMN loan_ref    text,
    ADD COLUMN reading     jsonb,                                       -- what the reader made of it
    ADD COLUMN handled_by  text,                                        -- rule:replies, or a staff id
    ADD COLUMN note        text;

ALTER TABLE inbound_messages ADD CONSTRAINT inbound_messages_outcome_check
    CHECK (outcome IS NULL OR outcome IN ('promise', 'says_paid', 'question', 'stop', 'unclear', 'unknown_number'));
ALTER TABLE inbound_messages ADD CONSTRAINT inbound_messages_handled
    CHECK ((handled_at IS NULL) = (handled_by IS NULL));

CREATE INDEX inbound_messages_waiting ON inbound_messages (received_at) WHERE handled_at IS NULL;
CREATE INDEX collection_messages_phone ON collection_messages (to_phone, sent_at DESC) WHERE status = 'sent';
