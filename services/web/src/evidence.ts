// A proposal's evidence, as the reviewer reads it. Rules record what they checked as keys
// (match.py); here each known key becomes a sentence, marked as agreeing, disagreeing or unknown.
// The investigator agent records its reason and its findings, each for, against or neutral
// (investigator.py). Unknown keys are still shown, as they are, so nothing a proposer recorded is hidden.

export interface Check {
  /** true: agrees · false: disagrees · null: couldn't tell */
  ok: boolean | null;
  text: string;
}

type Evidence = Record<string, unknown>;

const BEARING: Record<string, boolean | null> = { for: true, against: false, neutral: null };

const KNOWN: Record<string, (v: unknown, e: Evidence) => Check | Check[] | null> = {
  // The agent's: its reason first, then each finding as it weighed it.
  reason: (v) => ({ ok: null, text: String(v) }),
  findings: (v) =>
    (Array.isArray(v) ? v : []).map((f: { fact?: string; bearing?: string }) => ({
      ok: BEARING[f.bearing ?? "neutral"] ?? null,
      text: String(f.fact ?? ""),
    })),
  also_considered: (v) => {
    const others = Array.isArray(v) ? (v as string[]) : [];
    return others.length ? { ok: null, text: `Also considered: ${others.join(", ")}` } : null;
  },
  // The run's id links to the agent's run on the case file; it isn't evidence in itself.
  run_id: () => null,
  virtual_account: (v) => ({ ok: true, text: `Paid into ${v}, the loan's own virtual account` }),
  loan_status: (v) => ({ ok: v === "active", text: `The loan is ${v}` }),
  quoted_reference: (v) => ({ ok: true, text: `Quotes ${v}, a loan that exists` }),
  shared_names: (v) => {
    const names = Array.isArray(v) ? v : [];
    return names.length
      ? { ok: true, text: `The payer's name shares ${names.join(" and ")} with the borrower` }
      : { ok: false, text: "The payer's name shares nothing with the borrower" };
  },
  amount_is_due: (v) =>
    v ? { ok: true, text: "The amount is one the loan has due" } : { ok: null, text: "The amount isn't one the loan has due" },
  same_branch: (v) =>
    v === true
      ? { ok: true, text: "Paid at the borrower's own branch" }
      : v === false
        ? { ok: false, text: "Paid at another branch" }
        : { ok: null, text: "Branch not known for this channel" },
  phone_matches: (v) => ({ ok: true, text: `The phone typed at the terminal is ${v}'s, who has one active loan` }),
  meaning: (v) => ({ ok: true, text: `Not a repayment: ${v}` }),
  // The pattern itself is for engineers (it stays in the stored evidence); a reviewer needs the meaning.
  narration_pattern: () => ({ ok: true, text: "Recognised from the narration" }),
};

export function evidenceChecks(evidence: Evidence | null | undefined): Check[] {
  if (!evidence) return [];
  return Object.entries(evidence).flatMap(([key, value]) => {
    const known = KNOWN[key];
    const check = known ? known(value, evidence) : { ok: null, text: `${key.replace(/_/g, " ")}: ${JSON.stringify(value)}` };
    return check === null ? [] : Array.isArray(check) ? check : [check];
  });
}
