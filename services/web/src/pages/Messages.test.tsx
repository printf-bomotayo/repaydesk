import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../App";
import { setToken, type CollectionsMessage, type MessagePage, type Reply, type Staff } from "../api";

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

const PROMISED: Reply = {
  id: 6,
  channel: "sms",
  from_phone: "07005123456",
  body: "I go pay Friday",
  received_at: "2026-10-12T09:00:00Z",
  borrower_ref: "CUS-000950",
  borrower_name: "Zainab Shehu",
  loan_ref: "LN-2026-001209",
  message_id: 40,
  answers: BODY,
  asked_kobo: 60_500_00,
  reading: { kind: "promise", due_on: "2026-10-16", amount_kobo: null, why: "names a date (friday)" },
  outcome: "promise",
  handled_at: "2026-10-12T09:00:01Z",
  handled_by: "rule:replies",
  handled_by_name: null,
  note: null,
  promise: { id: 2, due_on: "2026-10-16", amount_kobo: 60_500_00, status: "open" },
};

const SAYS_PAID: Reply = {
  ...PROMISED,
  id: 7,
  body: "I don pay, abeg check am",
  reading: { kind: "says_paid", due_on: null, amount_kobo: null, why: "says it has paid: a person checks for the payment" },
  outcome: "says_paid",
  handled_at: null,
  handled_by: null,
  promise: null,
};

const replyPage = (items: Reply[]) => ({ items, total: items.length, waiting: 1, limit: 20, offset: 0 });

let who: Staff;
let posts: { url: string; body: unknown }[];
let approveAnswer: () => Response;
let promiseAnswer: () => Response;

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  setToken(null);
  who = NGOZI;
  posts = [];
  approveAnswer = () => json(200, { message_id: 41, status: "approved", edited: true, send_after: "2026-10-13T07:00:00Z" });
  promiseAnswer = () => json(200, { inbound_id: 7, promise_id: 3 });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://app.test");
      const p = url.pathname;
      if (init?.method === "POST" && p !== "/api/auth/sign-in") posts.push({ url: p, body: JSON.parse(String(init.body ?? "{}")) });
      if (p === "/api/config") return json(200, { bank_name: "Demo Microfinance Bank", links: { fineract_web: "", odoo: "", jaeger: "" } });
      if (p === "/api/auth/sign-in") return json(200, { token: "t", staff: who, expires_in: 1 });
      if (p === "/api/summary") return json(200, { lines: { new: 0, proposed: 0, approved: 0, posted: 0, suspense: 0, excluded: 0, duplicate: 0 }, pending_proposals: 0, failed_postings: 0, drafts_waiting: 2, replies_waiting: 0 });
      if (p === "/api/collections/messages") return json(200, page(url.searchParams.get("status") === "sent" ? [SENT] : [DRAFT, FAILING]));
      if (p === "/api/collections/messages/41/approve") return approveAnswer();
      if (p === "/api/collections/messages/41/reject") return json(200, { message_id: 41, status: "rejected" });
      if (p === "/api/collections/replies") return json(200, replyPage(url.searchParams.get("waiting") === "true" ? [SAYS_PAID] : [PROMISED, SAYS_PAID]));
      if (p === "/api/collections/replies/7/promise") return promiseAnswer();
      if (p === "/api/collections/replies/7/close") return json(200, { inbound_id: 7, handled: true });
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

test("replies wait for a person, who records the promise the rules couldn't, or closes them with a note", async () => {
  await signIn();
  fireEvent.click(await screen.findByRole("button", { name: "Replies" }));
  const card = await screen.findByRole("article", { name: "Reply from Zainab Shehu" });
  expect(within(card).getByText('"I don pay, abeg check am"')).toBeInTheDocument();
  expect(within(card).getByText("says it has paid")).toBeInTheDocument();
  expect(within(card).getByText(/a person checks for the payment/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Waiting for a person 1/ })).toHaveAttribute("aria-pressed", "true");
  expect(within(card).getByLabelText("Amount, ₦")).toHaveValue("60500"); // what the message asked for

  fireEvent.change(within(card).getByLabelText("Promised for"), { target: { value: "2026-12-30" } });
  promiseAnswer = () => json(422, { refused: "bad_date", message: "A promise is for a date from today up to a month away." });
  fireEvent.click(within(card).getByRole("button", { name: "Record promise" }));
  expect(await within(card).findByRole("alert")).toHaveTextContent("up to a month away");

  fireEvent.change(within(card).getByLabelText("Promised for"), { target: { value: "2026-10-20" } });
  fireEvent.change(within(card).getByLabelText("Amount, ₦"), { target: { value: "30,000" } });
  promiseAnswer = () => json(200, { inbound_id: 7, promise_id: 3 });
  fireEvent.click(within(card).getByRole("button", { name: "Record promise" }));
  expect(await screen.findByText(/Promise recorded: ₦30,000 by 20 Oct\. Zainab Shehu won't be messaged before then\./)).toBeInTheDocument();
  expect(posts.at(-1)).toEqual({ url: "/api/collections/replies/7/promise", body: { due_on: "2026-10-20", amount_kobo: 30_000_00 } });

  fireEvent.change(within(card).getByLabelText("What was done"), { target: { value: "Found it in suspense." } });
  fireEvent.click(within(card).getByRole("button", { name: "Close" }));
  expect(await screen.findByText("Dealt with. Your note is on the reply.")).toBeInTheDocument();
  expect(posts.at(-1)).toEqual({ url: "/api/collections/replies/7/close", body: { note: "Found it in suspense." } });
});

test("a reply read as a promise shows the promise and that the rules recorded it", async () => {
  who = AMAKA;
  await signIn();
  fireEvent.click(await screen.findByRole("button", { name: "Replies" }));
  fireEvent.click(await screen.findByRole("button", { name: "All" }));
  await vi.waitFor(() => expect(screen.getAllByRole("article", { name: "Reply from Zainab Shehu" })).toHaveLength(2));
  const promised = screen.getAllByRole("article", { name: "Reply from Zainab Shehu" })[0];
  expect(within(promised).getByText("a promise to pay")).toBeInTheDocument();
  expect(within(promised).getByText(/by 16 Oct/)).toBeInTheDocument();
  expect(within(promised).getByText(/Recorded from the reply/)).toBeInTheDocument();
  const waiting = screen.getAllByRole("article", { name: "Reply from Zainab Shehu" })[1];
  expect(within(waiting).getByText(/Read only/)).toBeInTheDocument();
  expect(within(waiting).queryByRole("button", { name: "Record promise" })).not.toBeInTheDocument();
});
