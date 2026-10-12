"""The bankbook: what someone investigating a payment may look up, and nothing they may change.

These are the investigator agent's tools (milestone 3), served over MCP by
`repaydesk.servers.bankbook`, and usable from Claude Desktop the same way. They read three places:

- the borrower directory, Repaydesk's copy of the book (fuzzy search over names, trading names and
  groups; up to 15 minutes old, and each answer says when it was copied);
- core banking, live, through the read-only login (a loan's balances and schedule, its repayments);
- Repaydesk's own lines and proposals (who has paid a loan before, under what name).

Nothing here writes: the database session is read-only and the Fineract login can't post. Every
answer passes through `repaydesk.privacy`, so phone and account numbers reach a model masked.
Money is in kobo, with a naira string beside it so nobody has to divide by a hundred.
"""

from __future__ import annotations

import re
from datetime import date

import psycopg

from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.domain import naira
from repaydesk.match import LINE_COLUMNS, _line, amounts_due, line_branch
from repaydesk.privacy import mask_account, mask_phone, mask_text, phones_in


class NotFound(LookupError):
    """Something asked for by reference doesn't exist. The message is for the model to read."""


def read_only(conn: psycopg.Connection) -> psycopg.Connection:
    """Every transaction on this session refuses writes, whatever a query tries."""
    conn.execute("SET default_transaction_read_only = on")
    return conn


def _money(kobo: int | None) -> dict | None:
    return None if kobo is None else {"kobo": kobo, "naira": naira(kobo)}


def _day(d: date | None) -> str | None:
    return d.isoformat() if d else None


# Names are matched three ways, scored by the best: how alike the two are as a whole
# (BAKARE BABAJIDE / Babajide Bakare), and how well either appears inside the other
# (EZE C / Chinedu Eze; IFEOMA PHARMACY / TRF FROM IFEOMA PHARMACY).
def _alike(field: str) -> str:
    return (f"greatest(public.similarity({field}, %(q)s), public.word_similarity(%(q)s, {field}), "
            f"public.word_similarity({field}, %(q)s))")


_SEARCH = f"""
SELECT b.ref, b.name, b.business_name, b.group_ref, b.group_name, b.branch_code, b.branch_name, b.phone, b.copied_at,
       greatest({_alike("lower(b.name)")}, {_alike("lower(coalesce(b.business_name, ''))")},
                {_alike("lower(coalesce(b.group_name, ''))")}) AS score,
       count(*) OVER () AS total
FROM borrowers b
WHERE b.active AND (lower(b.name) OPERATOR(public.%%) %(q)s OR %(q)s OPERATOR(public.<%%) lower(b.name)
   OR %(q)s OPERATOR(public.%%>) lower(b.name)
   OR lower(coalesce(b.business_name, '')) OPERATOR(public.%%) %(q)s
   OR %(q)s OPERATOR(public.<%%) lower(coalesce(b.business_name, ''))
   OR %(q)s OPERATOR(public.%%>) lower(coalesce(b.business_name, ''))
   OR %(q)s OPERATOR(public.<%%) lower(coalesce(b.group_name, '')))
  AND (%(branch)s::text IS NULL OR b.branch_code = %(branch)s)
ORDER BY score DESC, b.ref
LIMIT %(limit)s
"""

# Thresholds low enough to find an initialled or reordered name; the score orders what comes back.
_THRESHOLDS = ("SET LOCAL pg_trgm.similarity_threshold = 0.3; "
               "SET LOCAL pg_trgm.word_similarity_threshold = 0.4")


class Bankbook:
    def __init__(self, conn: psycopg.Connection, core: CoreBankingReader):
        self.conn = conn
        self.core = core

    # ---- the payment being investigated --------------------------------------------------------

    def payment_line(self, line_id: str) -> dict:
        """A payment line as it arrived, masked, with what Repaydesk has done with it so far."""
        row = self.conn.execute(f"SELECT {LINE_COLUMNS}, status, received_at FROM payment_lines WHERE id = %s",
                                (line_id,)).fetchone()
        if not row:
            raise NotFound(f"No payment line {line_id}.")
        line = _line(row[:12])
        proposals = self.conn.execute(
            "SELECT proposed_by, kind, status, allocations FROM proposals WHERE line_id = %s ORDER BY id",
            (line_id,)).fetchall()
        return {
            "line_id": line.line_id, "channel": line.channel.value, "value_date": _day(line.value_date),
            "amount": _money(line.amount), "narration": mask_text(line.narration), "payer_name": line.payer_name,
            "payer_account": mask_account(line.payer_account), "into_account": mask_account(line.source_account),
            "virtual_account": mask_account(line.virtual_account), "terminal_id": line.terminal_id,
            "reference_typed": mask_text(line.customer_reference), "branch_code": line_branch(line),
            "has_phone": bool(phones_in(line.customer_reference, line.narration)),
            "status": row[12],
            "earlier_proposals": [{"by": p[0], "kind": p[1], "status": p[2],
                                   "loans": [a["loan_ref"] for a in p[3]]} for p in proposals],
        }

    # ---- borrowers -----------------------------------------------------------------------------

    def _loans_of(self, borrower_refs: list[str]) -> dict[str, list[dict]]:
        rows = self.conn.execute(
            "SELECT borrower_ref, ref, product_name, status, outstanding_kobo, arrears_kobo, days_late FROM loans "
            "WHERE borrower_ref = ANY(%s) ORDER BY ref", (borrower_refs,)).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r[0], []).append({"loan_ref": r[1], "product": r[2], "status": r[3],
                                             "outstanding": _money(r[4]), "arrears": _money(r[5]),
                                             "days_late": r[6]})
        return out

    def _borrowers(self, rows: list[tuple], scores: list[float | None] | None = None) -> list[dict]:
        loans = self._loans_of([r[0] for r in rows])
        found = []
        for i, r in enumerate(rows):
            ref, name, business, group_ref, group_name, branch_code, branch_name, phone, copied = r[:9]
            item = {"borrower_ref": ref, "name": name, "trading_name": business, "group_ref": group_ref,
                    "group_name": group_name, "branch_code": branch_code, "branch_name": branch_name,
                    "phone": mask_phone(phone), "loans": loans.get(ref, []), "directory_copied_at": copied.isoformat()}
            if scores and scores[i] is not None:
                item["match_score"] = round(float(scores[i]), 2)
            found.append(item)
        return found

    def search_borrowers(self, text: str, branch_code: str | None = None, limit: int = 12) -> dict:
        q = " ".join(text.lower().split())
        if len(q) < 2:
            raise NotFound("Search for at least two letters of a name, trading name or group.")
        with self.conn.transaction():
            self.conn.execute(_THRESHOLDS)
            rows = self.conn.execute(_SEARCH, {"q": q, "branch": branch_code, "limit": min(max(limit, 1), 25)}
                                     ).fetchall()
        total = rows[0][10] if rows else 0
        found = {"searched_for": text, "branch_code": branch_code, "matches": total, "shown": len(rows),
                 "borrowers": self._borrowers(rows, [r[9] for r in rows])}
        if total > len(rows):
            # Said plainly, because a list cut short reads like a complete one.
            found["more_not_shown"] = (f"{total - len(rows)} more borrowers match. Ask again with a larger limit "
                                       f"(up to 25) or a branch_code before concluding anything about all of them.")
        return found

    def borrowers_by_line_phone(self, line_id: str) -> dict:
        """Borrowers whose phone is the one typed on a payment line. The number stays here."""
        row = self.conn.execute("SELECT customer_reference, narration FROM payment_lines WHERE id = %s",
                                (line_id,)).fetchone()
        if not row:
            raise NotFound(f"No payment line {line_id}.")
        phones = phones_in(*row)
        if not phones:
            return {"line_id": line_id, "phones_on_line": 0, "borrowers": []}
        rows = self.conn.execute(
            "SELECT ref, name, business_name, group_ref, group_name, branch_code, branch_name, phone, copied_at "
            "FROM borrowers WHERE phone = ANY(%s) ORDER BY ref", (phones,)).fetchall()
        return {"line_id": line_id, "phones_on_line": len(phones), "borrowers": self._borrowers(rows)}

    def borrower(self, borrower_ref: str) -> dict:
        rows = self.conn.execute(
            "SELECT ref, name, business_name, group_ref, group_name, branch_code, branch_name, phone, copied_at "
            "FROM borrowers WHERE ref = %s", (borrower_ref,)).fetchall()
        if not rows:
            raise NotFound(f"No borrower {borrower_ref} in the directory.")
        return self._borrowers(rows)[0]

    def group(self, group_ref: str) -> dict:
        rows = self.conn.execute(
            "SELECT ref, name, business_name, group_ref, group_name, branch_code, branch_name, phone, copied_at "
            "FROM borrowers WHERE group_ref = %s ORDER BY name", (group_ref,)).fetchall()
        if not rows:
            raise NotFound(f"No group {group_ref} in the directory.")
        return {"group_ref": group_ref, "group_name": rows[0][4], "members": self._borrowers(rows)}

    # ---- loans ---------------------------------------------------------------------------------

    def loan(self, loan_ref: str) -> dict:
        """A loan read live from core banking: balances, what's due next, and amounts that would fit."""
        view = self.core.get_loan(loan_ref)
        if not view:
            raise NotFound(f"No loan {loan_ref} in core banking.")
        name = self.conn.execute("SELECT name FROM borrowers WHERE ref = %s", (view.borrower_ref,)).fetchone()
        unpaid = [i for i in view.installments if i.outstanding > 0]
        return {
            "loan_ref": view.ref, "borrower_ref": view.borrower_ref, "borrower_name": name[0] if name else None,
            "product": view.product, "status": view.status, "disbursed_on": _day(view.disbursed_on),
            "principal": _money(view.principal), "outstanding": _money(view.outstanding),
            "arrears": _money(view.arrears), "days_late": view.days_late,
            "next_instalments": [{"number": i.number, "due_on": _day(i.due_on), "due": _money(i.total),
                                  "still_owed": _money(i.outstanding)} for i in unpaid[:3]],
            "amounts_that_would_fit": [_money(a) for a in sorted(amounts_due(view))],
            "read_live_from_core_banking": True,
        }

    def loans_fitting(self, amount_kobo: int, refs: list[str]) -> dict:
        """Which of these borrowers' (or loans') active loans an amount would fit, read live: the next
        instalment, a whole instalment, the arrears, or the full balance. One call checks every
        candidate, so "no other loan fits" can be said only after looking."""
        refs = list(dict.fromkeys(r.strip() for r in refs if r.strip()))[:25]
        if not refs:
            raise NotFound("Give borrower references (CUS-...) or loan references (LN-...) to check.")
        rows = self.conn.execute(
            "SELECT l.ref, l.borrower_ref, b.name, b.branch_code FROM loans l "
            "JOIN borrowers b ON b.ref = l.borrower_ref "
            "WHERE (l.ref = ANY(%(refs)s) OR l.borrower_ref = ANY(%(refs)s)) AND l.status = 'active' ORDER BY l.ref",
            {"refs": refs}).fetchall()
        checked = []
        for loan_ref, borrower_ref, name, branch in rows:
            view = self.core.get_loan(loan_ref)
            if not view or view.status != "active":
                continue
            unpaid = [i for i in view.installments if i.outstanding > 0]
            named = {"next instalment still owed": unpaid[0].outstanding if unpaid else None,
                     "a whole instalment": unpaid[0].total if unpaid else None,
                     "the arrears": view.arrears or None, "the full balance": view.outstanding or None}
            checked.append({"loan_ref": loan_ref, "borrower_ref": borrower_ref, "borrower_name": name,
                            "branch_code": branch, "fits_as": [k for k, v in named.items() if v == amount_kobo],
                            "arrears": _money(view.arrears), "next_instalment": _money(named["a whole instalment"])})
        return {"amount": _money(amount_kobo), "asked_about": refs, "active_loans_checked": len(checked),
                "fitting": [c for c in checked if c["fits_as"]],
                "not_fitting": [c for c in checked if not c["fits_as"]]}

    def nearby_loan_refs(self, loan_ref: str) -> dict:
        """Loans whose reference is one slip away from `loan_ref`: one digit different, or two neighbouring
        digits swapped. A quoted reference whose borrower doesn't match the payer is often one of these."""
        m = re.fullmatch(r"LN-(\d{4})-(\d{6})", loan_ref.strip().upper())
        if not m:
            raise NotFound("A loan reference looks like LN-2026-001509.")
        year, digits = m.groups()
        near = set()
        for i in range(6):
            for d in "0123456789":
                if d != digits[i]:
                    near.add(digits[:i] + d + digits[i + 1:])
            if i < 5 and digits[i] != digits[i + 1]:
                near.add(digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:])
        rows = self.conn.execute(
            "SELECT l.ref, l.status, b.ref, b.name, b.business_name, b.branch_code, b.branch_name FROM loans l "
            "JOIN borrowers b ON b.ref = l.borrower_ref WHERE l.ref = ANY(%s) ORDER BY l.ref",
            ([f"LN-{year}-{n}" for n in sorted(near)],)).fetchall()
        quoted = self.conn.execute(
            "SELECT b.name, b.branch_code FROM loans l JOIN borrowers b ON b.ref = l.borrower_ref WHERE l.ref = %s",
            (loan_ref.strip().upper(),)).fetchone()
        owner = {"name": quoted[0], "branch_code": quoted[1]} if quoted else None
        return {"quoted": loan_ref, "quoted_belongs_to": owner,
                "one_slip_away": [{"loan_ref": r[0], "status": r[1], "borrower_ref": r[2], "name": r[3],
                                   "trading_name": r[4], "branch_code": r[5], "branch_name": r[6]} for r in rows]}

    def payment_history(self, loan_ref: str, limit: int = 10) -> dict:
        """How a loan has been paid: repayments core banking holds, and the lines Repaydesk matched to it,
        with the payer's name and words as each line arrived."""
        posted = self.core.list_repayments(loan_ref)
        if not posted and not self.core.get_loan(loan_ref):
            raise NotFound(f"No loan {loan_ref} in core banking.")
        held = self.conn.execute(
            "SELECT l.id, l.value_date, (a->>'amount_kobo')::bigint, l.channel, l.payer_name, l.narration, "
            "l.status, p.proposed_by, p.status FROM proposals p JOIN payment_lines l ON l.id = p.line_id "
            "CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a "
            "WHERE a->>'loan_ref' = %s AND p.status IN ('pending', 'approved') "
            "ORDER BY l.value_date DESC, l.id LIMIT %s", (loan_ref, limit)).fetchall()
        return {
            "loan_ref": loan_ref,
            "repayments_in_core_banking": [
                {"on": _day(r.on), "amount": _money(r.amount), "channel": r.channel, "reference": r.reference}
                for r in sorted(posted, key=lambda r: r.on, reverse=True)[:limit]],
            "lines_matched_by_repaydesk": [
                {"line_id": h[0], "value_date": _day(h[1]), "amount": _money(h[2]), "channel": h[3],
                 "payer_name": h[4], "narration": mask_text(h[5]), "line_status": h[6], "proposed_by": h[7],
                 "decided": h[8] == "approved"} for h in held],
        }

    def find_payments(self, text: str, limit: int = 10) -> dict:
        """Earlier payments whose payer or narration reads like `text`, and the loans they were matched to.
        A payer who has paid before under the same name is strong evidence; one a person rejected is not."""
        q = " ".join(text.lower().split())
        if len(q) < 3:
            raise NotFound("Search for at least three letters of a payer's name or narration.")
        field = "lower(coalesce(l.payer_name, '') || ' ' || l.narration)"
        with self.conn.transaction():
            self.conn.execute(_THRESHOLDS)
            rows = self.conn.execute(
                f"SELECT l.id, l.value_date, l.amount_kobo, l.channel, l.payer_name, l.narration, p.allocations, "
                f"p.status, p.proposed_by, public.word_similarity(%(q)s, {field}) AS score "
                f"FROM payment_lines l JOIN proposals p ON p.line_id = l.id AND p.status IN ('pending', 'approved') "
                f"WHERE %(q)s OPERATOR(public.<%%) {field} "
                f"ORDER BY score DESC, l.value_date DESC LIMIT %(limit)s",
                {"q": q, "limit": min(max(limit, 1), 25)}).fetchall()
        return {"searched_for": text, "payments": [
            {"line_id": r[0], "value_date": _day(r[1]), "amount": _money(r[2]), "channel": r[3], "payer_name": r[4],
             "narration": mask_text(r[5]), "matched_to": [a["loan_ref"] for a in r[6]], "decided": r[7] == "approved",
             "proposed_by": r[8], "match_score": round(float(r[9]), 2)} for r in rows]}
