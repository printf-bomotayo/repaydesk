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

test("evidence nobody taught it to read is still shown", () => {
  expect(evidenceChecks({ odd_signal: 3 })).toEqual([{ ok: null, text: "odd signal: 3" }]);
  expect(evidenceChecks(null)).toEqual([]);
});
