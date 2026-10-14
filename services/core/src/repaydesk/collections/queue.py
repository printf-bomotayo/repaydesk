"""Arrears buckets, and which loans are due a message today.

Read from the borrower directory (Repaydesk's copy of the book, ADR 0008), Repaydesk's own messages
and promises. A loan is due a message when it is 1 to 90 days late with arrears, has no open
promise still in date, has had fewer than two messages in seven days, and has no message waiting
(a draft, or approved and not yet sent). A broken promise puts a loan back on the list as its own
kind of message.
"""

from __future__ import annotations

from datetime import date

import psycopg

from repaydesk.collections.policy import BUCKET_LABEL, BUCKETS, MAX_MESSAGES_PER_WEEK

_BUCKET_SQL = ("CASE WHEN l.days_late BETWEEN 1 AND 7 THEN 'reminder' WHEN l.days_late BETWEEN 8 AND 30 THEN "
               "'follow_up' WHEN l.days_late BETWEEN 31 AND 90 THEN 'arrangement' ELSE 'recovery' END")

# A broken promise brings the loan back as its own kind of message, first, until a message is
# drafted about it; after that the loan is late like any other.
_BROKEN = "coalesce(pr.status = 'broken' AND coalesce(r.last_drafted < pr.resolved_at, true), false)"

_DUE = f"""
WITH recent AS (
    SELECT loan_ref, count(*) FILTER (WHERE created_at > now() - interval '7 days' AND status IN ('approved', 'sent'))
               AS last7,
           count(*) FILTER (WHERE status IN ('draft', 'approved')) AS waiting, max(sent_at) AS last_sent,
           max(created_at) AS last_drafted
    FROM collection_messages GROUP BY loan_ref
), promised AS (  -- the latest promise, whatever became of it: a kept one ends an older broken one
    SELECT DISTINCT ON (loan_ref) loan_ref, status, due_on, amount_kobo, resolved_at FROM promises
    WHERE status <> 'cancelled' ORDER BY loan_ref, made_on DESC, id DESC
)
SELECT l.ref, l.borrower_ref, b.name, b.branch_name, l.days_late, l.arrears_kobo, l.outstanding_kobo,
       CASE WHEN {_BROKEN} THEN 'broken_promise' ELSE {_BUCKET_SQL} END AS bucket,
       r.last_sent, pr.status, pr.due_on, pr.amount_kobo, count(*) OVER () AS total
FROM loans l
JOIN borrowers b ON b.ref = l.borrower_ref
LEFT JOIN recent r ON r.loan_ref = l.ref
LEFT JOIN promised pr ON pr.loan_ref = l.ref AND pr.status IN ('open', 'broken')
WHERE l.status = 'active' AND l.arrears_kobo > 0 AND l.days_late BETWEEN 1 AND 90
  AND coalesce(r.waiting, 0) = 0  -- a draft, or an approved message not yet sent
  AND coalesce(r.last7, 0) < {MAX_MESSAGES_PER_WEEK}
  AND NOT coalesce(pr.status = 'open' AND pr.due_on >= %(today)s, false)  -- no promise: NULL, so coalesce
  AND (%(bucket)s::text IS NULL OR (CASE WHEN {_BROKEN} THEN 'broken_promise' ELSE {_BUCKET_SQL} END)
       = %(bucket)s)
ORDER BY {_BROKEN} DESC, l.days_late DESC, l.arrears_kobo DESC, l.ref
LIMIT %(limit)s OFFSET %(offset)s
"""


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def overview(conn: psycopg.Connection, today: date, bucket: str | None = None, limit: int = 50,
             offset: int = 0) -> dict:
    """The collections morning: loans in arrears by bucket, which are due a message, drafts and promises."""
    buckets = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT {_BUCKET_SQL}, count(*), coalesce(sum(l.arrears_kobo), 0)::bigint FROM loans l "
        f"WHERE l.status = 'active' AND l.arrears_kobo > 0 AND l.days_late > 0 GROUP BY 1").fetchall()}
    rows = conn.execute(_DUE, {"today": today, "bucket": bucket, "limit": limit, "offset": offset}).fetchall()
    drafts = conn.execute("SELECT count(*) FROM collection_messages WHERE status = 'draft'").fetchone()[0]
    promises = dict(conn.execute("SELECT status, count(*) FROM promises GROUP BY 1").fetchall())
    copied = conn.execute("SELECT max(copied_at) FROM loans").fetchone()[0]
    return {
        "as_of": today.isoformat(),
        "directory_copied_at": _iso(copied),
        "buckets": [{"bucket": name, "label": BUCKET_LABEL[name], "days": f"{lo} to {hi}",
                     "loans": buckets.get(name, (0, 0))[0], "arrears_kobo": buckets.get(name, (0, 0))[1]}
                    for name, lo, hi in BUCKETS]
        + [{"bucket": "recovery", "label": BUCKET_LABEL["recovery"], "days": "over 90",
            "loans": buckets.get("recovery", (0, 0))[0], "arrears_kobo": buckets.get("recovery", (0, 0))[1]}],
        "due": {
            "total": rows[0][12] if rows else 0,
            "items": [{"loan_ref": r[0], "borrower_ref": r[1], "borrower_name": r[2], "branch_name": r[3],
                       "days_late": r[4], "arrears_kobo": r[5], "outstanding_kobo": r[6], "bucket": r[7],
                       "bucket_label": BUCKET_LABEL[r[7]], "last_sent_at": _iso(r[8]),
                       "promise": {"status": r[9], "due_on": _iso(r[10]), "amount_kobo": r[11]} if r[9] else None}
                      for r in rows],
            "limit": limit, "offset": offset,
        },
        "drafts_waiting": drafts,
        "promises": {s: promises.get(s, 0) for s in ("open", "kept", "broken", "cancelled")},
    }
