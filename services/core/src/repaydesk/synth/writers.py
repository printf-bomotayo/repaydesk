"""Write a generated week the way each source would deliver it.

    inbox/northgate/      CSV statement per day, with the bank's preamble lines
    inbox/coastline/      SWIFT MT940 statement per day
    inbox/payrail/        webhook deliveries (headers + raw body), one JSON line each
    inbox/termpay/        POS settlement CSV per day
    inbox/branch-cash/    teller deposit sheet per branch per day
    truth/truth.json      what each line really is (evals only; agents never see this folder)

Parsers for every format are in repaydesk.adapters.statements and repaydesk.adapters.webhooks.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from repaydesk.domain import Book, Channel, PaymentLine
from repaydesk.synth.names import COLLECTION_ACCOUNTS


def _money(kobo: int) -> str:
    return f"{kobo / 100:,.2f}"


def _mt_money(kobo: int) -> str:
    return f"{kobo // 100},{kobo % 100:02d}"


def _days(book: Book) -> list[date]:
    return [book.week_start + timedelta(days=i) for i in range(7)]


def write_week(book: Book, data_dir: Path, webhook_secret: str) -> dict[str, int]:
    inbox, truth_dir = data_dir / "inbox", data_dir / "truth"
    for sub in ["northgate", "coastline", "payrail", "termpay", "branch-cash"]:
        (inbox / sub).mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    by_channel: dict[Channel, list[PaymentLine]] = defaultdict(list)
    for line in book.lines + book.debits:
        by_channel[line.channel].append(line)

    counts = {
        "northgate": _northgate(book, by_channel[Channel.BANK_CSV], inbox / "northgate"),
        "coastline": _coastline(book, by_channel[Channel.BANK_MT940], inbox / "coastline"),
        "payrail": _payrail(by_channel[Channel.VIRTUAL_ACCOUNT], inbox / "payrail", webhook_secret, book),
        "termpay": _termpay(book, by_channel[Channel.POS], inbox / "termpay"),
        "branch-cash": _branch_cash(by_channel[Channel.BRANCH_CASH], inbox / "branch-cash"),
    }

    lines = {line.line_id: line for line in book.lines}
    truth = []
    seen: dict[str, int] = defaultdict(int)
    for t in sorted(book.truth, key=lambda t: t.line_id):
        key = source_key(lines[t.line_id])
        seen[key] += 1
        truth.append({
            **asdict(t),
            "source_key": key if seen[key] == 1 else f"{key}#{seen[key]}",  # a redelivered webhook
            "allocations": [asdict(a) for a in t.allocations],
        })
    (truth_dir / "truth.json").write_text(json.dumps({
        "as_of": book.as_of.isoformat(), "week_start": book.week_start.isoformat(), "lines": truth,
    }, indent=1), encoding="utf-8")
    return counts


def source_key(line: PaymentLine) -> str:
    """How a line is identified once parsed back: its source and the source's own reference."""
    return f"{line.channel.value}:{line.bank_reference}"


def _opening(book: Book, account: str) -> int:
    # A plausible running balance; the bank's figure, not ours.
    return 18_500_000_00 + (int(hashlib.sha256(account.encode()).hexdigest(), 16) % 5_000_000_00)


def _northgate(book: Book, lines: list[PaymentLine], folder: Path) -> int:
    _name, number = COLLECTION_ACCOUNTS["bank_csv"]
    balance = _opening(book, number)
    count = 0
    for day in _days(book):
        todays = sorted((x for x in lines if x.value_date == day), key=lambda x: x.bank_reference)
        rows = [(x.bank_reference, x.narration, x.amount if x.line_id.startswith("DR-") else 0,
                 0 if x.line_id.startswith("DR-") else x.amount) for x in todays]
        balance = write_northgate_day(folder / f"northgate_{number}_{day:%Y%m%d}.csv", book.bank_name, number, day,
                                      rows, balance)
        count += sum(1 for r in rows if r[3])
    return count


def write_northgate_day(path: Path, bank_name: str, number: str, day: date,
                        rows: list[tuple[str, str, int, int]], balance: int) -> int:
    """One day of Northgate Bank's CSV statement: (reference, narration, debit, credit) per row, in
    kobo. Returns the closing balance."""
    with path.open("w", newline="", encoding="utf-8") as f:
        f.write(f"Account Name:,{bank_name} - Loan Collections\n")
        f.write(f"Account Number:,{number}\n")
        f.write(f"Period:,{day:%d-%b-%Y} to {day:%d-%b-%Y}\n\n")
        w = csv.writer(f)
        w.writerow(["Trans Date", "Value Date", "Reference", "Narration", "Debit", "Credit", "Balance"])
        for reference, narration, debit, credit in rows:
            balance += credit - debit
            w.writerow([f"{day:%d-%b-%Y}", f"{day:%d-%b-%Y}", reference, narration,
                        _money(debit) if debit else "", _money(credit) if credit else "", _money(balance)])
    return balance


def _coastline(book: Book, lines: list[PaymentLine], folder: Path) -> int:
    _name, number = COLLECTION_ACCOUNTS["bank_mt940"]
    balance = _opening(book, number)
    count = 0
    for seq, day in enumerate(_days(book), start=1):
        todays = sorted((x for x in lines if x.value_date == day), key=lambda x: x.bank_reference)
        out = [f":20:CST{day:%y%m%d}", f":25:{number}", f":28C:{seq:05d}/1",
               f":60F:C{day:%y%m%d}NGN{_mt_money(balance)}"]
        for x in todays:
            debit = x.line_id.startswith("DR-")
            balance += -x.amount if debit else x.amount
            mark = "D" if debit else "C"
            out.append(f":61:{day:%y%m%d}{day:%m%d}{mark}{_mt_money(x.amount)}NTRF{x.bank_reference}//{x.bank_reference[-8:]}")
            # :86: lines wrap at 65 characters, as real MT940 does
            text = x.narration
            out.append(":86:" + text[:65])
            out.extend(text[i : i + 65] for i in range(65, len(text), 65))
            count += 0 if debit else 1
        out.append(f":62F:C{day:%y%m%d}NGN{_mt_money(balance)}")
        out.append("-")
        (folder / f"coastline_{number}_{day:%Y%m%d}.sta").write_text("\r\n".join(out) + "\r\n", encoding="utf-8")
    return count


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _payrail(lines: list[PaymentLine], folder: Path, secret: str, book: Book) -> int:
    path = folder / f"payrail_deliveries_{book.week_start:%Y%m%d}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for i, x in enumerate(sorted(lines, key=lambda x: (x.value_date, x.line_id))):
            paid_at = datetime.combine(x.value_date, time(8, 0)) + timedelta(minutes=(i * 37) % 720)
            body = json.dumps({
                "event": "virtual_account.credited",
                "id": x.bank_reference,
                "data": {
                    "amount": x.amount, "currency": "NGN", "account_number": x.virtual_account,
                    "sender_name": x.payer_name, "sender_account": x.payer_account,
                    "narration": x.narration, "paid_at": paid_at.isoformat() + "+01:00",
                },
            }, separators=(",", ":")).encode()
            f.write(json.dumps({
                "headers": {"x-payrail-signature": sign(secret, body), "content-type": "application/json"},
                "body": body.decode(),
            }) + "\n")
    return len(lines)


def _termpay(book: Book, lines: list[PaymentLine], folder: Path) -> int:
    for day in _days(book):
        todays = sorted((x for x in lines if x.value_date == day), key=lambda x: x.bank_reference)
        with (folder / f"termpay_settlement_{day:%Y%m%d}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["settlement_date", "terminal_id", "rrn", "masked_pan", "amount", "fee", "net_amount",
                        "tran_time", "customer_ref"])
            for i, x in enumerate(todays):
                fee = min(round(x.amount * 0.005), 100_00)
                w.writerow([
                    f"{day + timedelta(days=1):%Y-%m-%d}", x.terminal_id, x.bank_reference,
                    f"5399{(i * 7919) % 100:02d}******{(i * 104729) % 10000:04d}",
                    f"{x.amount / 100:.2f}", f"{fee / 100:.2f}", f"{(x.amount - fee) / 100:.2f}",
                    f"{day:%Y-%m-%d} {9 + i % 9:02d}:{(i * 13) % 60:02d}", x.customer_reference or "",
                ])
    return len(lines)


def _branch_cash(lines: list[PaymentLine], folder: Path) -> int:
    sheets: dict[tuple[str, date], list[PaymentLine]] = defaultdict(list)
    for x in lines:
        sheets[(x.bank_reference.split("-")[1], x.value_date)].append(x)
    for (branch, day), todays in sheets.items():
        with (folder / f"cash_{branch}_{day:%Y%m%d}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Branch", "Teller", "Date", "Receipt No", "Depositor", "Loan Ref", "Amount (NGN)"])
            for i, x in enumerate(sorted(todays, key=lambda x: x.bank_reference)):
                w.writerow([branch, f"T{1 + i % 2}", f"{day:%d/%m/%Y}", x.bank_reference, x.payer_name,
                            x.customer_reference or "", f"{x.amount // 100}"])
    return len(lines)
