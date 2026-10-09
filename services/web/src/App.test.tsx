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
    "/api/summary": () => [200, { lines: COUNTS, pending_proposals: 3 }],
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

test("signing in lands on payments, with the token on every request", async () => {
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
  expect(await screen.findByText("Fineract 403: Loan is not active.")).toBeInTheDocument();
  expect(screen.getByText("Why it stopped")).toBeInTheDocument();
  expect(screen.getByText(/won't try this again on its own/)).toBeInTheDocument();
});

test("when the API ends the session, the app goes back to sign-in and says why", async () => {
  await signIn();
  await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)");
  routes["/api/lines"] = () => [401, { detail: "Sign in first." }];
  fireEvent.click(screen.getByRole("button", { name: /^duplicate/ }));
  expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
});
