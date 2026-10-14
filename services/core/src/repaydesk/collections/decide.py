"""A portfolio manager's decisions on drafted messages: approve (perhaps after editing), or reject.

Approval is where the policy is enforced for good. The message, as it will be sent, is checked
again against facts read live at that moment: the arrears now (a part-payment since drafting changes
the amount), the messages this week, any promise since. A message that fails a check can't be
approved; the manager edits it until it passes, or rejects it. An approved message gets the
borrower's number from the directory, a key the provider dedupes on, and the first moment inside
contact hours it may go; the gateway sends it (ADR 0002: Repaydesk's API never talks to the
outside world itself).
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.approvals import NotFound, Refused
from repaydesk.collections import drafter, policy
from repaydesk.staff import Staff

STATUSES = ("draft", "approved", "rejected", "sent", "failed")


def _may_decide(who: Staff) -> None:
    if not who.decides_messages:
        raise Refused("not_allowed", "Only portfolio managers and supervisors approve messages.")


def _locked(conn: psycopg.Connection, message_id: int) -> tuple:
    row = conn.execute(
        "SELECT m.id, m.status, m.loan_ref, m.borrower_ref, m.bucket, m.channel, m.body, b.name, b.phone "
        "FROM collection_messages m LEFT JOIN borrowers b ON b.ref = m.borrower_ref WHERE m.id = %s "
        "FOR UPDATE OF m", (message_id,)).fetchone()
    if not row:
        raise NotFound
    if row[1] != "draft":
        raise Refused("not_draft", f"This message is already {row[1]}.")
    return row


def approve(conn: psycopg.Connection, core: CoreBankingReader | None, message_id: int, who: Staff,
            body: str | None = None, now: datetime | None = None) -> dict:
    """Approve a draft for sending, as it stands or as edited. Refused with the failed checks if the
    message, checked again against the loan now, breaks the policy."""
    _may_decide(who)
    if core is None:
        raise Refused("core_unavailable", "Core banking can't be reached to check the amount due; try again.")
    now = now or datetime.now(UTC)
    with conn.transaction():
        m_id, _, loan_ref, borrower_ref, bucket, channel, drafted, name, phone = _locked(conn, message_id)
        text = (body if body is not None else drafted).strip()
        if not text:
            raise Refused("empty", "The message is empty.")
        if not phone:
            raise Refused("no_phone", "The directory has no phone number for this borrower.")
        case = drafter.gather(conn, core, {"loan_ref": loan_ref, "borrower_ref": borrower_ref,
                                           "borrower_name": name or "", "bucket": bucket})
        if case is None:
            raise Refused("nothing_due", "Core banking shows nothing due on this loan now. Reject the message.")
        checks = policy.check(text, channel, case.facts())
        if not policy.sendable(checks):
            failed = "; ".join(c["detail"] for c in checks if not c["ok"])
            raise Refused("fails_policy", f"The message breaks the collections policy: {failed}.")
        send_after = policy.next_contact_window(now)
        edited = text != drafted
        conn.execute(
            "UPDATE collection_messages SET status = 'approved', body = %s, drafted_body = %s, checks = %s, "
            "amount_due_kobo = %s, decided_by = %s, decided_at = %s, idempotency_key = %s, to_phone = %s, "
            "send_after = %s WHERE id = %s",
            (text, drafted if edited else None, Jsonb(checks), case.amount_due_kobo, who.id, now,
             f"CM-{m_id}", phone, send_after, m_id))
        db.audit(conn, who.id, "message.approved", f"loan:{loan_ref}",
                 {"message_id": m_id, "edited": edited, "amount_due_kobo": case.amount_due_kobo,
                  "send_after": send_after.isoformat()}, telemetry.current_trace_id())
    return {"message_id": m_id, "status": "approved", "edited": edited, "send_after": send_after.isoformat()}


def reject(conn: psycopg.Connection, message_id: int, who: Staff, reason: str) -> dict:
    _may_decide(who)
    if not (reason or "").strip():
        raise Refused("reason_required", "Say why, so the next draft can be better.")
    with conn.transaction():
        m_id, _, loan_ref, *_ = _locked(conn, message_id)
        conn.execute("UPDATE collection_messages SET status = 'rejected', decided_by = %s, decided_at = now(), "
                     "reject_reason = %s WHERE id = %s", (who.id, reason.strip(), m_id))
        db.audit(conn, who.id, "message.rejected", f"loan:{loan_ref}", {"message_id": m_id, "reason": reason.strip()},
                 telemetry.current_trace_id())
    return {"message_id": m_id, "status": "rejected"}


def _iso(v):
    return v.isoformat() if v else None


def messages(conn: psycopg.Connection, status: str = "draft", limit: int = 50, offset: int = 0) -> dict:
    """Messages by status, oldest draft first (first come, first decided); others newest first."""
    order = "m.id" if status == "draft" else "coalesce(m.sent_at, m.decided_at, m.created_at) DESC, m.id DESC"
    rows = conn.execute(
        "SELECT m.id, m.loan_ref, m.borrower_ref, b.name, b.branch_name, m.bucket, m.channel, m.body, "
        "m.amount_due_kobo, m.checks, m.drafted_by, m.run_id, r.verdict->>'note', m.created_at, m.status, "
        "m.decided_by, s.name, m.decided_at, m.reject_reason, m.send_after, m.sent_at, m.provider_id, m.error, "
        "m.attempts, m.drafted_body, l.days_late, count(*) OVER () "
        "FROM collection_messages m LEFT JOIN borrowers b ON b.ref = m.borrower_ref "
        "LEFT JOIN loans l ON l.ref = m.loan_ref LEFT JOIN agent_runs r ON r.id = m.run_id "
        "LEFT JOIN staff s ON s.id = m.decided_by "
        f"WHERE m.status = %s ORDER BY {order} LIMIT %s OFFSET %s", (status, limit, offset)).fetchall()
    counts = dict(conn.execute("SELECT status, count(*) FROM collection_messages GROUP BY 1").fetchall())
    return {
        "items": [{
            "id": r[0], "loan_ref": r[1], "borrower_ref": r[2], "borrower_name": r[3], "branch_name": r[4],
            "bucket": r[5], "bucket_label": policy.BUCKET_LABEL.get(r[5], r[5]), "channel": r[6], "body": r[7],
            "amount_due_kobo": r[8], "checks": r[9] or [], "passes_policy": policy.sendable(r[9] or []),
            "drafted_by": r[10], "run_id": r[11], "note": r[12], "created_at": _iso(r[13]), "status": r[14],
            "decided_by": r[15], "decided_by_name": r[16], "decided_at": _iso(r[17]), "reject_reason": r[18],
            "send_after": _iso(r[19]), "sent_at": _iso(r[20]), "provider_id": r[21], "error": r[22],
            "attempts": r[23], "drafted_body": r[24], "days_late": r[25],
        } for r in rows],
        "total": rows[0][26] if rows else 0,
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "limit": limit, "offset": offset,
    }
