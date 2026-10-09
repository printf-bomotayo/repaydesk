"""Approving and rejecting proposals.

The rules, in one place:

- An auditor sees everything and decides nothing.
- Up to the two-person threshold (₦500,000), one approval settles a proposal. Above it, two
  different people must approve.
- The approval that settles a proposal needs someone among its approvers whose limit covers the
  amount. A first approval may come from anyone allowed to decide: an officer can vouch for a
  ₦2.5m line, and a supervisor completes it.
- Nobody decides the same proposal twice, and only a pending proposal can be decided.
- A rejection needs a reason. The line goes back to waiting, and rules never propose it again:
  a person has said the obvious answer is wrong, so it is the agent's to investigate.

An approved match goes into the posting outbox in the same transaction as the approval, so there
is never an approval the gateway can't see, or a posting nobody approved. An approved exclusion
moves no money and needs no posting: its line is simply marked excluded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from repaydesk import db, telemetry
from repaydesk.staff import Staff

TWO_PERSON_ABOVE_KOBO = int(os.getenv("TWO_PERSON_ABOVE_KOBO", 50_000_000))  # ₦500,000


class Refused(Exception):
    """A decision the rules don't allow. `code` is stable for the web app; `message` is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Prior:
    staff_id: str
    limit_kobo: int


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    settles: bool = False  # this approval is the last one needed
    code: str = ""
    message: str = ""


def naira(kobo: int) -> str:
    return f"₦{kobo // 100:,}" + (f".{kobo % 100:02d}" if kobo % 100 else "")


def approvals_needed(amount_kobo: int) -> int:
    return 2 if amount_kobo > TWO_PERSON_ABOVE_KOBO else 1


def judge(staff: Staff, amount_kobo: int, prior: list[Prior]) -> Verdict:
    """Whether `staff` may approve now, given the approvals already given. No database: the web app
    shows the same answer before anyone clicks."""
    if staff.read_only:
        return Verdict(False, code="read_only", message="Auditors can see the queue but not decide.")
    if any(p.staff_id == staff.id for p in prior):
        return Verdict(False, code="same_person",
                       message="You've approved this already. Someone else gives the second approval.")
    needed = approvals_needed(amount_kobo)
    if len(prior) + 1 < needed:
        return Verdict(True, settles=False)
    if max([p.limit_kobo for p in prior] + [staff.limit_kobo]) < amount_kobo:
        return Verdict(False, code="over_limit",
                       message=f"{naira(amount_kobo)} is above your limit"
                               + (" and the first approver's" if prior else "")
                               + ". Someone with a higher limit approves it.")
    return Verdict(True, settles=True)


@dataclass
class Outcome:
    proposal_id: int
    status: str  # first_approval, approved, rejected
    outbox_id: int | None = None


_PROPOSAL = (
    "SELECT p.id, p.status, p.kind, p.line_id, p.allocations, l.amount_kobo, l.value_date, l.status "
    "FROM proposals p JOIN payment_lines l ON l.id = p.line_id WHERE p.id = %s FOR UPDATE OF p, l"
)


def _prior(conn: psycopg.Connection, proposal_id: int) -> list[Prior]:
    rows = conn.execute(
        "SELECT staff_id, limit_kobo FROM decisions WHERE proposal_id = %s AND decision = 'approve' ORDER BY at",
        (proposal_id,),
    ).fetchall()
    return [Prior(*r) for r in rows]


def _load(conn: psycopg.Connection, proposal_id: int):
    row = conn.execute(_PROPOSAL, (proposal_id,)).fetchone()
    if not row:
        raise NotFound(proposal_id)
    if row[1] != "pending":
        raise Refused("not_pending", f"This proposal is already {row[1]}.")
    return row


def approve(conn: psycopg.Connection, proposal_id: int, staff: Staff) -> Outcome:
    trace_id = telemetry.current_trace_id()
    with conn.transaction():
        # The row locks make two reviewers clicking at once take turns: the second sees the first's decision.
        pid, _, kind, line_id, allocations, amount, value_date, _ = _load(conn, proposal_id)
        prior = _prior(conn, pid)
        verdict = judge(staff, amount, prior)
        if not verdict.allowed:
            raise Refused(verdict.code, verdict.message)
        if kind == "match" and sum(int(a["amount_kobo"]) for a in allocations) != amount:
            raise Refused("bad_split", "The proposed amounts don't add up to the payment. Reject it with a reason.")
        conn.execute(
            "INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo) VALUES (%s, %s, 'approve', %s)",
            (pid, staff.id, staff.limit_kobo),
        )
        subject = f"payment_line:{line_id}"
        if not verdict.settles:
            db.audit(conn, staff.id, "proposal.first_approval", subject,
                     {"proposal_id": pid, "amount_kobo": amount, "limit_kobo": staff.limit_kobo}, trace_id)
            return Outcome(pid, "first_approval")

        approvers = [p.staff_id for p in prior] + [staff.id]
        conn.execute("UPDATE proposals SET status = 'approved' WHERE id = %s", (pid,))
        db.audit(conn, staff.id, "proposal.approved", subject,
                 {"proposal_id": pid, "kind": kind, "amount_kobo": amount, "approved_by": approvers}, trace_id)
        if kind == "exclude":
            conn.execute("UPDATE payment_lines SET status = 'excluded' WHERE id = %s", (line_id,))
            return Outcome(pid, "approved")

        conn.execute("UPDATE payment_lines SET status = 'approved' WHERE id = %s", (line_id,))
        outbox_id = conn.execute(
            "INSERT INTO posting_outbox (proposal_id, line_id, idempotency_key, value_date, amount_kobo, "
            "allocations, approved_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (pid, line_id, f"RD-P{pid}", value_date, amount, Jsonb(allocations), approvers),
        ).fetchone()[0]
        db.audit(conn, "service:api", "posting.queued", subject,
                 {"proposal_id": pid, "outbox_id": outbox_id, "idempotency_key": f"RD-P{pid}"}, trace_id)
        return Outcome(pid, "approved", outbox_id)


def reject(conn: psycopg.Connection, proposal_id: int, staff: Staff, reason: str) -> Outcome:
    reason = (reason or "").strip()
    if staff.read_only:
        raise Refused("read_only", "Auditors can see the queue but not decide.")
    if not reason:
        raise Refused("reason_required", "Say why, so whoever looks next starts from your reason.")
    with conn.transaction():
        pid, _, _, line_id, _, amount, _, _ = _load(conn, proposal_id)
        if any(p.staff_id == staff.id for p in _prior(conn, pid)):
            raise Refused("same_person", "You approved this one. Ask someone else to reject it.")
        conn.execute(
            "INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo, reason) "
            "VALUES (%s, %s, 'reject', %s, %s)",
            (pid, staff.id, staff.limit_kobo, reason[:500]),
        )
        conn.execute("UPDATE proposals SET status = 'rejected' WHERE id = %s", (pid,))
        conn.execute("UPDATE payment_lines SET status = 'new' WHERE id = %s AND status = 'proposed'", (line_id,))
        db.audit(conn, staff.id, "proposal.rejected", f"payment_line:{line_id}",
                 {"proposal_id": pid, "amount_kobo": amount, "reason": reason[:500]}, telemetry.current_trace_id())
    return Outcome(pid, "rejected")


# ---- reading the queue -----------------------------------------------------------------------

_QUEUE = """
SELECT p.id, p.kind, p.proposed_by, p.confidence, p.allocations, p.evidence, p.created_at,
       l.id, l.channel, l.value_date, l.amount_kobo, l.narration, l.payer_name, l.customer_reference,
       coalesce((SELECT json_agg(json_build_object('staff_id', d.staff_id, 'name', s.name, 'limit_kobo',
                d.limit_kobo, 'at', d.at) ORDER BY d.at)
                 FROM decisions d JOIN staff s ON s.id = d.staff_id
                 WHERE d.proposal_id = p.id AND d.decision = 'approve'), '[]')
FROM proposals p JOIN payment_lines l ON l.id = p.line_id
WHERE p.status = 'pending' {where}
ORDER BY l.amount_kobo DESC, p.id
LIMIT %(limit)s OFFSET %(offset)s
"""

# The queue's views: everything, the payments that need two people, and proposed exclusions.
VIEWS = {
    "all": "",
    "two_person": "AND l.amount_kobo > %(threshold)s",
    "exclusions": "AND p.kind = 'exclude'",
}


def _item(row, staff: Staff) -> dict:
    (pid, kind, by, conf, allocations, evidence, created, line_id, channel, value_date, amount, narration,
     payer, customer_ref, approvals) = row
    verdict = judge(staff, amount, [Prior(a["staff_id"], a["limit_kobo"]) for a in approvals])
    return {
        "proposal_id": pid, "kind": kind, "proposed_by": by, "confidence": float(conf),
        "allocations": allocations, "evidence": evidence, "proposed_at": created.isoformat(),
        "line": {"id": line_id, "channel": channel, "value_date": value_date.isoformat(), "amount_kobo": amount,
                 "narration": narration, "payer_name": payer, "customer_reference": customer_ref},
        "approvals": [{"staff_id": a["staff_id"], "name": a["name"], "at": a["at"]} for a in approvals],
        "approvals_needed": approvals_needed(amount),
        "you": {"can_approve": verdict.allowed, "settles": verdict.settles, "refused": verdict.code or None,
                "message": verdict.message or None},
    }


def queue(conn: psycopg.Connection, staff: Staff, view: str = "all", limit: int = 50, offset: int = 0) -> dict:
    """Pending proposals, largest payment first, a page at a time, with each view's count."""
    args = {"limit": limit, "offset": offset, "threshold": TWO_PERSON_ABOVE_KOBO}
    rows = conn.execute(_QUEUE.format(where=VIEWS[view]), args).fetchall()
    counts = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE l.amount_kobo > %(threshold)s), "
        "count(*) FILTER (WHERE p.kind = 'exclude') "
        "FROM proposals p JOIN payment_lines l ON l.id = p.line_id WHERE p.status = 'pending'", args,
    ).fetchone()
    return {
        "items": [_item(r, staff) for r in rows],
        "counts": dict(zip(("all", "two_person", "exclusions"), counts, strict=True)),
        "total": counts[list(VIEWS).index(view)],
        "limit": limit, "offset": offset,
    }


def proposal(conn: psycopg.Connection, proposal_id: int, staff: Staff) -> dict:
    """One proposal in any status, with every decision on it, approvals and rejections both."""
    row = conn.execute(
        "SELECT p.id, p.kind, p.proposed_by, p.confidence, p.allocations, p.evidence, p.created_at, p.status, "
        "l.id, l.channel, l.value_date, l.amount_kobo, l.narration, l.payer_name, l.status "
        "FROM proposals p JOIN payment_lines l ON l.id = p.line_id WHERE p.id = %s", (proposal_id,),
    ).fetchone()
    if not row:
        raise NotFound(proposal_id)
    decisions = conn.execute(
        "SELECT d.staff_id, s.name, d.decision, d.reason, d.at, d.limit_kobo "
        "FROM decisions d JOIN staff s ON s.id = d.staff_id "
        "WHERE d.proposal_id = %s ORDER BY d.at", (proposal_id,),
    ).fetchall()
    outbox = conn.execute("SELECT id, idempotency_key, status FROM posting_outbox WHERE proposal_id = %s",
                          (proposal_id,)).fetchone()
    (pid, kind, by, conf, allocations, evidence, created, status,
     line_id, channel, vd, amount, narr, payer, lstatus) = row
    prior = [Prior(d[0], d[5]) for d in decisions if d[2] == "approve"]
    verdict = judge(staff, amount, prior) if status == "pending" else Verdict(False, code="not_pending")
    return {
        "proposal_id": pid, "kind": kind, "proposed_by": by, "confidence": float(conf), "status": status,
        "allocations": allocations, "evidence": evidence, "proposed_at": created.isoformat(),
        "line": {"id": line_id, "channel": channel, "value_date": vd.isoformat(), "amount_kobo": amount,
                 "narration": narr, "payer_name": payer, "status": lstatus},
        "decisions": [{"staff_id": d[0], "name": d[1], "decision": d[2], "reason": d[3], "at": d[4].isoformat()}
                      for d in decisions],
        "approvals_needed": approvals_needed(amount),
        "posting": {"outbox_id": outbox[0], "idempotency_key": outbox[1], "status": outbox[2]} if outbox else None,
        "you": {"can_approve": verdict.allowed, "refused": verdict.code or None, "message": verdict.message or None},
    }
