"""The collections policy's checkable rules (docs/collections-policy.md), as code.

Every drafted message is checked here before a person sees it; a draft that fails a check is shown
with the failure and can't be approved until it's fixed. The rules a person must judge (tone, the
offer to talk) are the drafting agent's instructions and the manager's eye, not code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from repaydesk.domain import BANK_TIMEZONE

# Buckets by days late. Over 90 is recovery: people, not drafted messages.
BUCKETS = (("reminder", 1, 7), ("follow_up", 8, 30), ("arrangement", 31, 90))
BUCKET_LABEL = {"reminder": "Reminder", "follow_up": "Follow-up", "arrangement": "Arrangement",
                "recovery": "Recovery", "broken_promise": "Promise broken"}
CONTACT_HOURS = (time(8, 0), time(18, 0))
CONTACT_DAYS = range(0, 6)  # Monday to Saturday
MAX_MESSAGES_PER_WEEK = 2
MAX_LENGTH = {"sms": 320, "whatsapp": 600}

# Words that threaten, shame or frighten. Matched as whole words, any case.
FORBIDDEN = ("police", "arrest", "arrested", "prison", "jail", "court", "lawyer", "lawyers", "efcc", "blacklist",
             "blacklisted", "credit bureau", "your family", "your contacts", "everyone will know", "shame",
             "disgrace", "embarrass")
# Things a message about money must never ask for.
SECRETS = ("pin", "password", "bvn", "card number", "otp", "cvv")


def bucket_for(days_late: int) -> str | None:
    """reminder, follow_up or arrangement; recovery over 90 days; None when not late."""
    if days_late <= 0:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= days_late <= hi:
            return name
    return "recovery"


def may_contact_at(moment: datetime) -> bool:
    local = moment.astimezone(ZoneInfo(BANK_TIMEZONE))
    return local.weekday() in CONTACT_DAYS and CONTACT_HOURS[0] <= local.time() < CONTACT_HOURS[1]


def next_contact_window(moment: datetime) -> datetime:
    """The next moment a message may be sent, from `moment` (itself, if it's inside the hours)."""
    tz = ZoneInfo(BANK_TIMEZONE)
    local = moment.astimezone(tz)
    for days in range(0, 8):
        day = (local + timedelta(days=days)).date()
        start = datetime.combine(day, CONTACT_HOURS[0], tz)
        end = datetime.combine(day, CONTACT_HOURS[1], tz)
        if day.weekday() not in CONTACT_DAYS:
            continue
        if local < start:
            return start
        if local < end:
            return local
    raise AssertionError("a contact window always comes within a week")


@dataclass(frozen=True)
class Facts:
    """What a message about one loan must agree with."""

    loan_ref: str
    amount_due_kobo: int
    days_late: int
    first_name: str
    messages_last_7_days: int = 0
    promise_open_until: date | None = None
    today: date | None = None


def _amounts_in(text: str) -> set[int]:
    """Naira amounts written in the text, in kobo: ₦60,500 · N60,500.00 · NGN 60500 · 60,500 naira."""
    found = set()
    for m in re.finditer(r"(?:₦|\bNGN\s?|\bN(?=\d))\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?|"
                         r"\b(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?\s?naira\b", text, re.I):
        whole = (m.group(1) or m.group(3)).replace(",", "")
        kobo = m.group(2) or m.group(4) or "00"
        found.add(int(whole) * 100 + int(kobo))
    return found


def check(body: str, channel: str, facts: Facts) -> list[dict]:
    """Each checkable rule, passed or failed, with what it found. A draft is sendable only when all pass."""
    results: list[dict] = []

    def note(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "ok": ok, "detail": detail})

    text = body.strip()
    lower = text.lower()
    bucket = bucket_for(facts.days_late)
    note("in_scope", bucket in ("reminder", "follow_up", "arrangement"),
         f"{facts.days_late} days late: {BUCKET_LABEL.get(bucket or '', 'not late')}"
         + ("; over 90 days goes to the recovery team" if bucket == "recovery" else ""))
    until = facts.promise_open_until
    note("no_open_promise", not (until and facts.today and until >= facts.today),
         f"promised to pay by {until.day} {until:%b}" if until else "no open promise")
    note("frequency", facts.messages_last_7_days < MAX_MESSAGES_PER_WEEK,
         f"{facts.messages_last_7_days} message(s) in the last 7 days; at most {MAX_MESSAGES_PER_WEEK}")
    limit = MAX_LENGTH.get(channel, 0)
    note("length", 0 < len(text) <= limit, f"{len(text)} characters; {channel} allows {limit}")
    note("loan_reference", facts.loan_ref.lower() in lower, f"names {facts.loan_ref}")
    amounts = _amounts_in(text)
    note("amount_due", facts.amount_due_kobo in amounts,
         f"₦{facts.amount_due_kobo / 100:,.2f} due; the message says "
         + (", ".join(f"₦{a / 100:,.2f}" for a in sorted(amounts)) or "no amount"))
    note("first_name", facts.first_name.lower() in lower, f"addresses {facts.first_name}")
    hits = [w for w in FORBIDDEN if re.search(rf"\b{re.escape(w)}\b", lower)]
    note("no_threats", not hits, "no threatening or shaming words" if not hits else f"says {', '.join(hits)}")
    asks = [w for w in SECRETS if re.search(rf"\b{re.escape(w)}\b", lower)]
    note("no_secrets", not asks, "asks for nothing secret" if not asks else f"mentions {', '.join(asks)}")
    links = re.findall(r"https?://|www\.|\b\w+\.(?:com|ng|net|org|ly|link)\b", lower)
    note("no_links", not links, "no links" if not links else "contains a link")
    shouting = re.findall(r"\b[A-Z]{4,}\b", text)  # LN and NGN are short enough to pass
    note("no_shouting", not shouting and text.count("!") <= 1,
         "calm" if not shouting and text.count("!") <= 1 else
         f"capitals for emphasis ({', '.join(shouting[:3])})" if shouting else "more than one exclamation mark")
    return results


def sendable(checks: list[dict]) -> bool:
    return bool(checks) and all(c["ok"] for c in checks)
