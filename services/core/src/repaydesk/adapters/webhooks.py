"""Virtual-account credits from the payment provider (PayRail, a stand-in for any provider).

The provider signs each delivery with HMAC-SHA512 over the raw body. A delivery with a bad
signature is rejected before it is parsed. The provider may deliver the same event more
than once; the event id is the line's reference, and ingestion drops repeats.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from repaydesk.domain import Channel, PaymentLine
from repaydesk.synth.names import COLLECTION_ACCOUNTS


class PayRailWebhooks:
    SIGNATURE_HEADER = "x-payrail-signature"

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def verify(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.secret, body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def to_line(self, body: bytes) -> PaymentLine | None:
        event = json.loads(body)
        if event.get("event") != "virtual_account.credited":
            return None
        d = event["data"]
        if d.get("currency") != "NGN" or int(d["amount"]) <= 0:
            return None
        return PaymentLine(
            line_id=f"{Channel.VIRTUAL_ACCOUNT.value}:{event['id']}",
            channel=Channel.VIRTUAL_ACCOUNT,
            source_account=COLLECTION_ACCOUNTS["virtual_account"][1],
            value_date=datetime.fromisoformat(d["paid_at"]).date(),
            amount=int(d["amount"]),
            narration=d.get("narration") or "",
            bank_reference=event["id"],
            payer_name=d.get("sender_name"),
            payer_account=d.get("sender_account"),
            virtual_account=d.get("account_number"),
        )


class MessagingWebhooks:
    """Borrowers' replies from the messaging provider (the simulator, in the prototype), signed with
    HMAC-SHA256 over the raw body. A redelivery carries the same reply id."""

    SIGNATURE_HEADER = "x-messaging-signature"

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def sign(self, body: bytes) -> str:
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    def verify(self, body: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(body), signature or "")

    @staticmethod
    def to_reply(body: bytes) -> dict:
        event = json.loads(body)
        if event.get("event") != "message.received":
            raise ValueError("not a reply")
        d = event["data"]
        if d["channel"] not in ("sms", "whatsapp") or not str(d["body"]).strip():
            raise ValueError("unreadable reply")
        return {"provider_id": str(d["id"]), "channel": d["channel"], "from_phone": str(d["from"]),
                "body": str(d["body"])[:2000]}
