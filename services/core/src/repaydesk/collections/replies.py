"""Borrowers' replies: read each one, and turn a reply with a date into a promise to pay.

The reader is rules, not a model: a reply is short, the cases that matter are few, and a promise is
a commitment Repaydesk will hold the borrower to, so it should come only from words a person could
point to. It reads English and Nigerian Pidgin ("I go pay Friday", "I don pay"):

- a date and an intent to pay ("I will pay on Friday", "Friday", "15/10", "end of the month") is a
  **promise**: recorded on the loan the last message was about, for the amount named in the reply,
  or else the amount that message asked for. The borrower isn't messaged again before that date.
- "I have paid" / "I don pay" is **says_paid**: a person checks for the payment.
- a question, or "call me", is a **question**; "stop" is **stop**; anything else is **unclear**.

Everything but a promise waits for a person, who can record the promise themselves or close it
with a note. A reply from a number the directory doesn't know is **unknown_number**.

The policy's rule for a promise: a date from today up to a month away. Further than that is a plan
to agree with a person, not a promise to record.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from repaydesk import db, telemetry
from repaydesk.approvals import NotFound, Refused
from repaydesk.collections import policy
from repaydesk.staff import Staff

ACTOR = "rule:replies"
FURTHEST_PROMISE = timedelta(days=31)

_WEEKDAYS = {name: i for i, names in enumerate(
    (("monday", "mon"), ("tuesday", "tue", "tues"), ("wednesday", "wed"), ("thursday", "thu", "thur", "thurs"),
     ("friday", "fri"), ("saturday", "sat"), ("sunday", "sun"))) for name in names}
_MONTHS = {name: i for i, names in enumerate(
    (("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
     ("december", "dec")), start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

_PAID = re.compile(r"\b(i have paid|i've paid|ive paid|i paid|already paid|have paid|i don pay|don pay|"
                   r"i have sent|i've sent|i sent the money|i have transferred|i've transferred|i transferred|"
                   r"payment (?:has been )?made)\b")
_STOP = re.compile(r"\b(stop|unsubscribe|don'?t (?:message|text|contact)|do not (?:message|text|contact)|"
                   r"leave me alone)\b")
# Paying words only: "I will call you on Friday" names a date, but isn't a promise to pay.
_INTENT = re.compile(r"\b(pay|paying|payment|pai|send|sending|transfer|transferring|settle|clear|balance|remit|"
                     r"promise)\b")
_QUESTION = re.compile(r"^\s*(how|what|why|when|where|who|which|can|could|is|are|do|does)\b|\?|\bcall me\b")


@dataclass(frozen=True)
class Reading:
    kind: str  # promise | says_paid | question | stop | unclear
    due_on: date | None = None
    amount_kobo: int | None = None  # named in the reply; None means the amount asked for
    why: str = ""

    def as_json(self) -> dict:
        d = asdict(self)
        d["due_on"] = self.due_on.isoformat() if self.due_on else None
        return d


def _amounts(text: str) -> set[int]:
    found = policy._amounts_in(text)
    for m in re.finditer(r"\b(\d{1,4})\s?k\b", text, re.I):  # "20k": twenty thousand naira
        found.add(int(m.group(1)) * 1000 * 100)
    return found


def _day_in_month(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _this_or_next_year(today: date, month: int, day: int) -> date | None:
    """A day and month without a year: this year's, unless that was more than six months ago (a
    reply in December naming 5 January means next year). A recent past date stays past."""
    d = _day_in_month(today.year, month, day)
    if d and (today - d).days > 183:
        d = _day_in_month(today.year + 1, month, day)
    return d


def _date_in(text: str, today: date) -> tuple[date | None, str]:
    """The date the reply names, and the words that named it. (None, "") if there isn't one."""
    if m := re.search(r"\bnext tomorrow\b", text):  # Pidgin: the day after tomorrow
        return today + timedelta(days=2), m.group(0)
    if m := re.search(r"\b(tomorrow|tomorow|tomoro|tommorow|tmrw|tmr)\b", text):
        return today + timedelta(days=1), m.group(0)
    if m := re.search(r"\b(today|tonight|this evening|later today)\b", text):
        return today, m.group(0)
    if m := re.search(r"\b(end of (?:the |this )?month|month[- ]end)\b", text):
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]), m.group(0)
    # 15/10, 15-10, 15/10/2026, 15.10.26
    if m := re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", text):
        day, month = int(m.group(1)), int(m.group(2))
        if not m.group(3):
            return _this_or_next_year(today, month, day), m.group(0)
        year = int(m.group(3))
        return _day_in_month(year + 2000 if year < 100 else year, month, day), m.group(0)
    # 15th October, 15 oct, October 15th, oct 15
    if m := (re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\b", text)
             or re.search(rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)):
        day_s, month_s = (m.group(1), m.group(2)) if m.group(1).isdigit() else (m.group(2), m.group(1))
        return _this_or_next_year(today, _MONTHS[month_s], int(day_s)), m.group(0)
    # next friday, on friday, friday
    if m := re.search(rf"\b(next\s+)?({_WEEKDAY})\b", text):
        ahead = (_WEEKDAYS[m.group(2)] - today.weekday()) % 7
        return today + timedelta(days=ahead + (7 if m.group(1) else 0)), m.group(0)
    # the 15th, 25th: this month, or next month if that's past
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", text):
        d = _day_in_month(today.year, today.month, int(m.group(1)))
        if d and d < today:
            nxt = today.replace(day=1) + timedelta(days=32)
            d = _day_in_month(nxt.year, nxt.month, int(m.group(1)))
        return d, m.group(0)
    return None, ""


def read(text: str, today: date) -> Reading:
    """What a borrower's reply says, by rules a person can check."""
    t = " ".join(text.lower().replace("’", "'").split())
    if _STOP.search(t):
        return Reading("stop", why="asks not to be messaged")
    when, said = _date_in(t, today)
    if _PAID.search(t):
        if when:
            return Reading("unclear", why=f"says it has paid, and names a date ({said}): a person checks which")
        return Reading("says_paid", why="says it has paid: a person checks for the payment")
    amounts = _amounts(text)
    if when is not None:
        short = len(t.split()) <= 4  # "Friday", "on Friday please"
        if not (_INTENT.search(t) or short):
            return Reading("unclear", why=f"names a date ({said}) but not paying")
        if when < today:
            return Reading("unclear", why=f"names a date already past ({said})")
        if when - today > FURTHEST_PROMISE:
            return Reading("unclear", why=f"names a date more than a month away ({said}): a plan for a person to agree")
        if len(amounts) > 1:
            return Reading("unclear", why="names more than one amount")
        amount = next(iter(amounts), None)
        return Reading("promise", when, amount, f"names a date ({said})" + (" and an amount" if amount else ""))
    if _QUESTION.search(t):
        return Reading("question", why="asks something")
    return Reading("unclear", why="no date, question or request found")


# ---- recording -------------------------------------------------------------------------------


def _record_promise(conn: psycopg.Connection, inbound_id: int, loan_ref: str, borrower_ref: str, due_on: date,
                    amount_kobo: int, today: date, recorded_by: str, source: str) -> int:
    """A new open promise on the loan. One promise stands at a time: an earlier open one is replaced."""
    conn.execute("UPDATE promises SET status = 'cancelled', resolved_at = now(), note = 'replaced by a later promise' "
                 "WHERE loan_ref = %s AND status = 'open'", (loan_ref,))
    promise_id = conn.execute(
        "INSERT INTO promises (loan_ref, borrower_ref, amount_kobo, due_on, made_on, source, inbound_id, recorded_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (loan_ref, borrower_ref, amount_kobo, due_on, today, source, inbound_id, recorded_by)).fetchone()[0]
    db.audit(conn, recorded_by, "promise.recorded", f"loan:{loan_ref}",
             {"promise_id": promise_id, "inbound_id": inbound_id, "due_on": due_on.isoformat(),
              "amount_kobo": amount_kobo, "source": source}, telemetry.current_trace_id())
    return promise_id


def receive(conn: psycopg.Connection, provider_id: str, channel: str, from_phone: str, body: str,
            today: date) -> dict:
    """Store a reply once (a redelivery is the same reply), work out what it answers, and read it."""
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO inbound_messages (channel, from_phone, body, provider_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (provider_id) DO NOTHING RETURNING id", (channel, from_phone, body, provider_id)).fetchone()
        if row is None:
            existing = conn.execute("SELECT id, outcome FROM inbound_messages WHERE provider_id = %s",
                                    (provider_id,)).fetchone()
            return {"inbound_id": existing[0], "outcome": existing[1], "duplicate": True}
        inbound_id = row[0]
        borrower = conn.execute("SELECT ref FROM borrowers WHERE phone = %s ORDER BY ref LIMIT 1",
                                (from_phone,)).fetchone()
        answered = conn.execute(
            "SELECT id, loan_ref, amount_due_kobo FROM collection_messages WHERE to_phone = %s AND status = 'sent' "
            "ORDER BY sent_at DESC LIMIT 1", (from_phone,)).fetchone()
        if borrower is None:
            reading, note = Reading("unclear", why="from a number the directory doesn't know"), None
            outcome = "unknown_number"
        else:
            reading = read(body, today)
            outcome, note = reading.kind, None
        loan_ref = answered[1] if answered else None
        conn.execute("UPDATE inbound_messages SET borrower_ref = %s, message_id = %s, loan_ref = %s, reading = %s, "
                     "outcome = %s WHERE id = %s",
                     (borrower[0] if borrower else None, answered[0] if answered else None, loan_ref,
                      Jsonb(reading.as_json()), outcome, inbound_id))
        db.audit(conn, "service:api", "reply.received", f"loan:{loan_ref}" if loan_ref else f"phone:{from_phone}",
                 {"inbound_id": inbound_id, "outcome": outcome, "why": reading.why}, telemetry.current_trace_id())
        promise_id = None
        if outcome == "promise":
            if answered is None:  # nothing to hold it to: a person decides which loan
                outcome, note = "unclear", "a promise, but no message was sent to this number to say which loan"
                conn.execute("UPDATE inbound_messages SET outcome = 'unclear', note = %s WHERE id = %s",
                             (note, inbound_id))
            else:
                promise_id = _record_promise(conn, inbound_id, loan_ref, borrower[0], reading.due_on,
                                             reading.amount_kobo or answered[2], today, ACTOR, "reply")
                conn.execute("UPDATE inbound_messages SET handled_at = now(), handled_by = %s WHERE id = %s",
                             (ACTOR, inbound_id))
    return {"inbound_id": inbound_id, "outcome": outcome, "promise_id": promise_id, "duplicate": False}


def _may_handle(who: Staff) -> None:
    if not who.decides_messages:
        raise Refused("not_allowed", "Only portfolio managers and supervisors deal with replies.")


def _waiting(conn: psycopg.Connection, inbound_id: int) -> tuple:
    row = conn.execute("SELECT id, loan_ref, borrower_ref, handled_at FROM inbound_messages WHERE id = %s FOR UPDATE",
                       (inbound_id,)).fetchone()
    if not row:
        raise NotFound
    if row[3] is not None:
        raise Refused("already_handled", "Someone has already dealt with this reply.")
    return row


def record_promise(conn: psycopg.Connection, inbound_id: int, who: Staff, due_on: date, amount_kobo: int,
                   today: date, loan_ref: str | None = None) -> dict:
    """A person reads the reply as a promise and records it: when Repaydesk couldn't, or wouldn't."""
    _may_handle(who)
    if amount_kobo <= 0:
        raise Refused("bad_amount", "The amount must be more than nothing.")
    if due_on < today or due_on - today > FURTHEST_PROMISE:
        raise Refused("bad_date", "A promise is for a date from today up to a month away.")
    with conn.transaction():
        _, replied_loan, borrower_ref, _ = _waiting(conn, inbound_id)
        loan_ref = loan_ref or replied_loan
        if not loan_ref:
            raise Refused("loan_required", "Say which loan the promise is for.")
        owner = conn.execute("SELECT borrower_ref FROM loans WHERE ref = %s", (loan_ref,)).fetchone()
        if not owner or (borrower_ref and owner[0] != borrower_ref):
            raise Refused("wrong_loan", f"{loan_ref} isn't a loan of the borrower who replied.")
        promise_id = _record_promise(conn, inbound_id, loan_ref, owner[0], due_on, amount_kobo, today, who.id, "staff")
        conn.execute("UPDATE inbound_messages SET handled_at = now(), handled_by = %s, loan_ref = %s, "
                     "note = 'promise recorded by a person' WHERE id = %s", (who.id, loan_ref, inbound_id))
    return {"inbound_id": inbound_id, "promise_id": promise_id}


def close(conn: psycopg.Connection, inbound_id: int, who: Staff, note: str) -> dict:
    """A person has dealt with the reply (called back, found the payment, noted the request)."""
    _may_handle(who)
    if not (note or "").strip():
        raise Refused("reason_required", "Say what was done, for the next person.")
    with conn.transaction():
        _, loan_ref, _, _ = _waiting(conn, inbound_id)
        conn.execute("UPDATE inbound_messages SET handled_at = now(), handled_by = %s, note = %s WHERE id = %s",
                     (who.id, note.strip(), inbound_id))
        db.audit(conn, who.id, "reply.handled", f"loan:{loan_ref}" if loan_ref else f"inbound:{inbound_id}",
                 {"inbound_id": inbound_id, "note": note.strip()}, telemetry.current_trace_id())
    return {"inbound_id": inbound_id, "handled": True}


def _iso(v):
    return v.isoformat() if v else None


def replies(conn: psycopg.Connection, waiting: bool | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Replies newest first; `waiting=True` for those a person still has to deal with."""
    where = "" if waiting is None else "WHERE i.handled_at IS NULL" if waiting else "WHERE i.handled_at IS NOT NULL"
    rows = conn.execute(
        "SELECT i.id, i.channel, i.from_phone, i.body, i.received_at, i.borrower_ref, b.name, i.loan_ref, "
        "i.message_id, m.body, i.reading, i.outcome, i.handled_at, i.handled_by, s.name, i.note, "
        "p.id, p.due_on, p.amount_kobo, p.status, m.amount_due_kobo, count(*) OVER () "
        "FROM inbound_messages i LEFT JOIN borrowers b ON b.ref = i.borrower_ref "
        "LEFT JOIN collection_messages m ON m.id = i.message_id LEFT JOIN staff s ON s.id = i.handled_by "
        "LEFT JOIN LATERAL (SELECT id, due_on, amount_kobo, status FROM promises WHERE inbound_id = i.id "
        "                   ORDER BY id DESC LIMIT 1) p ON true "
        f"{where} ORDER BY i.received_at DESC, i.id DESC LIMIT %s OFFSET %s", (limit, offset)).fetchall()
    waiting_count = conn.execute("SELECT count(*) FROM inbound_messages WHERE handled_at IS NULL").fetchone()[0]
    return {
        "items": [{
            "id": r[0], "channel": r[1], "from_phone": r[2], "body": r[3], "received_at": _iso(r[4]),
            "borrower_ref": r[5], "borrower_name": r[6], "loan_ref": r[7], "message_id": r[8], "answers": r[9],
            "reading": r[10], "outcome": r[11], "handled_at": _iso(r[12]), "handled_by": r[13],
            "handled_by_name": r[14], "note": r[15],
            "promise": {"id": r[16], "due_on": _iso(r[17]), "amount_kobo": r[18], "status": r[19]} if r[16] else None,
            "asked_kobo": r[20],
        } for r in rows],
        "total": rows[0][21] if rows else 0,
        "waiting": waiting_count,
        "limit": limit, "offset": offset,
    }
