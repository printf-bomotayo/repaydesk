-- Fuzzy search, for people and for the investigator agent's tools. Payers arrive misspelt,
-- initialled ("EZE C"), reordered ("BAKARE BABAJIDE") or as a trading name ("IFEOMA PHARMACY"),
-- and ILIKE finds none of those. Trigram similarity does.
--
-- pg_trgm is a trusted extension, so the database's owner can create it. It goes in public, and
-- everything here names it there, so a session whose search_path leaves public out (the tests'
-- throwaway schemas) still finds it.

CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;

CREATE INDEX borrowers_name_trgm ON borrowers USING gin (lower(name) public.gin_trgm_ops);
CREATE INDEX borrowers_business_trgm ON borrowers USING gin (lower(coalesce(business_name, '')) public.gin_trgm_ops);
CREATE INDEX borrowers_group_trgm ON borrowers USING gin (lower(coalesce(group_name, '')) public.gin_trgm_ops);
CREATE INDEX payment_lines_payer_trgm ON payment_lines
    USING gin (lower(coalesce(payer_name, '') || ' ' || narration) public.gin_trgm_ops);
