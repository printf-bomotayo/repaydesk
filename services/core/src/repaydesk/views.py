"""What the web app reads: payment lines, ingestion, the audit log.

Read-only queries shaped for the screens, kept apart from the code that changes things. Every list
is paged, because a real bank's week is tens of thousands of lines, not the demo's few hundred.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg

from repaydesk.domain import BANK_TIMEZONE, bank_today

STATUSES = ("new", "proposed", "approved", "posted", "suspense", "excluded", "duplicate")


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# ---- payment lines -----------------------------------------------------------------------------

_LIVE = """
LEFT JOIN LATERAL (
    SELECT p.id, p.kind, p.proposed_by, p.allocations FROM proposals p
    WHERE p.line_id = l.id AND p.status IN ('pending', 'approved') LIMIT 1
) live ON true
"""


def _line_filter(status: str | None, channel: str | None, q: str | None) -> tuple[str, dict]:
    where, args = [], {}
    if status:
        where.append("l.status = %(status)s")
        args["status"] = status
    elif status is not None:
        # "All" means all the money that arrived: copies of a line already held are left out, and
        # are one filter away. A provider retrying a webhook ten times is not ten payments.
        where.append("l.status <> 'duplicate'")
    if channel:
        where.append("l.channel = %(channel)s")
        args["channel"] = channel
    if q and q.strip():
        where.append("(l.id ILIKE %(q)s OR l.narration ILIKE %(q)s OR l.payer_name ILIKE %(q)s "
                     "OR l.customer_reference ILIKE %(q)s OR live.allocations::text ILIKE %(q)s)")
        args["q"] = f"%{q.strip()}%"
    return (" WHERE " + " AND ".join(where)) if where else "", args


def lines(conn: psycopg.Connection, status: str | None = None, channel: str | None = None, q: str | None = None,
          limit: int = 50, offset: int = 0) -> dict:
    where, args = _line_filter(status or "", channel, q)
    rows = conn.execute(
        f"SELECT l.id, l.channel, l.value_date, l.amount_kobo, l.narration, l.payer_name, l.status, "
        f"l.duplicate_of, live.kind, live.proposed_by, live.allocations, count(*) OVER () "
        f"FROM payment_lines l {_LIVE} {where} "
        f"ORDER BY l.value_date DESC, l.amount_kobo DESC, l.id LIMIT %(limit)s OFFSET %(offset)s",
        args | {"limit": limit, "offset": offset},
    ).fetchall()
    # Counts for the filter chips: everything matching the other filters, by status.
    cwhere, cargs = _line_filter(None, channel, q)
    counts = dict(conn.execute(f"SELECT l.status, count(*) FROM payment_lines l {_LIVE} {cwhere} GROUP BY 1",
                               cargs).fetchall())
    return {
        "items": [{
            "id": r[0], "channel": r[1], "value_date": _iso(r[2]), "amount_kobo": r[3], "narration": r[4],
            "payer_name": r[5], "status": r[6], "duplicate_of": r[7], "proposal_kind": r[8], "proposed_by": r[9],
            "loan_refs": [a["loan_ref"] for a in (r[10] or [])],
        } for r in rows],
        "total": rows[0][11] if rows else 0,
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "limit": limit, "offset": offset,
    }


def line(conn: psycopg.Connection, line_id: str) -> dict | None:
    """One line's case file: what arrived, where from, what was proposed, who decided, what happened."""
    row = conn.execute(
        "SELECT l.id, l.channel, l.source_account, l.value_date, l.amount_kobo, l.narration, l.bank_reference, "
        "l.payer_name, l.payer_account, l.virtual_account, l.terminal_id, l.customer_reference, l.status, "
        "l.duplicate_of, l.received_at, f.id, f.source, f.file_name, f.received_at "
        "FROM payment_lines l LEFT JOIN statement_files f ON f.id = l.file_id WHERE l.id = %s", (line_id,),
    ).fetchone()
    if not row:
        return None
    (lid, channel, account, value_date, amount, narration, bank_ref, payer, payer_account, virtual_account,
     terminal, customer_ref, status, duplicate_of, received, file_id, source, file_name, file_received) = row

    delivery = conn.execute(
        "SELECT received_at, event_id, outcome FROM webhook_deliveries WHERE line_id = %s ORDER BY id LIMIT 1",
        (lid,),
    ).fetchone()
    proposals = conn.execute(
        "SELECT id, kind, proposed_by, confidence, allocations, evidence, status, created_at FROM proposals "
        "WHERE line_id = %s ORDER BY id", (lid,),
    ).fetchall()
    ids = [p[0] for p in proposals]
    decisions = conn.execute(
        "SELECT d.proposal_id, d.staff_id, s.name, d.decision, d.reason, d.at FROM decisions d "
        "JOIN staff s ON s.id = d.staff_id WHERE d.proposal_id = ANY(%s) ORDER BY d.at", (ids,),
    ).fetchall() if ids else []
    posting = conn.execute(
        "SELECT o.id, o.idempotency_key, o.status, o.attempts, o.last_error, o.posted_at, o.next_attempt_at, "
        "o.core_transactions, o.ledger_entry FROM posting_outbox o "
        "WHERE o.line_id = %s ORDER BY o.id DESC LIMIT 1", (lid,),
    ).fetchone()
    copies = [r[0] for r in conn.execute(
        "SELECT id FROM payment_lines WHERE duplicate_of = %s ORDER BY received_at, id", (lid,))]
    trail = conn.execute(
        "SELECT a.id, a.at, a.actor, a.action, a.detail, a.trace_id, s.name FROM audit_log a "
        "LEFT JOIN staff s ON s.id = a.actor WHERE a.subject = %s ORDER BY a.id",
        (f"payment_line:{lid}",),
    ).fetchall()

    return {
        "id": lid, "channel": channel, "source_account": account, "value_date": _iso(value_date),
        "amount_kobo": amount, "narration": narration, "bank_reference": bank_ref, "payer_name": payer,
        "payer_account": payer_account, "virtual_account": virtual_account, "terminal_id": terminal,
        "customer_reference": customer_ref, "status": status, "duplicate_of": duplicate_of,
        "received_at": _iso(received), "duplicates": copies,
        "arrived_by": (
            {"kind": "file", "file_id": file_id, "source": source, "file_name": file_name,
             "received_at": _iso(file_received)} if file_id else
            # A provider's line with no delivery row was loaded some other way (a test, a backfill).
            {"kind": "webhook", "event_id": delivery[1] if delivery else bank_ref,
             "received_at": _iso(delivery[0] if delivery else received)}
        ),
        "proposals": [{
            "id": p[0], "kind": p[1], "proposed_by": p[2], "confidence": float(p[3]), "allocations": p[4],
            "evidence": p[5], "status": p[6], "proposed_at": _iso(p[7]),
            "decisions": [{"staff_id": d[1], "name": d[2], "decision": d[3], "reason": d[4], "at": _iso(d[5])}
                          for d in decisions if d[0] == p[0]],
        } for p in proposals],
        "posting": {"outbox_id": posting[0], "idempotency_key": posting[1], "status": posting[2],
                    "attempts": posting[3], "last_error": posting[4], "posted_at": _iso(posting[5]),
                    "next_attempt_at": _iso(posting[6]), "core_transactions": posting[7] or [],
                    "ledger_entry": posting[8]}
        if posting else None,
        "trail": [{"id": t[0], "at": _iso(t[1]), "actor": t[2], "actor_name": t[6], "action": t[3], "detail": t[4],
                   "trace_id": t[5]} for t in trail],
    }


# ---- ingestion ---------------------------------------------------------------------------------


def ingestion(conn: psycopg.Connection, limit: int = 200) -> dict:
    files = conn.execute(
        "SELECT f.id, f.source, f.file_name, f.received_at, f.line_count, "
        "count(l.id) FILTER (WHERE l.status <> 'duplicate'), count(l.id) FILTER (WHERE l.status = 'duplicate') "
        "FROM statement_files f LEFT JOIN payment_lines l ON l.file_id = f.id "
        "GROUP BY f.id ORDER BY f.received_at DESC, f.id DESC LIMIT %s", (limit,),
    ).fetchall()
    by_source = conn.execute(
        "SELECT f.source, count(DISTINCT f.id), count(l.id) FILTER (WHERE l.status <> 'duplicate'), "
        "count(l.id) FILTER (WHERE l.status = 'duplicate') "
        "FROM statement_files f LEFT JOIN payment_lines l ON l.file_id = f.id GROUP BY 1 ORDER BY 1"
    ).fetchall()
    outcomes = dict(conn.execute("SELECT outcome, count(*) FROM webhook_deliveries GROUP BY 1").fetchall())
    recent = conn.execute(
        "SELECT id, received_at, provider, event_id, signature_ok, outcome, line_id FROM webhook_deliveries "
        "ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return {
        "files": [{"id": f[0], "source": f[1], "file_name": f[2], "received_at": _iso(f[3]), "line_count": f[4],
                   "stored": f[5], "duplicates": f[6]} for f in files],
        "sources": [{"source": s[0], "files": s[1], "stored": s[2], "duplicates": s[3]} for s in by_source],
        "webhooks": {
            "outcomes": {k: outcomes.get(k, 0) for k in ("stored", "duplicate", "ignored", "rejected")},
            "recent": [{"id": w[0], "received_at": _iso(w[1]), "provider": w[2], "event_id": w[3],
                        "signature_ok": w[4], "outcome": w[5], "line_id": w[6]} for w in recent],
        },
    }


# ---- audit log ---------------------------------------------------------------------------------


def audit(conn: psycopg.Connection, actor: str | None = None, action: str | None = None,
          subject: str | None = None, before: int | None = None, limit: int = 100) -> dict:
    """Newest first. Paged by id (`before`), so new rows arriving don't shift the pages."""
    where, args = [], {"limit": limit}
    if actor:
        where.append("a.actor = %(actor)s")
        args["actor"] = actor
    if action:
        where.append("a.action LIKE %(action)s")
        args["action"] = action.rstrip("*") + "%"
    if subject:
        where.append("a.subject ILIKE %(subject)s")
        args["subject"] = f"%{subject}%"
    if before:
        where.append("a.id < %(before)s")
        args["before"] = before
    sql = ("SELECT a.id, a.at, a.actor, a.action, a.subject, a.detail, a.trace_id, s.name "
           "FROM audit_log a LEFT JOIN staff s ON s.id = a.actor"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.id DESC LIMIT %(limit)s")
    rows = conn.execute(sql, args).fetchall()
    actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY 1")]
    return {
        "items": [{"id": r[0], "at": _iso(r[1]), "actor": r[2], "actor_name": r[7], "action": r[3], "subject": r[4],
                   "detail": r[5], "trace_id": r[6]} for r in rows],
        "next_before": rows[-1][0] if len(rows) == limit else None,
        "actions": actions,
    }


def summary(conn: psycopg.Connection) -> dict:
    """Small counts for the navigation: lines by status, proposals waiting for a person, and
    postings that stopped."""
    by_status = dict(conn.execute("SELECT status, count(*) FROM payment_lines GROUP BY 1").fetchall())
    pending = conn.execute("SELECT count(*) FROM proposals WHERE status = 'pending'").fetchone()[0]
    failed = conn.execute("SELECT count(*) FROM posting_outbox WHERE status = 'failed'").fetchone()[0]
    return {"lines": {s: by_status.get(s, 0) for s in STATUSES}, "pending_proposals": pending,
            "failed_postings": failed}


# ---- today -------------------------------------------------------------------------------------

# How long money has sat unmatched, counted from its value date: the day it reached the bank.
AGE_BANDS = (("0 to 2 days", 0, 2), ("3 to 7 days", 3, 7), ("Over 7 days", 8, None))

_STOPPED = """
SELECT o.id, o.line_id, l.channel, l.narration, o.amount_kobo, o.idempotency_key, o.status, o.attempts,
       o.last_error, o.next_attempt_at, o.allocations, o.core_transactions, last.action, last.at
FROM posting_outbox o JOIN payment_lines l ON l.id = o.line_id
LEFT JOIN LATERAL (
    SELECT a.action, a.at FROM audit_log a
    WHERE a.subject = 'payment_line:' || o.line_id AND left(a.action, 8) = 'posting.'
    ORDER BY a.id DESC LIMIT 1
) last ON true
WHERE o.status = 'failed' OR (o.status = 'pending' AND o.attempts > 0)
ORDER BY o.status, o.id
LIMIT 100
"""


def today(conn: psycopg.Connection, two_person_above_kobo: int) -> dict:
    """The morning page: the latest statement week at a glance, and the work waiting for a person.

    The week is the seven value dates ending on the latest line held, not the calendar's:
    statements arrive a day or more late, and a quiet morning shouldn't read as an empty week.
    The queues (review, unmatched money, postings that stopped) count everything, whenever it came.
    """
    on = bank_today()
    latest = conn.execute("SELECT max(value_date) FROM payment_lines").fetchone()[0]
    end = latest or on
    week = {"start": end - timedelta(days=6), "end": end}
    in_week = "l.value_date BETWEEN %(start)s AND %(end)s"

    by_status = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT l.status, count(*), coalesce(sum(l.amount_kobo), 0)::bigint FROM payment_lines l "
        f"WHERE {in_week} GROUP BY 1", week).fetchall()}
    # A line counts as matched by rule while a rule's proposal for it stands: pending or approved.
    by_rule = ("EXISTS (SELECT 1 FROM proposals p WHERE p.line_id = l.id AND p.proposed_by LIKE 'rule:%%' "
               "AND p.status IN ('pending', 'approved'))")
    channels = conn.execute(
        f"SELECT l.channel, count(*), count(*) FILTER (WHERE {by_rule}), coalesce(sum(l.amount_kobo), 0)::bigint "
        f"FROM payment_lines l WHERE {in_week} AND l.status <> 'duplicate' GROUP BY 1 ORDER BY 2 DESC, 1",
        week).fetchall()

    review = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE l.amount_kobo > %s), coalesce(sum(l.amount_kobo), 0)::bigint "
        "FROM proposals p JOIN payment_lines l ON l.id = p.line_id WHERE p.status = 'pending'",
        (two_person_above_kobo,)).fetchone()
    unmatched = conn.execute(
        "SELECT %s - l.value_date, l.amount_kobo FROM payment_lines l WHERE l.status IN ('new', 'suspense')",
        (on,)).fetchall()
    postings = dict(conn.execute(
        "SELECT CASE WHEN status = 'pending' AND attempts > 0 THEN 'retrying' WHEN status = 'pending' "
        "THEN 'waiting' ELSE status END, count(*) FROM posting_outbox "
        "WHERE status <> 'posted' OR (posted_at AT TIME ZONE %s)::date = %s GROUP BY 1",
        (BANK_TIMEZONE, on)).fetchall())
    stopped = conn.execute(_STOPPED).fetchall()

    received = [v for k, v in by_status.items() if k != "duplicate"]
    lines_received = sum(c for c, _ in received)
    return {
        "as_of": on.isoformat(),
        "week": {k: v.isoformat() for k, v in week.items()},
        "received": {"lines": lines_received, "amount_kobo": sum(a for _, a in received),
                     "duplicates": by_status.get("duplicate", (0, 0))[0]},
        "by_status": {s: {"lines": by_status.get(s, (0, 0))[0], "amount_kobo": by_status.get(s, (0, 0))[1]}
                      for s in STATUSES if s != "duplicate"},
        "matched_by_rule": sum(c[2] for c in channels),
        "channels": [{"channel": c[0], "lines": c[1], "by_rule": c[2], "amount_kobo": c[3]} for c in channels],
        "review": {"pending": review[0], "two_person": review[1], "amount_kobo": review[2],
                   "two_person_above_kobo": two_person_above_kobo},
        "unmatched": {
            "lines": len(unmatched), "amount_kobo": sum(a for _, a in unmatched),
            "oldest_days": max((d for d, _ in unmatched), default=None),
            "bands": [{"label": label,
                       "lines": sum(1 for d, _ in unmatched if lo <= d and (hi is None or d <= hi)),
                       "amount_kobo": sum(a for d, a in unmatched if lo <= d and (hi is None or d <= hi))}
                      for label, lo, hi in AGE_BANDS],
        },
        "postings": {
            "posted_today": postings.get("posted", 0), "waiting": postings.get("waiting", 0),
            "retrying": postings.get("retrying", 0), "failed": postings.get("failed", 0),
            "stopped": [{
                "outbox_id": r[0], "line_id": r[1], "channel": r[2], "narration": r[3], "amount_kobo": r[4],
                "idempotency_key": r[5], "status": r[6], "attempts": r[7], "last_error": r[8],
                "next_attempt_at": _iso(r[9]), "loan_refs": [a["loan_ref"] for a in r[10]],
                "posted_in_core": len(r[11] or []),
                "refused": r[12] == "posting.refused", "last_attempt_at": _iso(r[13]),
            } for r in stopped],
        },
    }
