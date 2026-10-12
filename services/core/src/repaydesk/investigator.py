"""The investigator agent: for a payment no rule could place, work out whose loan it repays.

One investigation, start to finish:

1. **Triage.** A small, cheap model (Claude Haiku 5.5) reads the narration and says what kind of
   payment it looks like: a person's name, a business, a savings group, someone paying for
   someone else, Pidgin, no name at all. The investigator gets that as a starting hint.
2. **Investigation.** Claude Sonnet 5.5 looks things up through the bankbook MCP server (ADR 0008)
   and nothing else: borrowers by fuzzy name, loans read live, how each loan was paid before. It
   answers in a fixed shape: a match (one loan, or a split), not a repayment, or suspense, with
   its confidence and the findings for and against.
3. **Checks.** Repaydesk doesn't take the answer on trust. Before anything is saved, the amounts
   must add up to the payment to the kobo, every loan must exist and be active in core banking,
   and the confidence must clear the bar (`AGENT_MIN_CONFIDENCE`). Anything that fails becomes
   suspense, with the reason.
4. **A proposal, for people.** A match or an exclusion becomes a proposal by
   `agent:investigator` in the review queue, with the same approval rules as a rule's. Nothing an
   agent concludes is posted without a person; suspense waits for one.

Every run is recorded in `agent_runs`: the triage, each tool call and what it returned, the answer,
the checks, tokens and cost. ADR 0009.

    python -m repaydesk.investigate --limit 10      # the next ten unplaced lines
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import psycopg
from opentelemetry import trace
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from repaydesk import db, telemetry
from repaydesk.adapters.ports import CoreBankingReader
from repaydesk.bankbook import Bankbook

tracer = trace.get_tracer("repaydesk.investigator")

AGENT = "investigator"
ACTOR = f"agent:{AGENT}"
INVESTIGATOR_MODEL = os.getenv("INVESTIGATOR_MODEL", "claude-sonnet-5-5")
CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "claude-haiku-5-5")
MIN_CONFIDENCE = float(os.getenv("AGENT_MIN_CONFIDENCE", "0.7"))
MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "14"))

# US dollars per million tokens (Claude API list prices, 6 Oct 2026): input, output, cache read,
# cache write. Used to record what each run cost; the bill is the source of truth.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


# ---- what the models answer --------------------------------------------------------------------


class Triage(BaseModel):
    """What kind of payment the narration describes, before anything is looked up."""

    kind: Literal["names_a_person", "names_a_business", "names_a_group", "pays_for_someone_else",
                  "quotes_a_reference", "no_name", "not_a_repayment", "unclear"]
    names: list[str] = Field(description="People named, as written, e.g. ['EZE C'].")
    business: str | None = Field(description="A trading name, if one is written.")
    group: str | None = Field(description="A savings group or association, if one is written.")
    paid_for: str | None = Field(description="Who the payment is for, when someone pays for someone else.")
    reference: str | None = Field(description="A loan or customer reference as written, even if mistyped.")
    language: Literal["english", "pidgin", "other"]


class Allocation(BaseModel):
    loan_ref: str
    amount_kobo: int


class Finding(BaseModel):
    fact: str = Field(description="One thing you looked up and what it showed, in a short sentence.")
    bearing: Literal["for", "against", "neutral"]


class Verdict(BaseModel):
    """The investigator's answer."""

    outcome: Literal["match", "not_repayment", "suspense"]
    allocations: list[Allocation] = Field(description="For a match: each loan and the kobo it receives. "
                                                      "They must add up to the payment exactly. Empty otherwise.")
    confidence: float = Field(description="0 to 1: how sure a careful officer would be, given the findings.")
    reason: str = Field(description="One or two sentences a reviewer reads first.")
    findings: list[Finding]
    candidates: list[str] = Field(description="Borrower or loan references you considered, best first.")


TRIAGE_PROMPT = """\
You sort incoming repayments at a Nigerian microfinance bank before they are investigated. Read one
payment line and say what kind of payment it describes. Don't guess identities; report what is
written. Narrations are bank-printed: names may be initialled (EZE C), reordered (BAKARE BABAJIDE),
a trading name (IFEOMA PHARMACY), a group's contribution (IFELUWA TRADERS WK CONTRIB), someone
paying for another (LOAN FOR IMAOBONG), or Pidgin ("loan money wey remain")."""

INVESTIGATOR_PROMPT = """\
You are a reconciliation investigator at Demo Microfinance Bank, a Nigerian microfinance bank. A
payment arrived that no rule could place. Work out which loan or loans it repays, using the
bankbook tools, and answer in the required shape.

How payments arrive here:
- Names are written every which way: surname first or last, initials, nicknames, "Mama"/"Iya"
  plus a child's name, or the borrower's trading name. Branch cash sheets name the depositor, who
  may be a relative. POS lines often carry no name, but may carry a phone number typed at the
  terminal (use borrowers_by_line_phone).
- A savings-group leader may pay several members' instalments in one transfer. That is a split:
  one allocation per member's loan, each the amount that member owes, adding up to the payment.
- Someone may pay for another person ("LOAN FOR IMAOBONG"); the beneficiary's loan is the match.
- Text may be Pidgin. A reversal, an interest credit or an internal float is not a repayment.

How to decide:
- The amount is strong evidence: an instalment due, the arrears, or the whole balance (get_loan
  lists the amounts that would fit). So is the same payer paying the same loan before
  (payment_history, find_payments). A name match alone is weak when the name is common.
- When two or more borrowers fit about equally, don't choose: answer suspense and say who and why.
  A person will phone the branch. A wrong match moves someone else's money; suspense costs a call.
- Calibrate confidence: above 0.9 only when name, amount and history agree; 0.7 to 0.9 when two
  agree and nothing points elsewhere; below 0.7 means suspense.
- Allocations are in kobo and must add up to the payment's amount exactly.
- Be economical: a handful of lookups usually settles it. Stop when the evidence is clear."""


# ---- one investigation ---------------------------------------------------------------------------


@dataclass
class Run:
    line_id: str
    triage: Triage | None = None
    verdict: Verdict | None = None
    steps: list[dict] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    error: str | None = None

    def add_usage(self, model: str, usage: Any) -> None:
        u = self.usage.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        u["input"] += usage.input_tokens or 0
        u["output"] += usage.output_tokens or 0
        u["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        u["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost_usd(self) -> Decimal:
        total = Decimal(0)
        for model, u in self.usage.items():
            p_in, p_out, p_read, p_write = PRICES.get(model, PRICES[INVESTIGATOR_MODEL])
            total += (Decimal(u["input"]) * Decimal(str(p_in)) + Decimal(u["output"]) * Decimal(str(p_out))
                      + Decimal(u["cache_read"]) * Decimal(str(p_read))
                      + Decimal(u["cache_write"]) * Decimal(str(p_write))) / 1_000_000
        return total.quantize(Decimal("0.000001"))


def _text(content: Any, limit: int = 6000) -> str:
    """A tool result's content as plain text for the run record, cut to a readable length."""
    if isinstance(content, str):
        text = content
    else:
        text = "\n".join(getattr(c, "text", None) or (c.get("text", "") if isinstance(c, dict) else str(c))
                         for c in content)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more characters]"


class Investigator:
    """Runs investigations. `client` is an AsyncAnthropic (or a Bedrock client with the same surface);
    `mcp` is a connected MCP client session to the bankbook server."""

    def __init__(self, client: Any, mcp: Any, *, fallbacks: bool = True):
        self.client = client
        self.mcp = mcp
        self.fallbacks = fallbacks
        self._tools: list | None = None

    async def tools(self) -> list:
        from anthropic.lib.tools.mcp import async_mcp_tool

        if self._tools is None:
            listed = (await self.mcp.list_tools()).tools
            self._tools = [async_mcp_tool(t, self.mcp) for t in sorted(listed, key=lambda t: t.name)]
        return self._tools

    def _extra(self) -> dict:
        # Claude Sonnet 5.5 can decline a request on safety grounds; with fallbacks the API reruns it
        # on a fallback model in the same call instead. Claude API only (not Bedrock).
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if self.fallbacks else {}

    async def triage(self, run: Run, line: dict) -> None:
        shown = {k: line[k] for k in ("channel", "amount", "narration", "payer_name", "reference_typed")}
        msg = await self.client.beta.messages.parse(
            model=CLASSIFIER_MODEL, max_tokens=2000, system=TRIAGE_PROMPT,
            output_config={"effort": "low"}, output_format=Triage,
            messages=[{"role": "user", "content": json.dumps(shown, ensure_ascii=False)}],
        )
        run.add_usage(CLASSIFIER_MODEL, msg.usage)
        run.triage = msg.parsed_output

    async def investigate(self, run: Run, line: dict) -> None:
        brief = {"payment": line, "triage": run.triage.model_dump() if run.triage else None}
        runner = self.client.beta.messages.tool_runner(
            model=INVESTIGATOR_MODEL, max_tokens=16000, system=INVESTIGATOR_PROMPT,
            tools=await self.tools(), output_format=Verdict, max_iterations=MAX_TOOL_ROUNDS,
            output_config={"effort": "medium"}, cache_control={"type": "ephemeral"},
            messages=[{"role": "user", "content": "Investigate this payment.\n\n"
                                                  + json.dumps(brief, ensure_ascii=False, indent=1)}],
            **self._extra(),
        )
        last = None
        async for message in runner:
            last = message
            run.add_usage(INVESTIGATOR_MODEL, message.usage)
            calls = [b for b in message.content if b.type == "tool_use"]
            if not calls:
                continue
            started = time.monotonic()
            reply = await runner.generate_tool_call_response()  # runs the tools once; the runner reuses it
            ms = round((time.monotonic() - started) * 1000)
            results = {r["tool_use_id"]: r for r in (reply or {}).get("content", []) if isinstance(r, dict)}
            for call in calls:
                result = results.get(call.id, {})
                run.steps.append({"tool": call.name, "input": call.input, "output": _text(result.get("content", "")),
                                  "error": bool(result.get("is_error")), "ms": ms // max(len(calls), 1)})
        if last is None:
            raise RuntimeError("the model returned nothing")
        if last.stop_reason == "refusal":
            raise RuntimeError("the model declined to investigate this payment")
        if last.parsed_output is None:
            raise RuntimeError(f"no answer in the required shape (stopped: {last.stop_reason})")
        run.verdict = last.parsed_output

    async def run(self, line: dict) -> Run:
        run = Run(line["line_id"])
        with tracer.start_as_current_span("agent.investigate") as span:
            span.set_attributes({"payment.line_id": run.line_id, "gen_ai.system": "anthropic",
                                 "gen_ai.request.model": INVESTIGATOR_MODEL})
            try:
                await self.triage(run, line)
                await self.investigate(run, line)
            except Exception as e:  # recorded on the run; the line stays where it was
                run.error = f"{type(e).__name__}: {e}"[:1000]
                span.record_exception(e)
            for model, u in run.usage.items():
                span.set_attribute(f"gen_ai.usage.{model}.input_tokens", u["input"])
                span.set_attribute(f"gen_ai.usage.{model}.output_tokens", u["output"])
        return run


# ---- checking the answer, and acting on it -------------------------------------------------------


def check(verdict: Verdict, line_amount: int, core: CoreBankingReader) -> tuple[str, list[dict]]:
    """Repaydesk's own checks on the agent's answer. Returns what to do (match, exclude or suspense)
    and each check with its result, for the run record."""
    checks: list[dict] = []

    def note(name: str, ok: bool, detail: str) -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    if verdict.outcome == "suspense":
        return "suspense", checks
    confident = note("confidence", verdict.confidence >= MIN_CONFIDENCE,
                     f"{verdict.confidence:.2f} against a bar of {MIN_CONFIDENCE:.2f}")
    if verdict.outcome == "not_repayment":
        return ("exclude" if confident else "suspense"), checks

    allocs = verdict.allocations
    ok = note("has_allocations", bool(allocs), f"{len(allocs)} allocation(s)")
    total = sum(a.amount_kobo for a in allocs)
    ok &= note("adds_up", total == line_amount and all(a.amount_kobo > 0 for a in allocs),
               f"allocations total {total} kobo; the payment is {line_amount} kobo")
    ok &= note("one_per_loan", len({a.loan_ref for a in allocs}) == len(allocs), "no loan listed twice")
    for a in allocs:
        view = core.get_loan(a.loan_ref)
        ok &= note("loan_active", bool(view and view.status == "active"),
                   f"{a.loan_ref}: {view.status if view else 'not found in core banking'}")
    return ("match" if ok and confident else "suspense"), checks


def record(conn: psycopg.Connection, run: Run, line_amount: int, core: CoreBankingReader) -> dict:
    """Save the run and act on it, in one transaction: a proposal for people, or suspense, or nothing
    (a failed run leaves the line as it was, for the next attempt)."""
    trace_id = telemetry.current_trace_id()
    subject = f"payment_line:{run.line_id}"
    action, checks = ("failed", []) if run.error or not run.verdict else check(run.verdict, line_amount, core)
    v = run.verdict
    with conn.transaction():
        # Another run, or a person, may have placed the line meanwhile: then this answer is history only.
        status = conn.execute("SELECT status FROM payment_lines WHERE id = %s FOR UPDATE", (run.line_id,)).fetchone()[0]
        if action != "failed" and status not in ("new", "suspense"):
            checks.append({"check": "line_still_open", "ok": False, "detail": f"the line is now {status}"})
            action = "superseded"
        proposal_id = None
        if action in ("match", "exclude"):
            chosen = {a.loan_ref for a in v.allocations}
            evidence = {"reason": v.reason, "findings": [f.model_dump() for f in v.findings],
                        "also_considered": [c for c in v.candidates if c not in chosen]}
            proposal_id = conn.execute(
                "INSERT INTO proposals (line_id, kind, proposed_by, confidence, allocations, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (run.line_id, action, ACTOR, round(min(max(v.confidence, 0), 1), 3),
                 Jsonb([a.model_dump() for a in v.allocations] if action == "match" else []), Jsonb(evidence)),
            ).fetchone()[0]
            conn.execute("UPDATE payment_lines SET status = 'proposed' WHERE id = %s", (run.line_id,))
        elif action == "suspense":
            conn.execute("UPDATE payment_lines SET status = 'suspense' WHERE id = %s", (run.line_id,))
        outcome = {"match": "proposed", "exclude": "excluded", "suspense": "suspense"}.get(action, "failed")
        if action == "superseded":
            outcome = None
        run_id = conn.execute(
            "INSERT INTO agent_runs (line_id, agent, finished_at, model, classifier_model, classification, outcome, "
            "confidence, verdict, checks, proposal_id, steps, usage, cost_usd, error, trace_id) "
            "VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (run.line_id, AGENT, INVESTIGATOR_MODEL, CLASSIFIER_MODEL,
             Jsonb(run.triage.model_dump()) if run.triage else None, outcome,
             round(min(max(v.confidence, 0), 1), 3) if v else None, Jsonb(v.model_dump()) if v else None,
             Jsonb(checks), proposal_id, Jsonb(run.steps), Jsonb(run.usage), run.cost_usd(), run.error, trace_id),
        ).fetchone()[0]
        if proposal_id:
            conn.execute("UPDATE proposals SET evidence = evidence || %s WHERE id = %s",
                         (Jsonb({"run_id": run_id}), proposal_id))
            db.audit(conn, ACTOR, "proposal.created", subject,
                     {"proposal_id": proposal_id, "kind": action, "run_id": run_id,
                      "allocations": [a.model_dump() for a in v.allocations]}, trace_id)
        else:
            db.audit(conn, ACTOR, f"investigation.{outcome or 'superseded'}", subject,
                     {"run_id": run_id, "reason": (v.reason if v else run.error)}, trace_id)
    return {"run_id": run_id, "outcome": outcome or "superseded", "proposal_id": proposal_id,
            "confidence": v.confidence if v else None, "cost_usd": float(run.cost_usd())}


def open_lines(conn: psycopg.Connection, limit: int) -> list[str]:
    """Lines rules couldn't place (or a person sent back), not yet tried by the agent today, oldest first."""
    return [r[0] for r in conn.execute(
        "SELECT l.id FROM payment_lines l WHERE l.status = 'new' AND NOT EXISTS ("
        "  SELECT 1 FROM agent_runs r WHERE r.line_id = l.id AND r.started_at > now() - interval '1 day') "
        "ORDER BY l.value_date, l.id LIMIT %s", (limit,))]


async def investigate_lines(conn: psycopg.Connection, book: Bankbook, investigator: Investigator,
                            line_ids: list[str]) -> list[dict]:
    results = []
    for line_id in line_ids:
        line = book.payment_line(line_id)
        run = await investigator.run(line)
        out = record(conn, run, line["amount"]["kobo"], book.core)
        results.append({"line_id": line_id, **out})
    return results
