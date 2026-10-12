"""The investigator's eval: a fixed set of hard payments, run dry, graded against the truth.

    python -m repaydesk.evals select --data-dir data          # (re)write evals/cases.json from the truth file
    python -m repaydesk.evals run                              # investigate every case; report to evals/reports/
    python -m repaydesk.evals run --limit 5 --concurrency 2   # a pilot

**The cases** are payments from the seeded week (seed 42, 2,000 borrowers, as of 8 October 2026)
that rules leave for the agent: every one of the rare kinds (group leaders, a split, someone paying
for someone else, typos, Pidgin, payer only, and the ones nobody could place), plus a fixed sample of
the common ones (names, trading names, cash sheets). The expected answer is the generator's truth,
written when the payment was made up, never a model's output.

**Dry.** Each case runs through the real investigator and Repaydesk's real checks, against the live
stack, but nothing is written: no run, no proposal, no suspense. The live review queue is untouched.

**Grading** is exact, per case:
- a placeable payment is *correct* when the checked answer is a match with the same loans and the
  same amounts to the kobo, *wrong* when it is any other match or an exclusion (money would move to
  the wrong place, if a person approved it), and *held* when it went to suspense;
- an unplaceable payment is *correct* when it went to suspense and *wrong* otherwise.

The headline is accuracy, but the number that matters most is **wrong**: every wrong answer is one a
reviewer has to catch. Cost, tool calls and seconds are recorded per case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import anyio

from repaydesk import investigator as inv

ROOT = Path(__file__).resolve().parents[4]  # the repository, when run from a checkout
CASES = ROOT / "evals" / "cases.json"
REPORTS = ROOT / "evals" / "reports"

# Every case of these kinds goes in; they are rare, and each tests something different.
ALL_OF = ("group_leader", "split_across_loans", "third_party", "ref_typo", "cash_ref_typo", "pos_blank",
          "payer_only", "unresolvable", "pidgin")
# A fixed sample of these common ones.
SAMPLE_OF = {"business_name": 6, "name_only": 6, "cash_name": 6}


# ---- the case set ------------------------------------------------------------------------------------


def _order(key: str) -> str:
    """A stable, arbitrary order: the same sample every time, not just the earliest dates."""
    return hashlib.sha256(key.encode()).hexdigest()


def select(truth: dict) -> dict:
    lines = [t for t in truth["lines"] if "#" not in t["source_key"]]  # a redelivery is ingestion's, not the agent's
    by_case: dict[str, list[dict]] = defaultdict(list)
    for t in lines:
        by_case[t["case"]].append(t)
    chosen = [t for c in ALL_OF for t in by_case[c]]
    for c, n in SAMPLE_OF.items():
        chosen += sorted(by_case[c], key=lambda t: _order(t["source_key"]))[:n]
    cases = [{"id": t["source_key"], "case": t["case"], "placeable": t["resolvable"],
              "expected": sorted([[a["loan_ref"], a["amount"]] for a in t["allocations"]]) if t["resolvable"] else None,
              "note": t["note"]} for t in sorted(chosen, key=lambda t: (t["case"], t["source_key"]))]
    return {"book": {"seed": 42, "borrowers": 2000, "as_of": truth["as_of"]}, "cases": cases}


# ---- grading -----------------------------------------------------------------------------------------


def case_list(chosen: dict) -> str:
    """The case set as a table a person can read through and sign off."""
    book = chosen["book"]
    rows = ["# The investigator's eval cases", "",
            f"{len(chosen['cases'])} payments from the seeded week (seed {book['seed']}, {book['borrowers']:,} "
            f"borrowers, as of {book['as_of']}). Expected answers are the generator's truth. Written by "
            "`python -m repaydesk.evals select`; don't edit by hand.", "",
            "| Case (line id) | Kind | Expected | Note |", "|---|---|---|---|"]
    for c in chosen["cases"]:
        exp = ", ".join(f"{loan} ₦{kobo / 100:,.2f}" for loan, kobo in c["expected"]) if c["placeable"] else "suspense"
        rows.append(f"| `{c['id']}` | {c['case']} | {exp} | {c['note']} |")
    return "\n".join(rows) + "\n"


def grade(case: dict, action: str | None, allocations: list[list]) -> str:
    """correct, wrong or held (a placeable payment sent to suspense), or failed (no answer at all)."""
    if action is None:
        return "failed"
    if not case["placeable"]:
        return "correct" if action == "suspense" else "wrong"
    if action == "suspense":
        return "held"
    return "correct" if action == "match" and sorted(allocations) == case["expected"] else "wrong"


def interval(k: int, n: int) -> tuple[float, float]:
    """A 95% Wilson interval for k of n: how far the score could move from noise alone."""
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---- running -----------------------------------------------------------------------------------------


async def run_case(case: dict, agent: inv.Investigator, book) -> dict:
    started = time.monotonic()
    line = book.payment_line(case["id"])
    run = await agent.run(line)
    action, checks = (None, []) if run.error or not run.verdict else inv.check(run.verdict, line["amount"]["kobo"],
                                                                              book.core)
    v = run.verdict
    allocations = sorted([[a.loan_ref, a.amount_kobo] for a in v.allocations]) if v and action == "match" else []
    return {
        "id": case["id"], "case": case["case"], "placeable": case["placeable"], "expected": case["expected"],
        "grade": grade(case, action, allocations), "action": action, "allocations": allocations,
        "said": v.outcome if v else None, "said_allocations": sorted([[a.loan_ref, a.amount_kobo]
                                                                      for a in v.allocations]) if v else [],
        "confidence": v.confidence if v else None, "reason": v.reason if v else None,
        "failed_checks": [c["check"] for c in checks if not c["ok"]], "triage": run.triage.kind if run.triage else None,
        "tool_calls": len(run.steps), "tools": [s["tool"] for s in run.steps], "error": run.error,
        "cost_usd": float(run.cost_usd()), "usage": run.usage, "seconds": round(time.monotonic() - started, 1),
    }


async def run_all(cases: list[dict], concurrency: int) -> list[dict]:
    from mcp.client.client import Client

    from repaydesk import db
    from repaydesk.adapters.fineract import FineractCoreBanking
    from repaydesk.bankbook import Bankbook, read_only
    from repaydesk.investigate import model_client
    from repaydesk.servers import bankbook as bankbook_server

    client, fallbacks = model_client()
    results: list[dict] = []
    queue = list(cases)

    async def worker(n: int) -> None:
        # Each worker has its own database session and MCP server: a session runs one transaction at a time.
        core = FineractCoreBanking(os.environ["FINERACT_URL"], os.getenv("FINERACT_TENANT", "default"),
                                   os.environ["FINERACT_USERNAME"], os.environ["FINERACT_PASSWORD"], timeout=30)
        book = Bankbook(read_only(db.connect(os.environ["REPAYDESK_DATABASE_URL"])), core)
        async with Client(bankbook_server.build(book)) as mcp:
            agent = inv.Investigator(client, mcp, fallbacks=fallbacks)
            while queue:
                case = queue.pop(0)
                r = await run_case(case, agent, book)
                results.append(r)
                print(f"[eval] {len(results):>3}/{len(cases)} {r['grade']:<7} {r['case']:<18} {r['id']} "
                      f"({r['confidence'] if r['confidence'] is not None else '-'}, ${r['cost_usd']:.4f}, "
                      f"{r['tool_calls']} tools)", flush=True)

    async with anyio.create_task_group() as tg:
        for n in range(max(1, min(concurrency, len(cases)))):
            tg.start_soon(worker, n)
    order = {c["id"]: i for i, c in enumerate(cases)}
    return sorted(results, key=lambda r: order[r["id"]])


# ---- the report --------------------------------------------------------------------------------------


def summary(results: list[dict]) -> dict:
    n = len(results)
    grades = Counter(r["grade"] for r in results)
    placeable = [r for r in results if r["placeable"]]
    unplaceable = [r for r in results if not r["placeable"]]

    def share(rows: list[dict]) -> float | None:
        return sum(1 for r in rows if r["grade"] == "correct") / len(rows) if rows else None

    correct = grades["correct"]
    lo, hi = interval(correct, n)
    proposed = [r for r in results if r["action"] in ("match", "exclude")]
    return {
        "cases": n, "correct": correct, "wrong": grades["wrong"], "held": grades["held"], "failed": grades["failed"],
        "accuracy": correct / n if n else 0.0, "accuracy_95": [lo, hi],
        # Of what the agent put in front of a reviewer, how much was right.
        "precision": share(proposed), "placed_correctly": share(placeable), "held_when_unplaceable": share(unplaceable),
        "unplaceable_cases": len(unplaceable),
        "checks_caught": sum(1 for r in results if r["said"] == "match" and r["action"] == "suspense"),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "cost_per_case": round(sum(r["cost_usd"] for r in results) / n, 4) if n else 0,
        "tool_calls_per_case": round(sum(r["tool_calls"] for r in results) / n, 1) if n else 0,
        "seconds_per_case": round(sum(r["seconds"] for r in results) / n, 1) if n else 0,
    }


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def markdown(results: list[dict], s: dict, meta: dict) -> str:
    lo, hi = s["accuracy_95"]
    out = [
        f"# Investigator eval, {meta['date']}",
        "",
        f"{meta['investigator']} investigating, {meta['classifier']} triage, confidence bar "
        f"{meta['min_confidence']}. {s['cases']} cases from `evals/cases.json`, one pass each, run dry against the "
        f"seeded stack.",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Correct | {s['correct']} of {s['cases']} ({_pct(s['accuracy'])}; 95% interval {_pct(lo)} to {_pct(hi)}) |",
        f"| **Wrong** (a reviewer must catch these) | **{s['wrong']}** |",
        f"| Held in suspense though placeable | {s['held']} |",
        f"| Failed (no answer) | {s['failed']} |",
        f"| Precision: right, of what was proposed | {_pct(s['precision'])} |",
        f"| Placeable payments placed correctly | {_pct(s['placed_correctly'])} |",
        f"| Unplaceable payments held ({s['unplaceable_cases']} cases) | {_pct(s['held_when_unplaceable'])} |",
        f"| Matches the checks turned into suspense | {s['checks_caught']} |",
        f"| Cost | ${s['cost_usd']:.2f} in all, ${s['cost_per_case']:.4f} a case |",
        f"| Tool calls a case | {s['tool_calls_per_case']} |",
        f"| Seconds a case | {s['seconds_per_case']} |",
        "",
        "## By kind of payment",
        "",
        "| Kind | Cases | Correct | Wrong | Held | Failed |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    kinds: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        kinds[r["case"]][r["grade"]] += 1
        kinds[r["case"]]["n"] += 1
    for kind, c in sorted(kinds.items()):
        out.append(f"| {kind} | {c['n']} | {c['correct']} | {c['wrong']} | {c['held']} | {c['failed']} |")
    out += ["", "## Every case", "",
            "| Case | Kind | Grade | Expected | Answered | Confidence | Tools | Cost | Reason |",
            "|---|---|---|---|---|---:|---:|---:|---|"]
    for r in results:
        exp = ", ".join(f"{loan} {kobo / 100:,.0f}" for loan, kobo in r["expected"]) if r["placeable"] else "suspense"
        got = r["action"] or "failed"
        if r["action"] == "match":
            got = ", ".join(f"{loan} {kobo / 100:,.0f}" for loan, kobo in r["allocations"])
        elif r["said"] == "match" and r["action"] == "suspense":
            got = f"suspense (checks: {', '.join(r['failed_checks'])})"
        reason = (r["reason"] or r["error"] or "").replace("|", "/").replace("\n", " ")
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "-"
        out.append(f"| `{r['id']}` | {r['case']} | {r['grade']} | {exp} | {got} | {conf} | {r['tool_calls']} | "
                   f"${r['cost_usd']:.3f} | {reason[:220]} |")
    out += ["", "Grading: a placeable payment is correct only when the checked answer names the same loans with the "
            "same amounts to the kobo; an unplaceable one only when it goes to suspense. Expected answers are the "
            "synthetic generator's truth, never a model's.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sel = sub.add_parser("select", help="write the case set from the truth file")
    sel.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data")))
    sel.add_argument("--out", type=Path, default=CASES)
    run = sub.add_parser("run", help="run the cases and write a report")
    run.add_argument("--cases", type=Path, default=CASES)
    run.add_argument("--limit", type=int)
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--out", type=Path, default=REPORTS)
    args = ap.parse_args(argv)

    if args.command == "select":
        truth = json.loads((args.data_dir / "truth" / "truth.json").read_text(encoding="utf-8"))
        chosen = select(truth)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(chosen, indent=1) + "\n", encoding="utf-8")
        args.out.with_suffix(".md").write_text(case_list(chosen), encoding="utf-8")
        print(f"[eval] {len(chosen['cases'])} cases written to {args.out}: "
              + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c['case'] for c in chosen['cases']).items())))
        return 0

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"][: args.limit]
    results = anyio.run(run_all, cases, args.concurrency)
    s = summary(results)
    when = datetime.now(UTC)
    meta = {"date": when.strftime("%Y-%m-%d %H:%M UTC"), "investigator": inv.INVESTIGATOR_MODEL,
            "classifier": inv.CLASSIFIER_MODEL, "min_confidence": inv.MIN_CONFIDENCE}
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{when:%Y-%m-%d-%H%M}-{inv.INVESTIGATOR_MODEL}"
    stem.with_suffix(".md").write_text(markdown(results, s, meta), encoding="utf-8")
    stem.with_suffix(".jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    lo, hi = s["accuracy_95"]
    print(f"[eval] {s['correct']}/{s['cases']} correct ({_pct(s['accuracy'])}, 95% {_pct(lo)} to {_pct(hi)}), "
          f"{s['wrong']} wrong, {s['held']} held, {s['failed']} failed; ${s['cost_usd']:.2f}. Report: {stem}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
