"""Run the investigator agent over payments no rule could place.

    python -m repaydesk.investigate                  # the next 10 open lines
    python -m repaydesk.investigate --limit 50
    python -m repaydesk.investigate --line "bank_mt940:CST9758245850"

Reads through the bankbook MCP server (in process, over the MCP protocol); writes only agent runs,
proposals for people to review, and suspense. `MODEL_PROVIDER` picks the Claude API (`anthropic`,
with `ANTHROPIC_API_KEY`) or Amazon Bedrock (`bedrock`, with `AWS_REGION` and AWS credentials).
"""

from __future__ import annotations

import argparse
import os
import sys

import anyio

from repaydesk import db, telemetry
from repaydesk import investigator as inv
from repaydesk.bankbook import Bankbook, read_only


def model_client():
    """The model provider. Bedrock takes the same requests; its model ids carry an `anthropic.` prefix,
    and server-side fallbacks aren't offered there."""
    provider = os.getenv("MODEL_PROVIDER", "anthropic")
    if provider == "bedrock":
        from anthropic import AsyncAnthropicBedrockMantle

        for name in ("INVESTIGATOR_MODEL", "CLASSIFIER_MODEL"):
            current = getattr(inv, name)
            if not current.startswith("anthropic."):
                setattr(inv, name, f"anthropic.{current}")
        return AsyncAnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"]), False
    if provider != "anthropic":
        raise SystemExit(f"MODEL_PROVIDER is anthropic or bedrock, not {provider}")
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(), True


async def amain(args) -> int:
    from mcp.client.client import Client

    from repaydesk.adapters.fineract import FineractCoreBanking
    from repaydesk.servers import bankbook as bankbook_server

    url = os.environ["REPAYDESK_DATABASE_URL"]
    conn = db.connect(url)
    db.migrate(conn)
    core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                               os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=30)
    book = Bankbook(read_only(db.connect(url)), core)
    client, fallbacks = model_client()
    line_ids = [args.line] if args.line else inv.open_lines(conn, args.limit)
    if not line_ids:
        print("[investigate] nothing to investigate: every line is placed, waiting for review, or tried today")
        return 0
    print(f"[investigate] {len(line_ids)} line(s), {inv.INVESTIGATOR_MODEL} with {inv.CLASSIFIER_MODEL} triage",
          flush=True)
    async with Client(bankbook_server.build(book)) as mcp:
        agent = inv.Investigator(client, mcp, fallbacks=fallbacks)
        total = 0.0
        for line_id in line_ids:
            [r] = await inv.investigate_lines(conn, book, agent, [line_id])
            total += r["cost_usd"]
            conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "-"
            print(f"[investigate] {line_id}: {r['outcome']} (confidence {conf}, run {r['run_id']}, "
                  f"${r['cost_usd']:.4f})", flush=True)
    print(f"[investigate] done: ${total:.4f} in all")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--line", help="investigate this one line, whatever its status")
    args = ap.parse_args(argv)
    telemetry.setup("repaydesk-investigator")
    try:
        return anyio.run(amain, args)
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
