"""The drafting agent: one message per late loan, under the collections policy, for a person to approve.

For each loan on the morning list (`queue`), Repaydesk gathers the facts itself, live where it
matters (the arrears and days late from core banking; the borrower's name from the directory; the
loan's earlier messages and promises), and asks Claude Sonnet 5.5 for one message in a fixed shape.
No lookups: the drafter needs nothing it isn't handed, so it has no tools.

Every draft is checked against the policy's checkable rules (`policy.check`). A draft that fails gets
one repair: the model is shown exactly which checks failed and asked again. Whatever comes back is
saved as a draft with its checks: one that still fails is shown with its failures and can't be
approved until a person fixes it. Nothing is sent from here; a portfolio manager approves, and the
gateway sends (chunk 16). Every run is recorded in `agent_runs` (agent `collector`).

    python -m repaydesk.collections.draft --limit 10
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.collections import policy
from repaydesk.domain import bank_today
from repaydesk.investigator import Run

AGENT = "collector"
ACTOR = f"agent:{AGENT}"
DRAFTER_MODEL = os.getenv("DRAFTER_MODEL", "claude-sonnet-5-5")
CHANNEL = {"reminder": "sms", "follow_up": "sms", "broken_promise": "sms", "arrangement": "whatsapp"}

SYSTEM = """\
You write one message from Demo Microfinance Bank, a Nigerian microfinance bank, to a borrower whose
loan repayment is late. A portfolio manager reads every message before it is sent.

The bank's collections policy, which every message must follow:
- Address the borrower by first name, and say it's from Demo Microfinance Bank.
- Name the loan reference and the amount now due, exactly as given, in naira (₦60,500).
- Say how to pay: into the loan's virtual account, or at any branch with the loan reference.
- Offer to talk: they can reply to this message, or call their branch.
- Never threaten, shame or frighten: no police, arrest, court, lawyers, EFCC, blacklists or credit
  bureaus; nothing about their family, contacts, or other people knowing.
- No links. Never ask for a PIN, password, BVN, card number or OTP.
- No capitals for emphasis; at most one exclamation mark.
- Plain, warm English a trader reads at a glance. SMS: at most 320 characters. WhatsApp: at most 600.

Tone by kind:
- reminder (1 to 7 days late): friendly; it may simply be an oversight.
- follow_up (8 to 30 days): firm and polite; ask them to pay, or reply with the date they will pay.
- arrangement (31 to 90 days): serious and supportive; invite them to talk and agree a plan.
- broken_promise: firm; mention the date they promised, ask for a new date or a conversation.

Write only the message itself, then a one-line note to the manager on why you wrote it that way."""


class Draft(BaseModel):
    body: str = Field(description="The message exactly as it will be sent.")
    note: str = Field(description="One line for the portfolio manager: why this wording.")


@dataclass
class Case:
    """Everything the drafter is told about one loan, gathered by Repaydesk."""

    loan_ref: str
    borrower_ref: str
    first_name: str
    bucket: str
    channel: str
    amount_due_kobo: int
    days_late: int
    messages_last_7_days: int
    earlier: list[dict]
    promise: dict | None

    def facts(self) -> policy.Facts:
        until = None
        if self.promise and self.promise["status"] == "open":
            until = datetime.fromisoformat(self.promise["due_on"]).date()
        return policy.Facts(self.loan_ref, self.amount_due_kobo, self.days_late, self.first_name,
                            self.messages_last_7_days, until, bank_today())

    def brief(self) -> dict:
        return {"kind": self.bucket, "channel": self.channel, "first_name": self.first_name,
                "loan_ref": self.loan_ref, "amount_due": f"₦{self.amount_due_kobo / 100:,.0f}"
                if self.amount_due_kobo % 100 == 0 else f"₦{self.amount_due_kobo / 100:,.2f}",
                "days_late": self.days_late, "earlier_messages": self.earlier, "promise": self.promise}


def gather(conn: psycopg.Connection, core: CoreBankingReader, item: dict) -> Case | None:
    """The facts for one loan on the morning list, read live. None when there's nothing to ask for any
    more (paid since the directory's copy, or closed)."""
    view = core.get_loan(item["loan_ref"])
    if not view or view.status != "active" or view.arrears <= 0 or view.days_late <= 0:
        return None
    bucket = "broken_promise" if item["bucket"] == "broken_promise" else policy.bucket_for(view.days_late)
    if bucket not in CHANNEL:
        return None  # over 90 days now: the recovery team's
    earlier = [{"sent": r[0].date().isoformat() if r[0] else None, "kind": r[1], "status": r[2], "text": r[3]}
               for r in conn.execute(
                   "SELECT coalesce(sent_at, created_at), bucket, status, body FROM collection_messages "
                   "WHERE loan_ref = %s ORDER BY id DESC LIMIT 3", (item["loan_ref"],))]
    last7 = conn.execute("SELECT count(*) FROM collection_messages WHERE loan_ref = %s AND status IN "
                         "('approved', 'sent') AND created_at > now() - interval '7 days'",
                         (item["loan_ref"],)).fetchone()[0]
    promise = conn.execute("SELECT status, due_on, amount_kobo FROM promises WHERE loan_ref = %s "
                           "ORDER BY id DESC LIMIT 1", (item["loan_ref"],)).fetchone()
    return Case(item["loan_ref"], item["borrower_ref"], item["borrower_name"].split()[0], bucket, CHANNEL[bucket],
                view.arrears, view.days_late, last7, earlier,
                {"status": promise[0], "due_on": promise[1].isoformat(), "amount": f"₦{promise[2] / 100:,.0f}"}
                if promise else None)


class Drafter:
    def __init__(self, client):
        self.client = client

    async def _ask(self, run: Run, messages: list[dict]) -> Draft:
        msg = await self.client.beta.messages.parse(
            model=DRAFTER_MODEL, max_tokens=2000, system=SYSTEM, output_config={"effort": "low"},
            output_format=Draft, messages=messages)
        run.add_usage(DRAFTER_MODEL, msg.usage)
        if msg.stop_reason == "refusal" or msg.parsed_output is None:
            raise RuntimeError(f"no draft in the required shape (stopped: {msg.stop_reason})")
        return msg.parsed_output

    async def draft(self, case: Case) -> tuple[Run, Draft | None, list[dict], int]:
        """The draft, its checks, and how many attempts it took (1, or 2 after a repair)."""
        run = Run(case.loan_ref)
        brief = json.dumps(case.brief(), ensure_ascii=False, indent=1)
        messages = [{"role": "user", "content": f"Write the message for this loan.\n\n{brief}"}]
        try:
            first = await self._ask(run, messages)
            checks = policy.check(first.body, case.channel, case.facts())
            if policy.sendable(checks):
                return run, first, checks, 1
            failed = "\n".join(f"- {c['check']}: {c['detail']}" for c in checks if not c["ok"])
            messages += [{"role": "assistant", "content": json.dumps(first.model_dump(), ensure_ascii=False)},
                         {"role": "user", "content": f"That draft fails the bank's checks:\n{failed}\n"
                                                     "Rewrite it so it passes, keeping to the policy."}]
            second = await self._ask(run, messages)
            return run, second, policy.check(second.body, case.channel, case.facts()), 2
        except Exception as e:  # recorded on the run; nothing is saved for the loan
            run.error = f"{type(e).__name__}: {e}"[:1000]
            return run, None, [], 0


def record(conn: psycopg.Connection, case: Case, run: Run, draft: Draft | None, checks: list[dict],
           attempts: int) -> dict:
    """Save the run, and the draft for a person to approve, in one transaction."""
    outcome = "failed" if run.error or draft is None else "drafted"
    with conn.transaction():
        run_id = conn.execute(
            "INSERT INTO agent_runs (loan_ref, agent, started_at, finished_at, model, outcome, verdict, checks, steps, "
            "usage, cost_usd, error, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (case.loan_ref, AGENT, run.started, datetime.now(UTC), DRAFTER_MODEL, outcome,
             Jsonb({**draft.model_dump(), "attempts": attempts, "brief": case.brief()}) if draft else None,
             Jsonb(checks), Jsonb([]), Jsonb(run.usage), run.cost_usd(), run.error, telemetry.current_trace_id()),
        ).fetchone()[0]
        message_id = None
        if draft:
            message_id = conn.execute(
                "INSERT INTO collection_messages (loan_ref, borrower_ref, bucket, channel, body, amount_due_kobo, "
                "drafted_by, run_id, checks) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (case.loan_ref, case.borrower_ref, case.bucket, case.channel, draft.body.strip(), case.amount_due_kobo,
                 ACTOR, run_id, Jsonb(checks)),
            ).fetchone()[0]
            db.audit(conn, ACTOR, "message.drafted", f"loan:{case.loan_ref}",
                     {"message_id": message_id, "run_id": run_id, "bucket": case.bucket,
                      "passes_policy": policy.sendable(checks), "attempts": attempts}, telemetry.current_trace_id())
    return {"run_id": run_id, "message_id": message_id, "outcome": outcome, "attempts": attempts,
            "passes_policy": policy.sendable(checks), "cost_usd": float(run.cost_usd())}


async def draft_morning(conn: psycopg.Connection, core: CoreBankingReader, drafter: Drafter, items: list[dict]) -> list:
    results = []
    for item in items:
        case = gather(conn, core, item)
        if case is None:
            results.append({"loan_ref": item["loan_ref"], "outcome": "skipped: nothing due any more"})
            continue
        run, draft, checks, attempts = await drafter.draft(case)
        results.append({"loan_ref": case.loan_ref, **record(conn, case, run, draft, checks, attempts)})
    return results
