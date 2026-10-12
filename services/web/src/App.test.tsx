import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "./App";
import { setToken } from "./api";

// A stand-in for the API: answers by path, records what was asked.
type Handler = (url: URL, init?: RequestInit) => [number, unknown];
let routes: Record<string, Handler>;
let calls: string[];

const LINE = {
  id: "virtual_account:evt_5c1e09ab77f2d410#2",
  channel: "virtual_account",
  value_date: "2026-10-07",
  amount_kobo: 60_500_00,
  narration: "Transfer from ZAINAB SHEHU (redelivered)",
  payer_name: "ZAINAB SHEHU",
  status: "duplicate",
  duplicate_of: "virtual_account:evt_5c1e09ab77f2d410",
  proposal_kind: null,
  proposed_by: null,
  loan_refs: [],
};
const COUNTS = { new: 0, proposed: 0, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 1 };

const STOPPED = {
  outbox_id: 41, line_id: "bank_csv:NGT2610060007", channel: "bank_csv", narration: "NIP/GTB/ZAINAB SHEHU/LN-2026-001209",
  amount_kobo: 60_500_00, idempotency_key: "RD-P41", status: "failed", attempts: 1,
  last_error: "Fineract 403: Loan is not active.", next_attempt_at: null, loan_refs: ["LN-2026-001209"],
  posted_in_core: 0, refused: false, last_attempt_at: "2026-10-10T08:00:00Z",
};
const TODAY = {
  as_of: "2026-10-10",
  week: { start: "2026-10-04", end: "2026-10-10" },
  received: { lines: 351, amount_kobo: 41_860_000_00, duplicates: 5 },
  by_status: {
    new: { lines: 133, amount_kobo: 0 }, proposed: { lines: 200, amount_kobo: 0 }, approved: { lines: 1, amount_kobo: 0 },
    posted: { lines: 9, amount_kobo: 0 }, suspense: { lines: 0, amount_kobo: 0 }, excluded: { lines: 8, amount_kobo: 0 },
  },
  matched_by_rule: 218,
  channels: [{ channel: "virtual_account", lines: 141, by_rule: 141, amount_kobo: 17_240_000_00 }],
  review: { pending: 200, two_person: 12, amount_kobo: 0, two_person_above_kobo: 500_000_00 },
  unmatched: { lines: 133, amount_kobo: 3_120_000_00, oldest_days: 9, bands: [
    { label: "0 to 2 days", lines: 0, amount_kobo: 0 }, { label: "3 to 7 days", lines: 100, amount_kobo: 0 },
    { label: "Over 7 days", lines: 33, amount_kobo: 0 }] },
  postings: { posted_today: 4, waiting: 0, retrying: 1, failed: 2, stopped: [
    STOPPED,
    { ...STOPPED, outbox_id: 42, idempotency_key: "RD-P42", line_id: "bank_csv:X2", refused: true, last_error: "refused: approved_by names someone who didn't decide" },
    { ...STOPPED, outbox_id: 43, idempotency_key: "RD-P43", line_id: "bank_csv:X3", status: "pending", next_attempt_at: "2026-10-10T08:10:00Z", last_error: "Odoo didn't answer" },
  ] },
};

beforeEach(() => {
  setToken(null);
  window.history.pushState({}, "", "/");
  calls = [];
  routes = {
    "/api/config": () => [200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "", odoo: "", jaeger: "http://j" } }],
    "/api/auth/sign-in": (_u, init) => {
      const body = JSON.parse(String(init?.body));
      return body.password === "right"
        ? [200, { token: "t1", staff: { id: body.staff_id, name: "Amaka Nwosu", role: "officer", limit_kobo: 50_000_000 }, expires_in: 1 }]
        : [401, { detail: "Wrong name or password." }];
    },
    "/api/summary": () => [200, { lines: COUNTS, pending_proposals: 3, failed_postings: 2 }],
    "/api/today": () => [200, TODAY],
    "/api/lines": () => [200, { items: [LINE], total: 1, counts: COUNTS, limit: 50, offset: 0 }],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://app.test");
      calls.push(`${init?.method ?? "GET"} ${url.pathname}${url.search}`);
      const handler = routes[url.pathname] ?? (() => [404, { detail: "Not Found" }]);
      const [status, body] = handler(url, init);
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

async function signIn(password = "right") {
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

test("a wrong password is refused without leaving the sign-in screen", async () => {
  await signIn("wrong");
  expect(await screen.findByRole("alert")).toHaveTextContent("Wrong name or password.");
});

test("signing in lands on Today: the week, and the postings that stopped", async () => {
  await signIn();
  expect(await screen.findByRole("heading", { name: "Saturday 10 October" })).toBeInTheDocument();
  expect(screen.getByText(/Statement week 4 Oct to 10 Oct/)).toBeInTheDocument();
  expect(screen.getByText("62%")).toBeInTheDocument(); // 218 of 351 by rule
  expect(screen.getByText("12 need two people (over ₦500,000)")).toBeInTheDocument();
  expect(screen.getByText("Fineract 403: Loan is not active.")).toBeInTheDocument();
  expect(screen.getByText("Needs a new proposal and decision")).toBeInTheDocument(); // refused: no retry
  expect(screen.getByText("The gateway retries on its own")).toBeInTheDocument();
  expect(screen.getByTitle("Postings that stopped")).toHaveTextContent("2");

  routes["/api/postings/41/retry"] = () => [200, { outbox_id: 41, status: "retrying" }];
  fireEvent.click(screen.getByRole("button", { name: "Retry posting" }));
  await waitFor(() => expect(calls).toContain("POST /api/postings/41/retry"));
  expect(await screen.findByText(/Sent back to the posting gateway/)).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c === "GET /api/today").length).toBeGreaterThan(1)); // reloaded
});

test("an auditor sees a stopped posting but can't send it back", async () => {
  routes["/api/auth/sign-in"] = () => [200, { token: "t2", staff: { id: "hadiza", name: "Hadiza Lawal", role: "auditor", limit_kobo: 0 }, expires_in: 1 }];
  await signIn();
  expect(await screen.findByText("Fineract 403: Loan is not active.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry posting" })).not.toBeInTheDocument();
  expect(screen.getAllByText("Read only").length).toBeGreaterThan(0);
});

test("a payments link survives sign-in, with the token on every request", async () => {
  window.history.pushState({}, "", "/payments");
  await signIn();
  expect(await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)")).toBeInTheDocument();
  expect(screen.getByText("Amaka Nwosu")).toBeInTheDocument();
  expect(screen.getByText("₦60,500")).toBeInTheDocument();
  const fetchMock = vi.mocked(fetch);
  const linesCall = fetchMock.mock.calls.find(([u]) => String(u).startsWith("/api/lines"));
  expect((linesCall?.[1]?.headers as Record<string, string>).Authorization).toBe("Bearer t1");
});

test("a line id with # in it is asked for encoded, not cut off at the #", async () => {
  routes["/api/lines/" + encodeURIComponent(LINE.id)] = () => [404, { detail: "No such payment line." }];
  window.history.pushState({}, "", "/payments");
  await signIn();
  fireEvent.click(await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)"));
  await waitFor(() => expect(calls).toContain("GET /api/lines/virtual_account%3Aevt_5c1e09ab77f2d410%232"));
});

test("a posted line's case file shows what each book called it; a failed one says it stopped", async () => {
  const id = "bank_csv:NGT2610060001";
  const posting = {
    outbox_id: 7,
    idempotency_key: "RD-P110",
    status: "posted",
    attempts: 2,
    last_error: null,
    posted_at: "2026-10-09T09:15:00Z",
    next_attempt_at: null,
    core_transactions: [
      { loan_ref: "LN-2026-001209", external_id: "RD-P110", transaction_id: 4301, principal_kobo: 50_000_00, interest_kobo: 10_500_00, charges_kobo: 0, overpayment_kobo: 0 },
    ],
    ledger_entry: "RPY/2026/00012",
  };
  const line = {
    id, channel: "bank_csv", source_account: "1023456789", value_date: "2026-10-06", amount_kobo: 60_500_00,
    narration: "TRF ZAINAB SHEHU LN-2026-001209", bank_reference: "NGT2610060001", payer_name: "ZAINAB SHEHU",
    payer_account: null, virtual_account: null, terminal_id: null, customer_reference: null, status: "posted",
    duplicate_of: null, received_at: "2026-10-07T08:00:00Z", duplicates: [],
    arrived_by: { kind: "file", file_id: 1, source: "bank_csv", file_name: "northgate.csv", received_at: "2026-10-07T08:00:00Z" },
    proposals: [{ id: 110, kind: "match", proposed_by: "rule:loan_ref_in_narration", confidence: 1,
      allocations: [{ loan_ref: "LN-2026-001209", amount_kobo: 60_500_00 }], evidence: {}, status: "approved",
      proposed_at: "2026-10-07T08:01:00Z", decisions: [] }],
    posting,
    trail: [{ id: 9, at: "2026-10-09T09:15:00Z", actor: "service:gateway", actor_name: null, action: "posting.posted", detail: {}, trace_id: null }],
  };
  routes["/api/lines/" + encodeURIComponent(id)] = () => [200, line];
  window.history.pushState({}, "", `/payments/${encodeURIComponent(id)}`);
  await signIn();

  expect(await screen.findByText("RPY/2026/00012")).toBeInTheDocument();
  expect(screen.getByText("Posted to Fineract and the ledger")).toBeInTheDocument();
  expect(screen.getByText("₦50,000")).toBeInTheDocument(); // principal, as Fineract split it
  expect(screen.getByText("₦10,500")).toBeInTheDocument();

  routes["/api/lines/" + encodeURIComponent(id)] = () => [200, {
    ...line, status: "approved",
    posting: { ...posting, status: "failed", posted_at: null, ledger_entry: null, core_transactions: [], last_error: "Fineract 403: Loan is not active." },
  }];
  fireEvent.click(screen.getByRole("link", { name: "Payments" }));
  await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)");
  window.history.back();
  const banner = await screen.findByRole("region", { name: "Posting stopped" }); // coral, at the top: it needs a person
  expect(banner).toHaveTextContent("Fineract 403: Loan is not active.");
  expect(banner).toHaveTextContent("RD-P110");
  expect(screen.getByText("Why it stopped")).toBeInTheDocument();
  expect(screen.getByText(/won't try this again on its own/)).toBeInTheDocument();

  routes["/api/postings/7/retry"] = () => [200, { outbox_id: 7, status: "retrying" }];
  fireEvent.click(screen.getByRole("button", { name: "Retry posting" }));
  await waitFor(() => expect(calls).toContain("POST /api/postings/7/retry"));
});

test("when the API ends the session, the app goes back to sign-in and says why", async () => {
  window.history.pushState({}, "", "/payments");
  await signIn();
  await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)");
  routes["/api/lines"] = () => [401, { detail: "Sign in first." }];
  fireEvent.click(screen.getByRole("button", { name: /^duplicate/ }));
  expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
});
