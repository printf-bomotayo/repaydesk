"""Draft the morning's collections messages: one per loan due a message, for a person to approve.

    python -m repaydesk.collections.draft                 # the next 10 loans on the morning list
    python -m repaydesk.collections.draft --limit 30
    python -m repaydesk.collections.draft --bucket broken_promise

Uses the same model provider settings as the investigator (`MODEL_PROVIDER`, `ANTHROPIC_API_KEY`).
Writes drafts and agent runs only; nothing is sent.
"""

from __future__ import annotations

import argparse
import os
import sys

import anyio

from repaydesk import db, telemetry
from repaydesk.collections import drafter, queue
from repaydesk.domain import bank_today


async def amain(args) -> int:
    from repaydesk.adapters.fineract import FineractCoreBanking
    from repaydesk.investigate import model_client

    conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
    db.migrate(conn)
    core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                               os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=30)
    items = queue.overview(conn, bank_today(), args.bucket, args.limit)["due"]["items"]
    if not items:
        print("[draft] nobody is due a message")
        return 0
    client, _ = model_client()
    print(f"[draft] {len(items)} loan(s), {drafter.DRAFTER_MODEL}", flush=True)
    total = 0.0
    for result in await drafter.draft_morning(conn, core, drafter.Drafter(client), items):
        total += result.get("cost_usd", 0)
        detail = (f" (passes the policy: {result['passes_policy']}, attempts {result['attempts']}, "
                  f"run {result['run_id']})") if "run_id" in result else ""
        print(f"[draft] {result['loan_ref']}: {result['outcome']}{detail}", flush=True)
    print(f"[draft] done: ${total:.4f} in all")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--bucket", choices=["reminder", "follow_up", "arrangement", "broken_promise"])
    args = ap.parse_args(argv)
    telemetry.setup("repaydesk-drafter")
    try:
        return anyio.run(amain, args)
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
