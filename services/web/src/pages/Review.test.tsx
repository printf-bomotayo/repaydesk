import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type QueueItem, type Staff } from "../api";

// A stand-in for the API that keeps a little queue and applies decisions to it, as the real one does.
const OFFICER: Staff = { id: "amaka", name: "Amaka Nwosu", role: "officer", limit_kobo: 50_000_000 };
const AUDITOR: Staff = { id: "hadiza", name: "Hadiza Lawal", role: "auditor", limit_kobo: 0 };

function item(id: number, amount: number, extra: Partial<QueueItem> = {}): QueueItem {
  return {
    proposal_id: id,
    kind: "match",
    proposed_by: "rule:loan_reference",
    confidence: 1,
    allocations: [{ loan_ref: `LN-2026-00${1000 + id}`, amount_kobo: amount }],
    evidence: { quoted_reference: `LN-2026-00${1000 + id}`, shared_names: ["SHEHU"] },
    proposed_at: "2026-10-08T10:00:00Z",
    line: {
      id: `bank_csv:T${id}`,
      channel: "bank_csv",
      value_date: "2026-10-07",
      amount_kobo: amount,
      narration: `NIP/GTB/PAYER ${id}/RPY`,
      payer_name: `PAYER ${id}`,
      customer_reference: null,
    },
    approvals: [],
    approvals_needed: amount > 50_000_000 ? 2 : 1,
    you: { can_approve: true, settles: amount <= 50_000_000, refused: null, message: null },
    ...extra,
  };
}

let queue: QueueItem[];
let posts: { path: string; body: unknown }[];
let staff: Staff;
let conflictOn: number | null;

beforeEach(() => {
  setToken(null);
  window.history.pushState({}, "", "/review");
  posts = [];
  staff = OFFICER;
  conflictOn = null;
  queue = [
    item(1, 875_000_00, { kind: "match" }),
    item(2, 126_000_00),
    item(3, 2_500_000_00, {
      approvals: [{ staff_id: "kunle", name: "Kunle Adebayo", at: "2026-10-09T08:00:00Z" }],
      you: {
        can_approve: false,
        settles: false,
        refused: "over_limit",
        message: "₦2,500,000 is above your limit and the first approver's. Someone with a higher limit approves it.",
      },
    }),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://app.test");
      const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      const p = url.pathname;
      if (p === "/api/config") return json(200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "", odoo: "", jaeger: "" } });
      if (p === "/api/auth/sign-in") return json(200, { token: "t", staff, expires_in: 1 });
      if (p === "/api/summary") return json(200, { lines: { new: 0, proposed: queue.length, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, pending_proposals: queue.length });
      if (p === "/api/queue") {
        const sorted = [...queue].sort((a, b) => b.line.amount_kobo - a.line.amount_kobo); // as the API orders it
        const items = sorted.map((q) => (staff.role === "auditor" ? { ...q, you: { can_approve: false, settles: false, refused: "read_only", message: "Auditors can see the queue but not decide." } } : q));
        return json(200, { items, counts: { all: items.length, two_person: items.filter((i) => i.approvals_needed === 2).length, exclusions: 0 }, total: items.length, limit: 25, offset: 0 });
      }
      const m = p.match(/^\/api\/proposals\/(\d+)\/(approve|reject)$/);
      if (m) {
        const id = Number(m[1]);
        posts.push({ path: p, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (conflictOn === id) {
          queue = queue.filter((q) => q.proposal_id !== id);
          return json(409, { refused: "not_pending", message: "This proposal is already approved." });
        }
        const q = queue.find((x) => x.proposal_id === id)!;
        if (m[2] === "approve" && q.approvals_needed === 2 && q.approvals.length === 0) {
          q.approvals = [{ staff_id: staff.id, name: staff.name, at: "2026-10-09T09:00:00Z" }];
          q.you = { can_approve: false, settles: false, refused: "same_person", message: "You've approved this already. Someone else gives the second approval." };
          return json(200, { proposal_id: id, status: "first_approval", outbox_id: null });
        }
        queue = queue.filter((x) => x.proposal_id !== id);
        return json(200, m[2] === "approve" ? { proposal_id: id, status: "approved", outbox_id: 9 } : { proposal_id: id, status: "rejected" });
      }
      return json(404, { detail: "Not Found" });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

async function openQueue() {
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("heading", { name: "Review queue" });
  return await screen.findAllByRole("article");
}

const card = (amount: string) => screen.getByRole("article", { name: new RegExp(`^${amount} `) });

test("largest first, each with its proposal and what the rule checked", async () => {
  const cards = await openQueue();
  expect(cards.map((c) => within(c).getByText(/^₦/, { selector: ".big" }).textContent)).toEqual([
    "₦2,500,000",
    "₦875,000",
    "₦126,000",
  ]);
  expect(within(card("₦126,000")).getByText("LN-2026-001002")).toBeInTheDocument();
  expect(within(card("₦126,000")).getByText("The payer's name shares SHEHU with the borrower")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Needs two people 2/ })).toBeInTheDocument();
});

test("an officer can't settle what is above every approver's limit, and is told why", async () => {
  await openQueue();
  const big = card("₦2,500,000");
  expect(within(big).getByText("1 of 2 approvals: Kunle Adebayo")).toBeInTheDocument();
  expect(within(big).getByRole("button", { name: "Approve" })).toBeDisabled();
  expect(within(big).getByText(/above your limit and the first approver's/)).toBeInTheDocument();
});

test("a large payment takes a first approval, then waits for someone else", async () => {
  await openQueue();
  fireEvent.click(within(card("₦875,000")).getByRole("button", { name: "Approve (first of two)" }));
  expect(await screen.findByText(/First approval recorded for ₦875,000/)).toBeInTheDocument();
  await waitFor(() => expect(within(card("₦875,000")).getByText("1 of 2 approvals: Amaka Nwosu")).toBeInTheDocument());
  expect(within(card("₦875,000")).getByRole("button", { name: "Approve" })).toBeDisabled();
  expect(within(card("₦875,000")).getByRole("button", { name: "Reject" })).toBeDisabled(); // the API would refuse it
});

test("approving a small payment takes it off the queue and queues it for posting", async () => {
  await openQueue();
  fireEvent.click(within(card("₦126,000")).getByRole("button", { name: "Approve" }));
  expect(await screen.findByText("Approved ₦126,000. It's queued for posting to Fineract and Odoo.")).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
  expect(posts).toEqual([{ path: "/api/proposals/2/approve", body: null }]);
});

test("a rejection carries a reason, written in when it's not on the list", async () => {
  await openQueue();
  const c = card("₦126,000");
  fireEvent.click(within(c).getByRole("button", { name: "Reject" }));
  fireEvent.change(within(c).getByLabelText("Reason"), { target: { value: "Something else" } });
  const submit = within(c).getByRole("button", { name: "Reject" });
  expect(submit).toBeDisabled(); // no reason yet
  fireEvent.change(within(c).getByLabelText("Your reason"), { target: { value: "Payer is the borrower's landlord" } });
  fireEvent.click(submit);
  expect(await screen.findByText(/Rejected: payer is the borrower's landlord/)).toBeInTheDocument();
  expect(posts).toEqual([{ path: "/api/proposals/2/reject", body: { reason: "Payer is the borrower's landlord" } }]);
});

test("when someone else decided first, the reviewer is told and the queue refreshes", async () => {
  conflictOn = 2;
  await openQueue();
  fireEvent.click(within(card("₦126,000")).getByRole("button", { name: "Approve" }));
  expect(await screen.findByText(/Someone decided this one while you were looking/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
});

test("an auditor sees the queue and can't decide", async () => {
  staff = AUDITOR;
  const cards = await openQueue();
  expect(cards).toHaveLength(3);
  expect(screen.queryByRole("button", { name: /Approve|Reject/ })).toBeNull();
  expect(screen.getAllByText("Read only")).toHaveLength(3);
});
