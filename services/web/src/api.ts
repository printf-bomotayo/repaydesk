// The one place the app talks to Repaydesk's API. Every call goes to /api on the app's own origin;
// Vite (in development) and nginx (in the container) forward it to the API service.

export type Status = "new" | "proposed" | "approved" | "posted" | "suspense" | "excluded" | "duplicate";
export const STATUSES: Status[] = ["new", "proposed", "approved", "posted", "suspense", "excluded", "duplicate"];

export type Channel = "bank_csv" | "bank_mt940" | "virtual_account" | "pos" | "branch_cash";
export const CHANNELS: Channel[] = ["bank_csv", "bank_mt940", "virtual_account", "pos", "branch_cash"];

export interface Staff {
  id: string;
  name: string;
  role: "officer" | "supervisor" | "auditor";
  limit_kobo: number;
}

export interface Config {
  bank_name: string;
  links: { fineract_web: string; odoo: string; jaeger: string };
}

export interface Summary {
  lines: Record<Status, number>;
  pending_proposals: number;
}

export interface LineRow {
  id: string;
  channel: Channel;
  value_date: string;
  amount_kobo: number;
  narration: string;
  payer_name: string | null;
  status: Status;
  duplicate_of: string | null;
  proposal_kind: "match" | "exclude" | null;
  proposed_by: string | null;
  loan_refs: string[];
}

export interface LinePage {
  items: LineRow[];
  total: number;
  counts: Record<Status, number>;
  limit: number;
  offset: number;
}

export interface Allocation {
  loan_ref: string;
  amount_kobo: number;
}

export interface Decision {
  staff_id: string;
  name: string;
  decision: "approve" | "reject";
  reason: string | null;
  at: string;
}

export interface AuditRow {
  id: number;
  at: string;
  actor: string;
  /** The person's name when the actor is staff; null for rules, agents and services. */
  actor_name: string | null;
  action: string;
  subject: string;
  detail: Record<string, unknown>;
  trace_id: string | null;
}

export interface LineDetail {
  id: string;
  channel: Channel;
  source_account: string;
  value_date: string;
  amount_kobo: number;
  narration: string;
  bank_reference: string;
  payer_name: string | null;
  payer_account: string | null;
  virtual_account: string | null;
  terminal_id: string | null;
  customer_reference: string | null;
  status: Status;
  duplicate_of: string | null;
  received_at: string;
  duplicates: string[];
  arrived_by:
    | { kind: "file"; file_id: number; source: string; file_name: string; received_at: string }
    | { kind: "webhook"; event_id: string; received_at: string }
    | null;
  proposals: {
    id: number;
    kind: "match" | "exclude";
    proposed_by: string;
    confidence: number;
    allocations: Allocation[];
    evidence: Record<string, unknown>;
    status: "pending" | "approved" | "rejected" | "superseded";
    proposed_at: string;
    decisions: Decision[];
  }[];
  posting: {
    outbox_id: number;
    idempotency_key: string;
    status: "pending" | "posted" | "failed";
    attempts: number;
    last_error: string | null;
    posted_at: string | null;
    next_attempt_at: string | null;
    /** Fineract's repayments, one per loan, split as Fineract applied them. */
    core_transactions: CoreTransaction[];
    /** Odoo's name for the journal entry. */
    ledger_entry: string | null;
  } | null;
  trail: Omit<AuditRow, "subject">[];
}

export interface CoreTransaction {
  loan_ref: string;
  external_id: string;
  transaction_id: number;
  principal_kobo: number;
  interest_kobo: number;
  charges_kobo: number;
  overpayment_kobo: number;
}

export interface Ingestion {
  files: {
    id: number;
    source: string;
    file_name: string;
    received_at: string;
    line_count: number | null;
    stored: number;
    duplicates: number;
  }[];
  sources: { source: string; files: number; stored: number; duplicates: number }[];
  webhooks: {
    outcomes: Record<"stored" | "duplicate" | "ignored" | "rejected", number>;
    recent: {
      id: number;
      received_at: string;
      provider: string;
      event_id: string | null;
      signature_ok: boolean;
      outcome: "stored" | "duplicate" | "ignored" | "rejected";
      line_id: string | null;
    }[];
  };
}

export type QueueView = "all" | "two_person" | "exclusions";

export interface QueueItem {
  proposal_id: number;
  kind: "match" | "exclude";
  proposed_by: string;
  confidence: number;
  allocations: Allocation[];
  evidence: Record<string, unknown>;
  proposed_at: string;
  line: {
    id: string;
    channel: Channel;
    value_date: string;
    amount_kobo: number;
    narration: string;
    payer_name: string | null;
    customer_reference: string | null;
  };
  approvals: { staff_id: string; name: string; at: string }[];
  approvals_needed: 1 | 2;
  /** What the signed-in person may do, decided by the same rule the API enforces. */
  you: { can_approve: boolean; settles: boolean; refused: string | null; message: string | null };
}

export interface QueuePage {
  items: QueueItem[];
  counts: Record<QueueView, number>;
  total: number;
  limit: number;
  offset: number;
}

export interface AuditPage {
  items: AuditRow[];
  next_before: number | null;
  actions: string[];
}

// ---- borrowers and loans ----------------------------------------------------------------------------

/** The directory is a copy of core banking, refreshed every few minutes. */
export interface DirectoryState {
  synced_at: string | null;
  borrowers: number;
  loans: number;
  /** Set when the latest attempt failed: the copy is from `synced_at`. */
  failed_since: string | null;
}

export type BorrowerView = "all" | "arrears" | "held";

export interface BorrowerRow {
  ref: string;
  name: string;
  branch_name: string | null;
  business_name: string | null;
  phone: string | null;
  loan_refs: string[];
  arrears_kobo: number;
  days_late: number;
  /** Payments Repaydesk holds for this borrower's loans, not yet posted. */
  held: { lines: number; amount_kobo: number };
}

export interface BorrowerPage {
  items: BorrowerRow[];
  counts: Record<BorrowerView, number>;
  total: number;
  limit: number;
  offset: number;
  directory: DirectoryState | null;
}

export type LoanStatus = "active" | "closed" | "overpaid" | "other";

/** Money in for a loan: posted in core banking, or held by Repaydesk and not yet posted. */
export interface MoneyIn {
  on: string | null;
  loan_ref: string;
  amount_kobo: number;
  channel: string | null;
  reference: string;
  status: "posted" | "proposed" | "approved";
  line_id: string | null;
}

export interface BorrowerLoan {
  ref: string;
  core_id: string;
  product_name: string;
  principal_kobo: number;
  disbursed_on: string | null;
  status: LoanStatus;
  outstanding_kobo: number;
  arrears_kobo: number;
  days_late: number;
}

export interface BorrowerRecord {
  ref: string;
  core_id: string;
  name: string;
  phone: string | null;
  branch_code: string | null;
  branch_name: string | null;
  business_name: string | null;
  group_ref: string | null;
  group_name: string | null;
  joined_on: string | null;
  active: boolean;
  copied_at: string;
  loans: BorrowerLoan[];
  /** True when core banking answered; false shows the directory's copy. */
  live: boolean;
  payments: MoneyIn[];
  directory: DirectoryState | null;
}

export type LoanBand = "all" | "late_1_7" | "late_8_30" | "late_31_90" | "late_90" | "closed";

export interface LoanRow {
  ref: string;
  borrower_ref: string;
  borrower_name: string | null;
  product_name: string;
  status: LoanStatus;
  outstanding_kobo: number;
  arrears_kobo: number;
  days_late: number;
  held_kobo: number;
}

export interface LoanPage {
  items: LoanRow[];
  counts: Record<LoanBand | "current" | "in_arrears", number>;
  total: number;
  limit: number;
  offset: number;
  directory: DirectoryState | null;
}

export interface LoanRecord {
  ref: string;
  core_id: string;
  borrower_ref: string;
  borrower_name: string | null;
  borrower_core_id: string | null;
  product: string;
  product_name: string;
  principal_kobo: number;
  disbursed_on: string | null;
  status: LoanStatus;
  outstanding_kobo: number;
  arrears_kobo: number;
  days_late: number;
  copied_at: string;
  virtual_account: { number: string; provider: string } | null;
  live: boolean;
  /** From core banking; null when it didn't answer. */
  schedule: { number: number; due_on: string; due_kobo: number; paid_kobo: number }[] | null;
  held_kobo: number;
  money_in: MoneyIn[];
  directory: DirectoryState | null;
}

export type Found = { to: "loan" | "borrower"; ref: string } | { to: "payments"; q: string };

/** The API said no. `status` is the HTTP status; `message` is written for people. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "repaydesk.token";

// Session storage: the token lasts as long as the tab, and is per tab. It can be unavailable
// (private windows, blocked storage), so every access is guarded and the app still works for the
// tab's lifetime from memory.
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* memory only */
  }
}

let onUnauthorised: () => void = () => {};

/** Called when the API rejects the token (expired, or the account was deactivated). */
export function whenUnauthorised(fn: () => void): void {
  onUnauthorised = fn;
}

type Query = Record<string, string | number | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== null && v !== undefined && v !== "") params.set(k, String(v));
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${withQuery(path, opts.query)}`, {
    method,
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && token) {
      setToken(null);
      onUnauthorised();
    }
    const message = typeof data.detail === "string" ? data.detail : (data.message ?? `Request failed (${res.status})`);
    throw new ApiError(res.status, message, data.refused);
  }
  return data as T;
}

export const api = {
  config: () => request<Config>("GET", "/config"),
  signIn: (staff_id: string, password: string) =>
    request<{ token: string; staff: Staff; expires_in: number }>("POST", "/auth/sign-in", { body: { staff_id, password } }),
  me: () => request<Staff & { two_person_above_kobo: number }>("GET", "/me"),
  summary: () => request<Summary>("GET", "/summary"),
  lines: (query: { status?: string; channel?: string; q?: string; limit?: number; offset?: number }) =>
    request<LinePage>("GET", "/lines", { query }),
  line: (id: string) => request<LineDetail>("GET", `/lines/${encodeURIComponent(id)}`),
  ingestion: () => request<Ingestion>("GET", "/ingestion"),
  queue: (query: { view?: QueueView; limit?: number; offset?: number }) => request<QueuePage>("GET", "/queue", { query }),
  approve: (proposalId: number) =>
    request<{ proposal_id: number; status: "first_approval" | "approved"; outbox_id: number | null }>(
      "POST",
      `/proposals/${proposalId}/approve`,
    ),
  reject: (proposalId: number, reason: string) =>
    request<{ proposal_id: number; status: "rejected" }>("POST", `/proposals/${proposalId}/reject`, { body: { reason } }),
  borrowers: (query: { view?: BorrowerView; q?: string; limit?: number; offset?: number }) =>
    request<BorrowerPage>("GET", "/borrowers", { query }),
  borrower: (ref: string) => request<BorrowerRecord>("GET", `/borrowers/${encodeURIComponent(ref)}`),
  loans: (query: { band?: LoanBand; q?: string; limit?: number; offset?: number }) =>
    request<LoanPage>("GET", "/loans", { query }),
  loan: (ref: string) => request<LoanRecord>("GET", `/loans/${encodeURIComponent(ref)}`),
  find: (q: string) => request<Found>("GET", "/find", { query: { q } }),
  audit: (query: { actor?: string; action?: string; subject?: string; before?: number | null; limit?: number }) =>
    request<AuditPage>("GET", "/audit", { query }),
};
