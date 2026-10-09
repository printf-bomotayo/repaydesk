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
