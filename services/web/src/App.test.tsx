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

test("when the API ends the session, the app goes back to sign-in and says why", async () => {
  await signIn();
  await screen.findByText("Transfer from ZAINAB SHEHU (redelivered)");
  routes["/api/lines"] = () => [401, { detail: "Sign in first." }];
  fireEvent.click(screen.getByRole("button", { name: /^duplicate/ }));
  expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
});
