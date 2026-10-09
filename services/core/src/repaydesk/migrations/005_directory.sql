-- The borrower directory: a copy of who borrows what, taken from core banking every few minutes.
-- Lists and search read this copy, because asking Fineract for 2,000 borrowers on every page
-- would be slow for staff and heavy on the bank's core system. A single borrower's or loan's
-- page still asks Fineract live; the copy is only for finding them.
--
-- Core banking stays the system of record. Nothing here is edited by Repaydesk: each sync
-- replaces what changed and removes what core banking no longer has.

CREATE TABLE borrowers (
    ref            text PRIMARY KEY,          -- the bank's customer reference, e.g. CUS-000123
    core_id        text        NOT NULL,      -- core banking's own id, for links into its screens
    name           text        NOT NULL,
    phone          text,
    branch_code    text,
    branch_name    text,
    business_name  text,
    group_ref      text,
    group_name     text,
    joined_on      date,
    active         boolean     NOT NULL,
    copied_at      timestamptz NOT NULL       -- when a sync last wrote this row
);
CREATE INDEX borrowers_name ON borrowers (lower(name));
CREATE INDEX borrowers_phone ON borrowers (phone);

CREATE TABLE loans (
    ref               text PRIMARY KEY,       -- e.g. LN-2026-000123
    core_id           text        NOT NULL,
    borrower_ref      text        NOT NULL,   -- no foreign key: a sync may see a loan before its borrower
    product           text        NOT NULL,
    product_name      text        NOT NULL,
    principal_kobo    bigint      NOT NULL,
    disbursed_on      date,
    status            text        NOT NULL CHECK (status IN ('active', 'closed', 'overpaid', 'other')),
    outstanding_kobo  bigint      NOT NULL,
    arrears_kobo      bigint      NOT NULL,
    days_late         integer     NOT NULL,
    copied_at         timestamptz NOT NULL
);
CREATE INDEX loans_borrower ON loans (borrower_ref);
CREATE INDEX loans_late ON loans (days_late DESC) WHERE days_late > 0;

CREATE TABLE directory_syncs (
    id           bigserial PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    borrowers    integer,
    loans        integer,
    changed      integer,     -- rows added, changed or removed
    error        text
);
