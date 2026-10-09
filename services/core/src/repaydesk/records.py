"""Borrowers and loans, as the web app reads them.

Lists and search read the borrower directory (directory.py), Repaydesk's copy of the book. One
borrower's or one loan's page asks core banking live, because that is where a reviewer checks
before deciding, and a balance minutes old can mislead. If core banking doesn't answer, the page
says so and shows the directory's copy instead of failing.

Payments Repaydesk holds but hasn't posted yet (proposed, or approved and waiting for the posting
gateway) are shown beside what core banking has posted, so a borrower who paid is never shown as
unpaid while a reviewer catches up.
"""

from __future__ import annotations

import logging
import re

import psycopg

from repaydesk import directory
from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.match import loan_refs_in

log = logging.getLogger("repaydesk.records")

BORROWER_VIEWS = {
    "all": "true",
    "arrears": "agg.arrears_kobo > 0",
    "held": "held.n > 0",
}

LOAN_BANDS = {
    "all": "true",
    "late_1_7": "l.days_late BETWEEN 1 AND 7",
    "late_8_30": "l.days_late BETWEEN 8 AND 30",
    "late_31_90": "l.days_late BETWEEN 31 AND 90",
    "late_90": "l.days_late > 90",
    "closed": "l.status IN ('closed', 'overpaid')",
}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def phone_digits(text: str) -> str | None:
    """A Nigerian mobile number as the directory stores it (0 and ten digits), or None."""
    digits = re.sub(r"\D", "", text)
    if digits.startswith("234") and len(digits) == 13:
        digits = "0" + digits[3:]
    return digits if len(digits) == 11 and digits.startswith("0") else None


# Lines Repaydesk holds for a loan: a live proposal allocates to it and nothing is posted yet.
_HELD = """
SELECT a->>'loan_ref' AS loan_ref, l.id AS line_id, l.channel, l.value_date, (a->>'amount_kobo')::bigint AS amount_kobo,
       l.status, l.bank_reference
FROM proposals p
JOIN payment_lines l ON l.id = p.line_id
CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a
WHERE p.status IN ('pending', 'approved') AND l.status IN ('proposed', 'approved')
"""


def _held_by_loan(conn: psycopg.Connection, loan_refs: list[str]) -> dict[str, list[dict]]:
    if not loan_refs:
        return {}
    rows = conn.execute(f"SELECT * FROM ({_HELD}) h WHERE h.loan_ref = ANY(%s) ORDER BY value_date, line_id",
                        (loan_refs,)).fetchall()
    out: dict[str, list[dict]] = {}
    for loan_ref, line_id, channel, value_date, amount, status, bank_reference in rows:
        out.setdefault(loan_ref, []).append({"line_id": line_id, "channel": channel, "on": _iso(value_date),
                                             "amount_kobo": amount, "status": status, "reference": bank_reference})
    return out


# ---- borrowers -----------------------------------------------------------------------------------

_BORROWER_LIST = f"""
WITH agg AS (
    SELECT b.ref,
           coalesce(array_agg(l.ref ORDER BY l.disbursed_on DESC NULLS LAST, l.ref) FILTER (WHERE l.ref IS NOT NULL),
                    ARRAY[]::text[]) AS loan_refs,
           coalesce(sum(l.arrears_kobo), 0)::bigint AS arrears_kobo,
           coalesce(max(l.days_late), 0) AS days_late
    FROM borrowers b LEFT JOIN loans l ON l.borrower_ref = b.ref
    GROUP BY b.ref
), held AS (
    SELECT l.borrower_ref AS ref, count(*) AS n, sum(h.amount_kobo)::bigint AS amount_kobo
    FROM ({_HELD}) h JOIN loans l ON l.ref = h.loan_ref
    GROUP BY l.borrower_ref
)
SELECT b.ref, b.name, b.branch_name, b.business_name, b.phone, agg.loan_refs, agg.arrears_kobo, agg.days_late,
       coalesce(held.n, 0), coalesce(held.amount_kobo, 0), count(*) OVER ()
FROM borrowers b
JOIN agg ON agg.ref = b.ref
LEFT JOIN held ON held.ref = b.ref
WHERE {{view}} AND {{search}}
"""


def _borrower_search(q: str | None) -> tuple[str, dict]:
    q = (q or "").strip()
    if not q:
        return "true", {}
    args = {"q": f"%{q}%", "phone": phone_digits(q)}
    clause = ("(b.name ILIKE %(q)s OR b.business_name ILIKE %(q)s OR b.ref ILIKE %(q)s OR b.group_name ILIKE %(q)s "
              "OR array_to_string(agg.loan_refs, ' ') ILIKE %(q)s OR b.phone = %(phone)s)")
    return clause, args


def borrowers(conn: psycopg.Connection, view: str = "all", q: str | None = None, limit: int = 50,
              offset: int = 0) -> dict:
    search, args = _borrower_search(q)
    rows = conn.execute(
        _BORROWER_LIST.format(view=BORROWER_VIEWS[view], search=search)
        + " ORDER BY b.name, b.ref LIMIT %(limit)s OFFSET %(offset)s",
        args | {"limit": limit, "offset": offset},
    ).fetchall()
    counts = {}
    for v, cond in BORROWER_VIEWS.items():
        counts[v] = conn.execute(
            f"SELECT count(*) FROM ({_BORROWER_LIST.format(view=cond, search=search)}) x", args).fetchone()[0]
    return {
        "items": [{
            "ref": r[0], "name": r[1], "branch_name": r[2], "business_name": r[3], "phone": r[4], "loan_refs": r[5],
            "arrears_kobo": r[6], "days_late": r[7], "held": {"lines": r[8], "amount_kobo": r[9]},
        } for r in rows],
        "counts": counts,
        "total": rows[0][10] if rows else 0,
        "limit": limit, "offset": offset,
        "directory": directory.last_sync(conn),
    }


def borrower(conn: psycopg.Connection, ref: str, core: CoreBankingReader | None) -> dict | None:
    row = conn.execute(
        "SELECT ref, core_id, name, phone, branch_code, branch_name, business_name, group_ref, group_name, "
        "joined_on, active, copied_at FROM borrowers WHERE ref = %s", (ref,)).fetchone()
    if not row:
        return None
    b = dict(zip(("ref", "core_id", "name", "phone", "branch_code", "branch_name", "business_name", "group_ref",
                  "group_name", "joined_on", "active", "copied_at"), row, strict=True))
    b["joined_on"], b["copied_at"] = _iso(b["joined_on"]), _iso(b["copied_at"])
    loan_rows = conn.execute(
        "SELECT ref, core_id, product_name, principal_kobo, disbursed_on, status, outstanding_kobo, arrears_kobo, "
        "days_late FROM loans WHERE borrower_ref = %s ORDER BY disbursed_on DESC NULLS LAST, ref", (ref,)).fetchall()
    loans = [{"ref": r[0], "core_id": r[1], "product_name": r[2], "principal_kobo": r[3], "disbursed_on": _iso(r[4]),
              "status": r[5], "outstanding_kobo": r[6], "arrears_kobo": r[7], "days_late": r[8]} for r in loan_rows]

    live, posted = False, []
    if core is not None:
        try:
            found = core.get_borrower(ref)
            if found:
                b.update(name=found.name, phone=found.phone, branch_code=found.branch_code,
                         business_name=found.business_name)
                for loan in loans:
                    view = core.get_loan(loan["ref"])
                    if view:
                        loan.update(status=view.status, outstanding_kobo=view.outstanding,
                                    arrears_kobo=view.arrears, days_late=view.days_late)
                    posted += [_posted(r) for r in core.list_repayments(loan["ref"])]
                live = True
        except Exception:  # noqa: BLE001 - shown as "core banking didn't answer", with the directory's copy
            log.exception("core banking read failed for borrower %s", ref)

    held = _held_by_loan(conn, [loan["ref"] for loan in loans])
    for loan in loans:
        loan["held"] = held.get(loan["ref"], [])
    pending = [h | {"loan_ref": loan_ref} for loan_ref, items in held.items() for h in items]
    _link_posted(conn, posted)
    return b | {
        "loans": loans,
        "live": live,
        "payments": sorted(posted + [_pending(h) for h in pending], key=lambda p: (p["on"] or "", p["reference"]),
                           reverse=True),
        "directory": directory.last_sync(conn),
    }


def _posted(r) -> dict:
    return {"on": _iso(r.on), "loan_ref": r.loan_ref, "amount_kobo": r.amount, "channel": r.channel,
            "reference": r.reference, "status": "posted", "line_id": None}


def _pending(h: dict) -> dict:
    return {"on": h["on"], "loan_ref": h["loan_ref"], "amount_kobo": h["amount_kobo"], "channel": h["channel"],
            "reference": h["reference"], "status": h["status"], "line_id": h["line_id"]}


def _posting_key(reference: str) -> str | None:
    """The posting a Repaydesk repayment came from: `RD-P110`, or `RD-P110-2` for a split's second loan."""
    m = re.fullmatch(r"(RD-P\d+)(?:-\d+)?", reference or "")
    return m.group(1) if m else None


def _link_posted(conn: psycopg.Connection, posted: list[dict]) -> None:
    """A repayment Repaydesk posted carries its idempotency key as its reference: link it to its line."""
    keys = sorted({k for p in posted if (k := _posting_key(p["reference"]))})
    if not keys:
        return
    lines = dict(conn.execute("SELECT idempotency_key, line_id FROM posting_outbox WHERE idempotency_key = ANY(%s)",
                              (keys,)).fetchall())
    for p in posted:
        p["line_id"] = lines.get(_posting_key(p["reference"]))


# ---- loans -----------------------------------------------------------------------------------------

_LOAN_LIST = f"""
SELECT l.ref, l.borrower_ref, b.name, l.product_name, l.status, l.outstanding_kobo, l.arrears_kobo, l.days_late,
       coalesce(held.amount_kobo, 0), count(*) OVER ()
FROM loans l
LEFT JOIN borrowers b ON b.ref = l.borrower_ref
LEFT JOIN (SELECT loan_ref, sum(amount_kobo)::bigint AS amount_kobo FROM ({_HELD}) h GROUP BY loan_ref) held
       ON held.loan_ref = l.ref
WHERE {{band}} AND {{search}}
"""


def _loan_search(q: str | None) -> tuple[str, dict]:
    q = (q or "").strip()
    if not q:
        return "true", {}
    refs = loan_refs_in(q)
    return ("(l.ref ILIKE %(q)s OR l.ref = ANY(%(refs)s) OR b.name ILIKE %(q)s OR l.borrower_ref ILIKE %(q)s)",
            {"q": f"%{q}%", "refs": refs})


def loans(conn: psycopg.Connection, band: str = "all", q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    search, args = _loan_search(q)
    # The latest first in arrears, most overdue at the top: the order a collections officer works in.
    rows = conn.execute(
        _LOAN_LIST.format(band=LOAN_BANDS[band], search=search)
        + " ORDER BY (l.status = 'active') DESC, l.days_late DESC, l.arrears_kobo DESC, l.ref "
          "LIMIT %(limit)s OFFSET %(offset)s",
        args | {"limit": limit, "offset": offset},
    ).fetchall()
    counts = dict(conn.execute(
        "SELECT band, count(*) FROM (SELECT CASE "
        + " ".join(f"WHEN {cond} THEN '{name}'" for name, cond in LOAN_BANDS.items() if name != "all")
        + f" ELSE 'current' END AS band FROM loans l LEFT JOIN borrowers b ON b.ref = l.borrower_ref WHERE {search}) x "
          "GROUP BY band", args).fetchall())
    counts["all"] = sum(counts.values())
    counts["in_arrears"] = conn.execute(
        f"SELECT count(*) FROM loans l LEFT JOIN borrowers b ON b.ref = l.borrower_ref "
        f"WHERE l.arrears_kobo > 0 AND {search}", args).fetchone()[0]
    return {
        "items": [{
            "ref": r[0], "borrower_ref": r[1], "borrower_name": r[2], "product_name": r[3], "status": r[4],
            "outstanding_kobo": r[5], "arrears_kobo": r[6], "days_late": r[7], "held_kobo": r[8],
        } for r in rows],
        "counts": {k: counts.get(k, 0) for k in (*LOAN_BANDS, "current", "in_arrears")},
        "total": rows[0][9] if rows else 0,
        "limit": limit, "offset": offset,
        "directory": directory.last_sync(conn),
    }


def loan(conn: psycopg.Connection, ref: str, core: CoreBankingReader | None) -> dict | None:
    row = conn.execute(
        "SELECT l.ref, l.core_id, l.borrower_ref, b.name, b.core_id, l.product, l.product_name, l.principal_kobo, "
        "l.disbursed_on, l.status, l.outstanding_kobo, l.arrears_kobo, l.days_late, l.copied_at "
        "FROM loans l LEFT JOIN borrowers b ON b.ref = l.borrower_ref WHERE l.ref = %s", (ref,)).fetchone()
    if not row:
        return None
    out = dict(zip(("ref", "core_id", "borrower_ref", "borrower_name", "borrower_core_id", "product", "product_name",
                    "principal_kobo", "disbursed_on", "status", "outstanding_kobo", "arrears_kobo", "days_late",
                    "copied_at"), row, strict=True))
    out["disbursed_on"], out["copied_at"] = _iso(out["disbursed_on"]), _iso(out["copied_at"])
    va = conn.execute("SELECT account_number, provider FROM virtual_accounts WHERE loan_ref = %s", (ref,)).fetchone()
    out["virtual_account"] = {"number": va[0], "provider": va[1]} if va else None

    live, schedule, posted = False, None, []
    if core is not None:
        try:
            view = core.get_loan(ref)
            if view:
                out.update(status=view.status, outstanding_kobo=view.outstanding, arrears_kobo=view.arrears,
                           days_late=view.days_late, principal_kobo=view.principal)
                schedule = [{"number": i.number, "due_on": _iso(i.due_on), "due_kobo": i.total, "paid_kobo": i.paid}
                            for i in view.installments]
                posted = [_posted(r) for r in core.list_repayments(ref)]
                live = True
        except Exception:  # noqa: BLE001 - the page says core banking didn't answer
            log.exception("core banking read failed for loan %s", ref)

    held = [h | {"loan_ref": ref} for h in _held_by_loan(conn, [ref]).get(ref, [])]
    _link_posted(conn, posted)
    held_kobo = sum(h["amount_kobo"] for h in held)
    return out | {
        "live": live,
        "schedule": schedule,
        "held_kobo": held_kobo,
        "money_in": sorted(posted + [_pending(h) for h in held], key=lambda p: (p["on"] or "", p["reference"])),
        "directory": directory.last_sync(conn),
    }


# ---- the search box --------------------------------------------------------------------------------


def find(conn: psycopg.Connection, q: str) -> dict:
    """Where the search box should go. An exact loan reference, customer reference or phone opens
    that record; anything else searches payments, as before."""
    q = q.strip()
    refs = loan_refs_in(q)
    if len(refs) == 1 and conn.execute("SELECT 1 FROM loans WHERE ref = %s", (refs[0],)).fetchone():
        return {"to": "loan", "ref": refs[0]}
    hit = conn.execute("SELECT ref FROM borrowers WHERE upper(ref) = upper(%s)", (q,)).fetchone()
    if hit:
        return {"to": "borrower", "ref": hit[0]}
    phone = phone_digits(q)
    if phone:
        hits = conn.execute("SELECT ref FROM borrowers WHERE phone = %s LIMIT 2", (phone,)).fetchall()
        if len(hits) == 1:
            return {"to": "borrower", "ref": hits[0][0]}
    return {"to": "payments", "q": q}
