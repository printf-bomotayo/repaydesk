"""Statement files from a folder: the demo's StatementSource.

In production the same parsers read files fetched over SFTP or a bank's API; only `pending`
changes. Each parser turns one source's format into PaymentLines and skips debits. A line's
id is its source plus the source's own reference, so the same file read twice gives the
same ids, and ingestion can refuse what it has seen.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from repaydesk.adapters.webhooks import PayRailWebhooks
from repaydesk.domain import Channel, PaymentLine
from repaydesk.synth.names import COLLECTION_ACCOUNTS


class UnreadableStatement(Exception):
    pass


def _kobo(text: str) -> int:
    text = text.replace(",", "").strip()
    return round(float(text) * 100) if text else 0


def _with_ids(lines: Iterator[PaymentLine]) -> Iterator[PaymentLine]:
    seen: Counter[str] = Counter()
    for line in lines:
        key = f"{line.channel.value}:{line.bank_reference}"
        seen[key] += 1
        lid = key if seen[key] == 1 else f"{key}#{seen[key]}"
        yield replace(line, line_id=lid)


class FolderStatementSource:
    FOLDERS = {"northgate": "bank_csv", "coastline": "bank_mt940", "payrail": "virtual_account",
               "termpay": "pos", "branch-cash": "branch_cash"}

    def __init__(self, inbox: Path, webhook_secret: str):
        self.inbox = Path(inbox)
        self.webhooks = PayRailWebhooks(webhook_secret)

    def pending(self) -> list[Path]:
        return sorted(p for folder in self.FOLDERS for p in (self.inbox / folder).glob("*") if p.is_file())

    def read(self, path: Path) -> Iterator[PaymentLine]:
        kind = self.FOLDERS.get(path.parent.name)
        parser = {
            "bank_csv": parse_northgate,
            "bank_mt940": parse_mt940,
            "virtual_account": self._payrail,
            "pos": parse_termpay,
            "branch_cash": parse_branch_cash,
        }.get(kind)
        if not parser:
            raise UnreadableStatement(f"no parser for {path}")
        return _with_ids(parser(path))

    def _payrail(self, path: Path) -> Iterator[PaymentLine]:
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            delivery = json.loads(raw)
            body = delivery["body"].encode()
            if not self.webhooks.verify(body, delivery["headers"].get(PayRailWebhooks.SIGNATURE_HEADER, "")):
                raise UnreadableStatement(f"{path.name} line {n}: bad signature")
            line = self.webhooks.to_line(body)
            if line:
                yield line


def parse_northgate(path: Path) -> Iterator[PaymentLine]:
    """CSV with a preamble of 'Key:,value' rows before the header."""
    account = COLLECTION_ACCOUNTS["bank_csv"][1]
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    try:
        start = next(i for i, r in enumerate(rows) if r and r[0] == "Trans Date")
    except StopIteration as e:
        raise UnreadableStatement(f"{path.name}: no header row") from e
    for r in rows[start + 1 :]:
        if len(r) < 7 or not r[0]:
            continue
        credit = _kobo(r[5])
        if credit <= 0:
            continue  # a debit: charges and sweeps aren't repayments
        narration = r[3]
        parts = narration.split("/")
        payer = parts[2] if len(parts) >= 3 and parts[0] == "NIP" and parts[2] else None
        yield PaymentLine(
            line_id="", channel=Channel.BANK_CSV, source_account=account,
            value_date=datetime.strptime(r[1], "%d-%b-%Y").date(), amount=credit,
            narration=narration, bank_reference=r[2], payer_name=payer,
        )


_MT61 = re.compile(r"^:61:(\d{6})(\d{4})?(C|D|RC|RD)(\d+,\d{0,2})N\w{3}([^/]+)(?://(.*))?$")


def parse_mt940(path: Path) -> Iterator[PaymentLine]:
    account = COLLECTION_ACCOUNTS["bank_mt940"][1]
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    current: dict | None = None
    narration: list[str] = []

    def emit():
        if current and current["mark"] in ("C", "RD"):
            n = "".join(narration).strip()  # wrapped lines split mid-word, so no separator
            payer = n[len("TRF FROM ") :].split("/", 1)[0].strip() if n.startswith("TRF FROM ") else None
            yield PaymentLine(
                line_id="", channel=Channel.BANK_MT940, source_account=account,
                value_date=current["date"], amount=current["amount"], narration=n,
                bank_reference=current["ref"], payer_name=payer or None,
            )

    for raw in text.split("\n"):
        if raw.startswith(":61:"):
            yield from emit()
            m = _MT61.match(raw)
            if not m:
                raise UnreadableStatement(f"{path.name}: bad :61: line {raw!r}")
            d = datetime.strptime(m.group(1), "%y%m%d").date()
            whole, _, frac = m.group(4).partition(",")
            current = {"date": d, "mark": m.group(3), "amount": int(whole) * 100 + int((frac or "0").ljust(2, "0")),
                       "ref": m.group(5)}
            narration = []
        elif raw.startswith(":86:") and current:
            narration = [raw[4:]]
        elif raw.startswith(":62F:") or raw == "-":
            yield from emit()
            current, narration = None, []
        elif current and narration and raw and not raw.startswith(":"):
            narration.append(raw)  # continuation of a wrapped :86:


def parse_termpay(path: Path) -> Iterator[PaymentLine]:
    account = COLLECTION_ACCOUNTS["pos"][1]
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            yield PaymentLine(
                line_id="", channel=Channel.POS, source_account=account,
                value_date=date.fromisoformat(r["tran_time"][:10]), amount=_kobo(r["amount"]),
                narration=f"POS {r['terminal_id']} {r['masked_pan']}", bank_reference=r["rrn"],
                terminal_id=r["terminal_id"], customer_reference=r["customer_ref"] or None,
            )


def parse_branch_cash(path: Path) -> Iterator[PaymentLine]:
    account = COLLECTION_ACCOUNTS["branch_cash"][1]
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            yield PaymentLine(
                line_id="", channel=Channel.BRANCH_CASH, source_account=account,
                value_date=datetime.strptime(r["Date"], "%d/%m/%Y").date(), amount=_kobo(r["Amount (NGN)"]),
                narration=f"Cash deposit {r['Branch']} by {r['Depositor']}", bank_reference=r["Receipt No"],
                payer_name=r["Depositor"] or None, customer_reference=r["Loan Ref"] or None,
            )
