"""The ledger MCP server: journal entries and account balances, read only.

    python -m repaydesk.servers.ledger                    # stdio
    python -m repaydesk.servers.ledger --http 8101        # streamable HTTP, inside the stack

Logs in to Odoo as `ODOO_READ_USERNAME`, a user in Odoo's read-only accounting group (ADR 0008):
the ledger itself refuses anything but reads from this server.
"""

from __future__ import annotations

import argparse
import os
import sys

from mcp.server.mcpserver import MCPServer

from repaydesk import telemetry
from repaydesk.adapters.chart import CHART, collection_account_code
from repaydesk.adapters.ports import Ledger
from repaydesk.domain import naira
from repaydesk.servers import READ_ONLY, answer
from repaydesk.synth.names import COLLECTION_ACCOUNTS

INSTRUCTIONS = """\
Read the bank's general ledger: a journal entry by its reference, and account balances. Everything
here reads; nothing changes. Amounts are in kobo (100 kobo = 1 naira), each with a naira string.
A repayment Repaydesk posted has its payment's key as its reference, e.g. RD-P219.
"""


def accounts() -> dict[str, str]:
    """The accounts Repaydesk posts to, by code: its chart, and one collection account per channel."""
    named = {code: name for code, name, _ in CHART}
    named |= {collection_account_code(key): f"Collections: {bank}" for key, (bank, _) in COLLECTION_ACCOUNTS.items()}
    return dict(sorted(named.items()))


class Books:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def entry(self, reference: str) -> dict:
        found = self.ledger.read_entry(reference)
        if not found:
            raise LookupError(f"No journal entry with reference {reference} in the ledger.")
        names = accounts()
        return {
            "name": found.name, "state": found.state, "reference": reference, "date": found.entry.on.isoformat(),
            "journal": found.entry.journal, "memo": found.entry.memo,
            "lines": [{"account": line.account_code, "account_name": names.get(line.account_code),
                       "debit": {"kobo": line.debit, "naira": naira(line.debit)},
                       "credit": {"kobo": line.credit, "naira": naira(line.credit)}, "label": line.label}
                      for line in found.entry.lines],
        }

    def balances(self) -> dict:
        return {"accounts": [{"account": code, "name": name,
                              "balance": {"kobo": (b := self.ledger.balance(code)), "naira": naira(b)}}
                             for code, name in accounts().items()],
                "sign": "debit balances positive, credit balances negative"}


def build(books: Books) -> MCPServer:
    server = MCPServer("repaydesk-ledger", title="Repaydesk ledger", instructions=INSTRUCTIONS, version="1.0")

    @server.tool(annotations=READ_ONLY)
    def find_journal_entry(reference: str) -> dict:
        """A journal entry by its reference: its name in the ledger, date, state, and each line's account,
        debit and credit. A posted repayment credits loan principal, interest and charges as core banking
        split it."""
        return answer(books.entry, reference)

    @server.tool(annotations=READ_ONLY)
    def account_balances() -> dict:
        """Balances of the accounts Repaydesk posts to: the collection account for each channel, loan
        principal, interest and fee income, repayment suspense and customer overpayments."""
        return answer(books.balances)

    return server


def main(argv: list[str] | None = None) -> int:
    from repaydesk.adapters.odoo import OdooLedger

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on this port instead of stdio")
    args = ap.parse_args(argv)
    telemetry.setup("repaydesk-ledger")
    try:
        ledger = OdooLedger(os.environ["ODOO_URL"], os.getenv("ODOO_DB", "ledger"),
                            os.environ["ODOO_READ_USERNAME"], os.environ["ODOO_READ_PASSWORD"], timeout=20)
        server = build(Books(ledger))
        if args.http:
            server.run("streamable-http", host="0.0.0.0", port=args.http)
        else:
            server.run("stdio")
        return 0
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
