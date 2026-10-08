-- Ingestion: a line is known by its source and the source's own reference. The first time a
-- reference is seen it is the original; any later sighting (a redelivered webhook, a statement
-- sent twice with overlapping days) is kept, marked duplicate and pointed at the original, so
-- nothing that arrived is silently dropped and nothing is counted twice.

ALTER TABLE payment_lines ADD COLUMN dedupe_key   text NOT NULL;
ALTER TABLE payment_lines ADD COLUMN duplicate_of text REFERENCES payment_lines (id);
ALTER TABLE payment_lines ADD CONSTRAINT duplicate_points_at_original
    CHECK ((status = 'duplicate') = (duplicate_of IS NOT NULL));

-- A file is "seen before" only if the same source sent the same name with the same bytes. Hash
-- alone is not enough: every empty day's POS settlement file is the same header, byte for byte.
-- A statement resent under a new name is read again, and its lines are caught one by one below.
ALTER TABLE statement_files DROP CONSTRAINT statement_files_sha256_key;
ALTER TABLE statement_files ADD CONSTRAINT statement_files_once UNIQUE (source, file_name, sha256);

-- One original per reference, enforced by the database rather than by checking first.
CREATE UNIQUE INDEX payment_lines_one_original ON payment_lines (dedupe_key) WHERE status <> 'duplicate';

-- Every webhook delivery, accepted or not. The body is not stored: it carries the payer's name.
-- Its hash is, so a disputed delivery can still be matched to what the provider says it sent.
CREATE TABLE webhook_deliveries (
    id            bigserial PRIMARY KEY,
    provider      text        NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    body_sha256   text        NOT NULL,
    signature_ok  boolean     NOT NULL,
    event_id      text,
    outcome       text        NOT NULL CHECK (outcome IN ('stored', 'duplicate', 'ignored', 'rejected')),
    line_id       text REFERENCES payment_lines (id)
);
