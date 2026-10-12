import { evidenceChecks } from "./evidence";

test("a reference match reads as the checks the rule made", () => {
  const checks = evidenceChecks({
    quoted_reference: "LN-2026-000539",
    shared_names: ["SALAMI"],
    amount_is_due: true,
    same_branch: null,
  });
  expect(checks).toEqual([
    { ok: true, text: "Quotes LN-2026-000539, a loan that exists" },
    { ok: true, text: "The payer's name shares SALAMI with the borrower" },
    { ok: true, text: "The amount is one the loan has due" },
    { ok: null, text: "Branch not known for this channel" },
  ]);
});

test("an exclusion reads as its meaning, not its pattern", () => {
  expect(evidenceChecks({ meaning: "an internal float top-up", narration_pattern: "\bHQ OPERATIONS FLOAT\b" })).toEqual([
    { ok: true, text: "Not a repayment: an internal float top-up" },
    { ok: true, text: "Recognised from the narration" },
  ]);
});

test("evidence nobody taught it to read is still shown", () => {
  expect(evidenceChecks({ odd_signal: 3 })).toEqual([{ ok: null, text: "odd signal: 3" }]);
  expect(evidenceChecks(null)).toEqual([]);
});

test("the agent's reason comes first, then each finding as it weighed it", () => {
  const checks = evidenceChecks({
    reason: "Trading name and amount agree.",
    findings: [
      { fact: "Ifeoma Okonkwo trades as Ifeoma Pharmacy and owes exactly ₦605,000.", bearing: "for" },
      { fact: "Ifeoma Eze also trades as Ifeoma Pharmacy; ₦605,000 doesn't fit her loan.", bearing: "against" },
      { fact: "No earlier payments from this payer.", bearing: "neutral" },
    ],
    also_considered: ["LN-2026-000199", "LN-2026-000716"],
    run_id: 7,
  });
  expect(checks.map((c) => c.ok)).toEqual([null, true, false, null, null]);
  expect(checks[0].text).toBe("Trading name and amount agree.");
  expect(checks[4].text).toBe("Also considered: LN-2026-000199, LN-2026-000716");
});
