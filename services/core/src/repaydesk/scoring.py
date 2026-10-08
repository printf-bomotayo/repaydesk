"""Score proposals against the truth file, case by case.

Used for rules now and for the investigator agent from milestone 3. The truth file is read here
and nowhere an agent can reach.

A proposal is **correct** when it says exactly what the truth says: the same loans with the same
amounts, or "not a repayment" for a line that isn't one. It is **wrong** otherwise, including a
confident match on a line nobody could place. No proposal is neither: the line simply waits.

    python -m repaydesk.scoring                       # score the live proposals in the database
    python -m repaydesk.scoring --out evals/reports/  # and write a Markdown report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class CaseScore:
    lines: int = 0
    proposed: int = 0
    correct: int = 0
    wrong: list[str] = field(default_factory=list)


@dataclass
class Score:
    by_case: dict[str, CaseScore]

    @property
    def lines(self) -> int:
        return sum(c.lines for c in self.by_case.values())

    @property
    def proposed(self) -> int:
        return sum(c.proposed for c in self.by_case.values())

    @property
    def correct(self) -> int:
        return sum(c.correct for c in self.by_case.values())

    @property
    def wrong(self) -> list[str]:
        return [w for c in self.by_case.values() for w in c.wrong]

    def table(self) -> str:
        rows = ["| Case | Lines | Proposed | Correct | Wrong |", "|---|---:|---:|---:|---:|"]
        for case, c in sorted(self.by_case.items(), key=lambda kv: (-kv[1].lines, kv[0])):
            rows.append(f"| {case} | {c.lines} | {c.proposed} | {c.correct} | {len(c.wrong)} |")
        total = (self.lines, self.proposed, self.correct, len(self.wrong))
        rows.append("| **all** | " + " | ".join(f"**{n}**" for n in total) + " |")
        return "\n".join(rows)


def _allocations(items) -> list[tuple[str, int]]:
    return sorted((a["loan_ref"], int(a.get("amount_kobo", a.get("amount", 0)))) for a in items)


def score(truth: list[dict], proposals: dict[str, dict]) -> Score:
    """`truth`: the truth file's lines. `proposals`: line id -> {"kind", "allocations"}.
    Lines the truth calls duplicates are left out: ingestion handles them, never a proposal."""
    by_case: dict[str, CaseScore] = defaultdict(CaseScore)
    for t in truth:
        if t["case"] == "duplicate_delivery":
            continue
        c = by_case[t["case"]]
        c.lines += 1
        p = proposals.get(t["source_key"])
        if not p:
            continue
        c.proposed += 1
        if p["kind"] == "exclude":
            ok = not t["allocations"]
        else:
            ok = t["resolvable"] and _allocations(p["allocations"]) == _allocations(t["allocations"])
        if ok:
            c.correct += 1
        else:
            c.wrong.append(t["source_key"])
    return Score(dict(by_case))


def load_truth(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "truth" / "truth.json").read_text(encoding="utf-8"))["lines"]


def live_proposals(conn, proposed_by_prefix: str = "") -> dict[str, dict]:
    rows = conn.execute(
        "SELECT line_id, kind, allocations FROM proposals WHERE status IN ('pending', 'approved') "
        "AND proposed_by LIKE %s", (proposed_by_prefix + "%",)
    ).fetchall()
    return {line_id: {"kind": kind, "allocations": allocs} for line_id, kind, allocs in rows}


def main(argv: list[str] | None = None) -> int:
    from repaydesk import db

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data")))
    ap.add_argument("--proposed-by", default="", help="e.g. rule: or agent:")
    ap.add_argument("--out", type=Path, help="folder for a Markdown report")
    args = ap.parse_args(argv)

    conn = db.connect(os.environ["REPAYDESK_DATABASE_URL"])
    s = score(load_truth(args.data_dir), live_proposals(conn, args.proposed_by))
    print(s.table())
    print(f"\n{s.proposed} of {s.lines} lines proposed; {s.correct} correct, {len(s.wrong)} wrong")
    for w in s.wrong:
        print("  wrong:", w)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        who = (args.proposed_by.rstrip(":") or "all")
        path = args.out / f"{date.today():%Y-%m-%d}-{who}.md"
        path.write_text(f"# Proposals scored against the truth ({who})\n\n{s.table()}\n", encoding="utf-8")
        print("report:", path)
    return 1 if s.wrong else 0


if __name__ == "__main__":
    sys.exit(main())
