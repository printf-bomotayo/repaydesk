import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type CollectionsMorning, type Staff } from "../api";

const STAFF: Staff = { id: "ngozi", name: "Ngozi Eze", role: "manager", limit_kobo: 0 };

const MORNING: CollectionsMorning = {
  as_of: "2026-10-12",
  directory_copied_at: "2026-10-12T08:00:00Z",
  buckets: [
    { bucket: "reminder", label: "Reminder", days: "1 to 7", loans: 355, arrears_kobo: 21_000_000_00 },
    { bucket: "follow_up", label: "Follow-up", days: "8 to 30", loans: 171, arrears_kobo: 18_000_000_00 },
    { bucket: "arrangement", label: "Arrangement", days: "31 to 90", loans: 90, arrears_kobo: 12_000_000_00 },
    { bucket: "recovery", label: "Recovery", days: "over 90", loans: 30, arrears_kobo: 9_000_000_00 },
  ],
  due: {
    total: 2,
    items: [
      { loan_ref: "LN-2026-000199", borrower_ref: "CUS-001997", borrower_name: "Ifeoma Eze", branch_name: "Ikeja", days_late: 5, arrears_kobo: 58_080_00, outstanding_kobo: 116_160_00, bucket: "broken_promise", bucket_label: "Promise broken", last_sent_at: "2026-10-09T10:00:00Z", promise: { status: "broken", due_on: "2026-10-10", amount_kobo: 58_080_00 } },
      { loan_ref: "LN-2026-001209", borrower_ref: "CUS-000950", borrower_name: "Zainab Shehu", branch_name: "Abuja Wuse", days_late: 12, arrears_kobo: 60_500_00, outstanding_kobo: 242_000_00, bucket: "follow_up", bucket_label: "Follow-up", last_sent_at: null, promise: null },
    ],
    limit: 50,
    offset: 0,
  },
  drafts_waiting: 0,
  promises: { open: 3, kept: 1, broken: 1, cancelled: 0 },
};

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
      if (p === "/api/collections") return json(200, MORNING);
      return json(404, { detail: "Not Found" });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

test("the collections morning shows the buckets, the broken promise first, and filters by kind", async () => {
  window.history.pushState({}, "", "/collections");
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText("646 loans are late.", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Handled by the recovery team: no drafted messages.")).toBeInTheDocument();
  expect(screen.getByText("Portfolio manager")).toBeInTheDocument(); // the account panel names the role
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("Promise broken")).toBeInTheDocument();
  expect(within(rows[0]).getByText(/broken · ₦58,080 by 10 Oct/)).toBeInTheDocument();
  expect(within(rows[1]).getByText("none yet")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Follow-up" }));
  await vi.waitFor(() =>
    expect(calls.some((u) => u.pathname === "/api/collections" && u.searchParams.get("bucket") === "follow_up")).toBe(true),
  );
});
