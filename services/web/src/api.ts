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
    status: "pending" | "claimed" | "posted" | "failed";
    attempts: number;
    last_error: string | null;
    posted_at: string | null;
  } | null;
  trail: Omit<AuditRow, "subject">[];
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

export interface AuditPage {
  items: AuditRow[];
  next_before: number | null;
  actions: string[];
}

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
  audit: (query: { actor?: string; action?: string; subject?: string; before?: number | null; limit?: number }) =>
    request<AuditPage>("GET", "/audit", { query }),
};
