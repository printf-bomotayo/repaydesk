import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type BorrowerRecord, type LoanRecord, type Staff } from "../api";

// A stand-in for the API with one borrower and one loan: three instalments, the third late, and a
// payment for it that Repaydesk holds but hasn't posted.
const STAFF: Staff = { id: "hadiza", name: "Hadiza Lawal", role: "auditor", limit_kobo: 0 };
const DIRECTORY = { synced_at: new Date(Date.now() - 4 * 60_000).toISOString(), borrowers: 1, loans: 1, failed_since: null };

const LOAN: LoanRecord = {
  ref: "LN-2026-001209",
  core_id: "1209",
  borrower_ref: "CUS-000950",
  borrower_name: "Zainab Shehu",
  borrower_core_id: "949",
  product: "SME",
  product_name: "SME working capital, 6 months",
  principal_kobo: 300_000_00,
  disbursed_on: "2026-07-06",
  status: "active",
  outstanding_kobo: 242_000_00,
  arrears_kobo: 60_500_00,
  days_late: 3,
  copied_at: "2026-10-09T08:35:09Z",
  virtual_account: { number: "9991110041", provider: "payrail" },
  live: true,
  schedule: [
    { number: 1, due_on: "2026-08-06", due_kobo: 60_500_00, paid_kobo: 60_500_00 },
    { number: 2, due_on: "2026-09-06", due_kobo: 60_500_00, paid_kobo: 60_500_00 },
    { number: 3, due_on: "2020-10-06", due_kobo: 60_500_00, paid_kobo: 0 },
    { number: 4, due_on: "2099-11-06", due_kobo: 60_500_00, paid_kobo: 0 },
  ],
  held_kobo: 60_500_00,
  money_in: [
    { on: "2026-08-03", loan_ref: "LN-2026-001209", amount_kobo: 60_500_00, channel: "branch_cash", reference: "HIST-0001789", status: "posted", line_id: null },
    { on: "2026-10-06", loan_ref: "LN-2026-001209", amount_kobo: 60_500_00, channel: "virtual_account", reference: "evt_eb47", status: "proposed", line_id: "virtual_account:evt_eb47" },
  ],
  directory: DIRECTORY,
};

const BORROWER: BorrowerRecord = {
  ref: "CUS-000950",
  core_id: "949",
  name: "Zainab Shehu",
  phone: "07005566957",
  branch_code: "WSE",
  branch_name: "Abuja Wuse",
  business_name: null,
  group_ref: null,
  group_name: null,
  joined_on: "2023-01-12",
  active: true,
  copied_at: "2026-10-09T08:35:09Z",
  loans: [{ ref: LOAN.ref, core_id: "1209", product_name: LOAN.product_name, principal_kobo: LOAN.principal_kobo, disbursed_on: LOAN.disbursed_on, status: "active", outstanding_kobo: LOAN.outstanding_kobo, arrears_kobo: LOAN.arrears_kobo, days_late: 3 }],
  live: true,
  payments: [...LOAN.money_in].reverse(),
  directory: DIRECTORY,
};

let calls: URL[];
let live: boolean;

beforeEach(() => {
  setToken(null);
  calls = [];
  live = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = new URL(input, "http://app.test");
      calls.push(url);
      const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      const p = url.pathname;
      if (p === "/api/config") return json(200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "http://fineract.test", odoo: "", jaeger: "" } });
      if (p === "/api/auth/sign-in") return json(200, { token: "t", staff: STAFF, expires_in: 1 });
      if (p === "/api/summary") return json(200, { lines: { new: 0, proposed: 1, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, pending_proposals: 1 });
      if (p === "/api/lines") return json(200, { items: [], total: 0, counts: { new: 0, proposed: 0, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, limit: 50, offset: 0 });
      if (p === "/api/find") {
        const q = url.searchParams.get("q") ?? "";
        return json(200, q.toUpperCase().includes("1209") ? { to: "loan", ref: LOAN.ref } : q.startsWith("0700") ? { to: "borrower", ref: BORROWER.ref } : { to: "payments", q });
      }
      if (p === `/api/loans/${LOAN.ref}`) return json(200, live ? LOAN : { ...LOAN, live: false, schedule: null, money_in: LOAN.money_in.slice(1) });
      if (p === `/api/borrowers/${BORROWER.ref}`) return json(200, live ? BORROWER : { ...BORROWER, live: false, payments: BORROWER.payments.slice(0, 1) });
      if (p === "/api/loans") {
        const row = { ref: LOAN.ref, borrower_ref: BORROWER.ref, borrower_name: "Zainab Shehu", product_name: LOAN.product_name, status: "active", outstanding_kobo: LOAN.outstanding_kobo, arrears_kobo: LOAN.arrears_kobo, days_late: 3, held_kobo: LOAN.held_kobo };
        return json(200, { items: [row], counts: { all: 2130, late_1_7: 355, late_8_30: 171, late_31_90: 90, late_90: 30, closed: 207, current: 1277, in_arrears: 646 }, total: 1, limit: 50, offset: 0, directory: DIRECTORY });
      }
      if (p === "/api/borrowers") {
        const row = { ref: BORROWER.ref, name: "Zainab Shehu", branch_name: "Abuja Wuse", business_name: null, phone: BORROWER.phone, loan_refs: [LOAN.ref], arrears_kobo: LOAN.arrears_kobo, days_late: 3, held: { lines: 1, amount_kobo: LOAN.held_kobo } };
        return json(200, { items: [row], counts: { all: 2024, arrears: 635, held: 209 }, total: 1, limit: 50, offset: 0, directory: DIRECTORY });
      }
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

async function search(text: string) {
  const box = await screen.findByRole("searchbox", { name: "Search" });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.submit(box.closest("form")!);
}

test("a loan reference in the search box opens the loan, with what holding a payment would leave", async () => {
  await signIn("/payments");
  await search("ln 2026 001209");
  await screen.findByRole("heading", { name: LOAN.ref });

  expect(screen.getByText("₦60,500 in arrears · 3 days")).toBeInTheDocument();
  const ifPosted = screen.getByText("If held payments post").closest(".stat") as HTMLElement;
  expect(within(ifPosted).getByText("₦181,500")).toBeInTheDocument();
  expect(within(ifPosted).getByText("₦60,500 held by Repaydesk, not yet posted")).toBeInTheDocument();

  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(rows.map((r) => r.lastElementChild?.textContent)).toEqual(["paid", "paid", "late · payment held", ""]);
  expect(screen.getByRole("link", { name: /Fineract's staff screens/ })).toHaveAttribute(
    "href",
    "http://fineract.test/#/clients/949/loans-accounts/1209/general",
  );
  // The held payment opens its line; the posted one is core banking's reference only.
  expect(screen.getByRole("link", { name: "evt_eb47" })).toHaveAttribute("href", "/payments/virtual_account%3Aevt_eb47");
  expect(screen.queryByRole("link", { name: "HIST-0001789" })).toBeNull();
});

test("a phone opens the borrower; anything else still searches payments", async () => {
  await signIn("/payments");
  await search("0700 556 6957");
  await screen.findByRole("heading", { name: "Zainab Shehu" });
  expect(screen.getByText("0700 556 6957")).toBeInTheDocument();
  expect(screen.getByText("Payments received, every channel")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: LOAN.ref }).length).toBeGreaterThan(0);

  await search("SHEHU");
  await screen.findByRole("heading", { name: "Payments" });
  expect(window.location.search).toBe("?q=SHEHU");
});

test("when Fineract doesn't answer, the loan says so and still shows what Repaydesk holds", async () => {
  live = false;
  await signIn(`/loans/${LOAN.ref}`);
  expect(await screen.findByText(/Fineract didn't answer, so this is the directory's copy/)).toBeInTheDocument();
  expect(screen.getByText("the directory's copy")).toBeInTheDocument();
  expect(screen.getByText("Fineract didn't answer; try again shortly.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "evt_eb47" })).toBeInTheDocument();
});

test("loans filter by how late, and borrowers search, through the address", async () => {
  await signIn("/loans");
  expect(await screen.findByText(/2,130 loans · 646 in arrears/)).toBeInTheDocument();
  expect(screen.getByText(/Copied from core banking 4 minutes ago/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "8 to 30 171" }));
  await screen.findByRole("button", { name: "8 to 30 171", pressed: true });
  expect(calls.filter((u) => u.pathname === "/api/loans").at(-1)?.searchParams.get("band")).toBe("late_8_30");

  fireEvent.click(screen.getByRole("link", { name: /^Borrowers/ }));
  const box = await screen.findByRole("searchbox", { name: /Name, trading name/ });
  fireEvent.change(box, { target: { value: "Ifeoma Pharmacy" } });
  fireEvent.submit(box.closest("form")!);
  await screen.findByRole("button", { name: "All 2,024", pressed: true });
  expect(calls.filter((u) => u.pathname === "/api/borrowers").at(-1)?.searchParams.get("q")).toBe("Ifeoma Pharmacy");
  expect(screen.getByText("₦60,500", { selector: ".pill.s-proposed" })).toBeInTheDocument();
});
