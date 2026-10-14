import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type CollectionsMessage, type MessagePage, type Staff } from "../api";

const NGOZI: Staff = { id: "ngozi", name: "Ngozi Eze", role: "manager", limit_kobo: 0 };
const AMAKA: Staff = { id: "amaka", name: "Amaka Nwosu", role: "officer", limit_kobo: 500_000_00 };

const BODY =
  "Good morning Zainab, this is Demo Microfinance Bank. Your loan LN-2026-001209 has ₦60,500 due. Please pay into your loan's virtual account or at any branch with the loan reference. Reply here if you'd like to talk.";

const DRAFT: CollectionsMessage = {
  id: 41,
  loan_ref: "LN-2026-001209",
  borrower_ref: "CUS-000950",
  borrower_name: "Zainab Shehu",
  branch_name: "Abuja Wuse",
  bucket: "follow_up",
  bucket_label: "Follow-up",
  channel: "sms",
  body: BODY,
  amount_due_kobo: 60_500_00,
  checks: [{ check: "no_threats", ok: true, detail: "no threats" }],
  passes_policy: true,
  drafted_by: "agent:collector",
  run_id: 12,
  note: "Twelve days late, so firm and polite.",
  created_at: "2026-10-12T07:00:00Z",
  status: "draft",
  decided_by: null,
  decided_by_name: null,
  decided_at: null,
  reject_reason: null,
  send_after: null,
  sent_at: null,
  provider_id: null,
  error: null,
  attempts: 0,
  drafted_body: null,
  days_late: 12,
};

const FAILING: CollectionsMessage = {
  ...DRAFT,
  id: 42,
  loan_ref: "LN-2026-000199",
  borrower_name: "Ifeoma Eze",
  checks: [{ check: "amount_due", ok: false, detail: "₦58,080.00 due; the message says ₦1.00." }],
  passes_policy: false,
};

const SENT: CollectionsMessage = {
  ...DRAFT,
  id: 40,
  status: "sent",
  decided_by: "ngozi",
  decided_by_name: "Ngozi Eze",
  decided_at: "2026-10-12T08:05:00Z",
  send_after: "2026-10-12T08:05:00Z",
  sent_at: "2026-10-12T08:05:03Z",
  provider_id: "SIM-000007",
  drafted_body: "Good morning Zainab, the original words.",
};

const page = (items: CollectionsMessage[]): MessagePage => ({
  items,
  total: items.length,
  counts: { draft: 2, approved: 0, sent: 1, failed: 0, rejected: 0 },
  limit: 20,
  offset: 0,
});

let who: Staff;
let posts: { url: string; body: unknown }[];
let approveAnswer: () => Response;

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  setToken(null);
  who = NGOZI;
  posts = [];
  approveAnswer = () => json(200, { message_id: 41, status: "approved", edited: true, send_after: "2026-10-13T07:00:00Z" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://app.test");
      const p = url.pathname;
      if (init?.method === "POST" && p !== "/api/auth/sign-in") posts.push({ url: p, body: JSON.parse(String(init.body ?? "{}")) });
      if (p === "/api/config") return json(200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "", odoo: "", jaeger: "" } });
      if (p === "/api/auth/sign-in") return json(200, { token: "t", staff: who, expires_in: 1 });
      if (p === "/api/summary") return json(200, { lines: { new: 0, proposed: 0, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, pending_proposals: 0, failed_postings: 0, drafts_waiting: 2 });
      if (p === "/api/collections/messages") return json(200, page(url.searchParams.get("status") === "sent" ? [SENT] : [DRAFT, FAILING]));
      if (p === "/api/collections/messages/41/approve") return approveAnswer();
      if (p === "/api/collections/messages/41/reject") return json(200, { message_id: 41, status: "rejected" });
      return json(404, { detail: "Not Found" });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

async function signIn() {
  window.history.pushState({}, "", "/messages");
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

test("a manager edits a draft, the policy refuses it, and the fixed one is approved", async () => {
  await signIn();
  const card = await screen.findByRole("article", { name: "Message to Zainab Shehu" });
  expect(within(card).getByText("Passes all 1 of the policy's checks.")).toBeInTheDocument();
  expect(within(card).getByText(/From the agent: Twelve days late/)).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "How it was drafted (run 12)" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Messages/ })).toHaveTextContent("2"); // drafts waiting, in the nav

  const box = within(card).getByLabelText("The message");
  fireEvent.change(box, { target: { value: `${BODY} Or the police will come.` } });
  expect(within(card).getByText(/characters · edited/)).toBeInTheDocument();
  approveAnswer = () => json(422, { refused: "fails_policy", message: "The message breaks the collections policy: mentions the police." });
  fireEvent.click(within(card).getByRole("button", { name: "Approve as edited" }));
  expect(await within(card).findByRole("alert")).toHaveTextContent("mentions the police");
  expect(posts.at(-1)).toEqual({ url: "/api/collections/messages/41/approve", body: { body: `${BODY} Or the police will come.` } });

  fireEvent.change(box, { target: { value: BODY.replace("Reply here", "Reply here, or call your branch,") } });
  approveAnswer = () => json(200, { message_id: 41, status: "approved", edited: true, send_after: "2026-10-13T07:00:00Z" });
  fireEvent.click(within(card).getByRole("button", { name: "Approve as edited" }));
  expect(await screen.findByText(/Approved as edited\. It goes to Zainab Shehu from 13 Oct/)).toBeInTheDocument();
});

test("a draft that failed a check shows why and can't be approved as it stands", async () => {
  await signIn();
  const card = await screen.findByRole("article", { name: "Message to Ifeoma Eze" });
  expect(within(card).getByText("₦58,080.00 due; the message says ₦1.00.")).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Approve" })).toBeDisabled();
  expect(within(card).getByText("Fix the failed checks before approving.")).toBeInTheDocument();
});

test("a rejection sends the reason", async () => {
  await signIn();
  const card = await screen.findByRole("article", { name: "Message to Zainab Shehu" });
  fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
  fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
  expect(await screen.findByText("Rejected. The loan goes back on the morning list.")).toBeInTheDocument();
  expect(posts.at(-1)).toEqual({ url: "/api/collections/messages/41/reject", body: { reason: "Tone is wrong for this borrower" } });
});

test("an officer reads drafts but can't decide, and sent messages show what went out", async () => {
  who = AMAKA;
  await signIn();
  const card = await screen.findByRole("article", { name: "Message to Zainab Shehu" });
  expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  expect(within(card).getByText("Read only")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^Sent/ }));
  const sent = await screen.findByText(/provider id/);
  expect(sent).toHaveTextContent("SIM-000007");
  expect(screen.getByText("Edited before approval: what the agent wrote")).toBeInTheDocument();
  expect(screen.getByText(/by Ngozi Eze/)).toBeInTheDocument();
});
