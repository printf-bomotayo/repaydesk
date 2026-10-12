"""What a model may read: names yes, numbers that reach a person or an account no.

Everything the investigator agent reads goes through here first (ADR 0008). A payer's name and a
narration's words are the evidence, so they stay. Phone numbers and account numbers are masked to
their last four digits: enough to tell two apart, not enough to call or pay anyone. A tool that
needs the whole number (to find a borrower by the phone typed at a POS terminal) takes the line's
id and looks the number up itself, so the number never leaves the database.
"""

from __future__ import annotations

import re

# A Nigerian mobile number written the usual ways: 07005128830, 0700 512 8830, +234 700 512 8830.
_PHONE = re.compile(r"(?<![\w])(?:\+?234[\s-]?|0)([789][01]\d)[\s-]?(\d{3})[\s-]?(\d{4})(?![\w])")
# Any other standalone run of eight or more digits: an account number (NUBAN is ten), a card, a BVN.
# Runs inside a word (a bank's reference, NGT282773322650) are references, not people, and stay.
_DIGITS = re.compile(r"(?<![\w-])\d{8,}(?![\w-])")


def mask_phone(phone: str | None) -> str | None:
    """'07005566957' -> '0700 *** 6957'."""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    return f"{digits[:4]} *** {digits[-4:]}" if len(digits) >= 8 else "***"


def mask_account(number: str | None) -> str | None:
    """'0123456789' -> '****6789'."""
    if not number:
        return number
    return "****" + number[-4:] if len(number) > 4 else "****"


def mask_text(text: str | None) -> str | None:
    """A narration or reference with its phone and account numbers masked; every other word kept."""
    if not text:
        return text
    text = _PHONE.sub(lambda m: f"0{m.group(1)} *** {m.group(3)}", text)
    return _DIGITS.sub(lambda m: "****" + m.group(0)[-4:], text)


def phones_in(*texts: str | None) -> list[str]:
    """Phone numbers in the texts, as the directory stores them (0 and ten digits)."""
    found = []
    for text in texts:
        for m in _PHONE.finditer(text or ""):
            digits = "0" + "".join(m.groups())
            if digits not in found:
                found.append(digits)
    return found
