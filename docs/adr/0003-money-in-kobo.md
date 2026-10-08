# 3. Money is integer kobo inside Repaydesk

Date: 2026-10-08. Status: accepted.

## Decision

Every amount inside Repaydesk is an integer number of kobo (₦1 = 100 kobo). Adapters
convert at the boundary: Fineract and Odoo take decimal naira, statement files carry
formatted naira, the payment provider sends kobo. Postgres stores `bigint` kobo.

## Consequences

No float rounding in matching, splitting or allocation; a split across loans always adds
back to the line's amount exactly, and the tests check that it does.
