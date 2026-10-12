import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type RunDetail, type Staff } from "../api";

// A stand-in for the API with two runs: run 1 proposed a loan (and a supervisor has since approved
// it), run 2 held a payment in suspense.
const STAFF: Staff = { id: "hadiza", name: "Hadiza Lawal", role: "auditor", limit_kobo: 0 };

const RUN: RunDetail = {
  id: 1,
  started_at: "2026-10-12T09:14:00Z",
  finished_at: "2026-10-12T09:14:09Z",
  seconds: 9.4,
  agent: "investigator",
  model: "claude-sonnet-5-5",
  classifier_model: "claude-haiku-5-5",
  triage: { kind: "names_a_business", names: [], business: "IFEOMA PHARMACY", group: null, paid_for: null, reference: null, language: "english" },
  outcome: "proposed",
  confidence: 0.85,
  verdict: {
    outcome: "match",
    allocations: [{ loan_ref: "LN-2026-000717", amount_kobo: 605_000_00 }],
    confidence: 0.85,
    reason: "Two borrowers trade as Ifeoma Pharmacy; only the Onitsha one owes exactly ₦605,000.",
    findings: [
      { fact: "LN-2026-000717 owes exactly ₦605,000.", bearing: "for" },
      { fact: "Ifeoma Eze also trades as Ifeoma Pharmacy; the amount doesn't fit her loan.", bearing: "against" },
    ],
    candidates: ["LN-2026-000717", "LN-2026-000199"],
  },
  checks: [
    { check: "confidence", ok: true, detail: "0.85 against a bar of 0.70" },
    { check: "adds_up", ok: true, detail: "allocations total 60500000 kobo; the payment is 60500000 kobo" },
  ],
  proposal: { id: 229, status: "approved", decisions: [{ staff_id: "tunde", name: "Tunde Bakare", decision: "approve", reason: null, at: "2026-10-12T10:00:00Z" }] },
  steps: [
    { tool: "search_borrowers", input: { text: "IFEOMA PHARMACY" }, output: '{"borrowers": ["CUS-000485", "CUS-001997"]}', error: false, ms: 62 },
    { tool: "find_payments", input: { text: "IFEOMA PHARMACY" }, output: '{"payments": []}', error: false, ms: 62 },
  ],
  usage: { "claude-sonnet-5-5": { input: 6, output: 799, cache_read: 3578, cache_write: 6791 } },
  cost_usd: 0.0258,
  error: null,
  trace_id: null,
  line: { id: "bank_mt940:CST9758245850", amount_kobo: 605_000_00, narration: "TRF FROM IFEOMA PHARMACY/", channel: "bank_mt940", value_date: "2026-10-02", payer_name: "IFEOMA PHARMACY", status: "approved" },
};

const ROWS = [
  { id: 2, started_at: "2026-10-12T09:20:00Z", line_id: "bank_csv:NGT955120802300", amount_kobo: 422_500_00, narration: "NIP/OPAY/MR SANI ELECTRICALS/", channel: "bank_csv", outcome: "suspense", confidence: 0.3, reason: "Two Sani Lawans; the amount fits neither.", tool_calls: 4, cost_usd: 0.0287, model: "claude-sonnet-5-5", proposal_status: null, error: null },
  { id: 1, started_at: RUN.started_at, line_id: RUN.line.id, amount_kobo: RUN.line.amount_kobo, narration: RUN.line.narration, channel: "bank_mt940", outcome: "proposed", confidence: 0.85, reason: RUN.verdict!.reason, tool_calls: 2, cost_usd: 0.0258, model: "claude-sonnet-5-5", proposal_status: "approved", error: null },
];

let calls: URL[];

beforeEach(() => {
  setToken(null);
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = new URL(input, "http://app.test");
      calls.push(url);
      const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      const p = url.pathname;
      if (p === "/api/config") return json(200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "", odoo: "", jaeger: "" } });
      if (p === "/api/auth/sign-in") return json(200, { token: "t", staff: STAFF, expires_in: 1 });
      if (p === "/api/summary") return json(200, { lines: { new: 0, proposed: 0, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, pending_proposals: 0, failed_postings: 0 });
      if (p === "/api/runs") {
        const outcome = url.searchParams.get("outcome");
        const items = outcome ? ROWS.filter((r) => r.outcome === outcome) : ROWS;
        return json(200, { items, total: items.length, counts: { proposed: 1, excluded: 0, suspense: 1, failed: 0 }, all_runs: 2, cost_usd: 0.0545, limit: 50, offset: 0 });
      }
      if (p === "/api/runs/1") return json(200, RUN);
      return json(404, { detail: "Not Found" });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

async function signIn(path: string) {
  window.history.pushState({}, "", path);
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

test("the runs list each investigation newest first, and filters by outcome", async () => {
  await signIn("/runs");
  expect(await screen.findByText("NIP/OPAY/MR SANI ELECTRICALS/")).toBeInTheDocument();
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("held in suspense")).toBeInTheDocument();
  expect(within(rows[1]).getByText("85%")).toBeInTheDocument();
  expect(screen.getByText(/\$0\.05\d at list prices/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^held in suspense/ }));
  expect(await screen.findByText("NIP/OPAY/MR SANI ELECTRICALS/")).toBeInTheDocument();
  expect(calls.some((u) => u.pathname === "/api/runs" && u.searchParams.get("outcome") === "suspense")).toBe(true);
});

test("a run shows what it looked up, its answer, the checks and what a person decided", async () => {
  await signIn("/runs/1");
  expect(await screen.findByText(/Two borrowers trade as Ifeoma Pharmacy/)).toBeInTheDocument();
  expect(screen.getByText(/1 · search_borrowers\(text: "IFEOMA PHARMACY"\)/)).toBeInTheDocument();
  expect(screen.getByText(/names a business: IFEOMA PHARMACY/)).toBeInTheDocument();
  expect(screen.getByText("LN-2026-000717 owes exactly ₦605,000.")).toBeInTheDocument();
  expect(screen.getByLabelText("against")).toBeInTheDocument();
  expect(screen.getByText("0.85 against a bar of 0.70")).toBeInTheDocument();
  expect(screen.getByText("2 of 2")).toBeInTheDocument(); // checks passed
  expect(screen.getByText(/Approved by Tunde Bakare/)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Review the proposal" })).not.toBeInTheDocument(); // already decided
  fireEvent.click(screen.getAllByText("What came back")[0]);
  expect(screen.getByText('{"borrowers": ["CUS-000485", "CUS-001997"]}')).toBeVisible();
});
