"""The bankbook MCP server: borrowers, loans and payments, read only.

    python -m repaydesk.servers.bankbook                  # stdio, for Claude Desktop or Claude Code
    python -m repaydesk.servers.bankbook --http 8100      # streamable HTTP, inside the stack

Reads Repaydesk's database in a read-only session and core banking through the read-only login
(`FINERACT_USERNAME` / `FINERACT_PASSWORD` should be `repaydesk-read`'s). See `repaydesk.bankbook`.
"""

from __future__ import annotations

import argparse
import os
import sys

from mcp.server.mcpserver import MCPServer

from repaydesk import db, telemetry
from repaydesk.bankbook import Bankbook, read_only
from repaydesk.servers import READ_ONLY, answer

INSTRUCTIONS = """\
Look up borrowers, loans and payments at a Nigerian microfinance bank, to work out whose loan a
payment is meant for. Everything here reads; nothing changes.

Money is in kobo (100 kobo = 1 naira), each amount with a naira string beside it. Phone and account
numbers are masked to their last four digits; to find a borrower by the phone typed on a payment,
use borrowers_by_line_phone with the payment's line id. Borrower searches read a copy of the book
taken every few minutes; get_loan reads core banking live.
"""


def build(book: Bankbook) -> MCPServer:
    server = MCPServer("repaydesk-bankbook", title="Repaydesk bankbook", instructions=INSTRUCTIONS, version="1.0")

    @server.tool(annotations=READ_ONLY)
    def get_payment_line(line_id: str) -> dict:
        """One incoming payment as it arrived: channel, value date, amount, narration, payer's name, the
        reference typed (if any), the branch for cash and POS, and any earlier proposals for it."""
        return answer(book.payment_line, line_id)

    @server.tool(annotations=READ_ONLY)
    def search_borrowers(text: str, branch_code: str | None = None, limit: int = 8) -> dict:
        """Borrowers whose name, trading name or savings group reads like `text`, best match first, each
        with their loans (status, outstanding, arrears). Finds misspelt, initialled ("EZE C") and reordered
        names. `branch_code` (e.g. IKJ) keeps to one branch. match_score runs from 0 to 1."""
        return answer(book.search_borrowers, text, branch_code, limit)

    @server.tool(annotations=READ_ONLY)
    def borrowers_by_line_phone(line_id: str) -> dict:
        """Borrowers whose registered phone is a number typed on the payment line (often at a POS terminal).
        The number itself stays hidden."""
        return answer(book.borrowers_by_line_phone, line_id)

    @server.tool(annotations=READ_ONLY)
    def get_borrower(borrower_ref: str) -> dict:
        """A borrower by customer reference (e.g. CUS-000485): branch, trading name, group and loans."""
        return answer(book.borrower, borrower_ref)

    @server.tool(annotations=READ_ONLY)
    def get_group(group_ref: str) -> dict:
        """A savings group's members and their loans. A group leader often pays for several members at
        once, so one payment can belong to several loans."""
        return answer(book.group, group_ref)

    @server.tool(annotations=READ_ONLY)
    def get_loan(loan_ref: str) -> dict:
        """A loan read live from core banking (e.g. LN-2026-000717): status, outstanding, arrears and days
        late, the next instalments, and the amounts that a payment on it would usually be."""
        return answer(book.loan, loan_ref)

    @server.tool(annotations=READ_ONLY)
    def payment_history(loan_ref: str, limit: int = 10) -> dict:
        """How a loan has been paid before: repayments core banking holds (date, amount, channel), and the
        lines Repaydesk matched to it with the payer's name and narration as each arrived."""
        return answer(book.payment_history, loan_ref, limit)

    @server.tool(annotations=READ_ONLY)
    def find_payments(text: str, limit: int = 10) -> dict:
        """Earlier payments whose payer or narration reads like `text`, and the loans they were matched to,
        decided or still awaiting review. Someone who paid a loan before under the same name likely
        pays it again."""
        return answer(book.find_payments, text, limit)

    return server


def from_environment() -> Bankbook:
    from repaydesk.adapters.fineract import FineractCoreBanking

    conn = read_only(db.connect(os.environ["REPAYDESK_DATABASE_URL"]))
    core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                               os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=20)
    return Bankbook(conn, core)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on this port instead of stdio")
    args = ap.parse_args(argv)
    telemetry.setup("repaydesk-bankbook")
    try:
        server = build(from_environment())
        if args.http:
            server.run("streamable-http", host="0.0.0.0", port=args.http)
        else:
            server.run("stdio")  # stdout is the protocol: nothing else may print there
        return 0
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
