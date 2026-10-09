// Money is integer kobo everywhere (ADR 0003). It becomes naira only here, for display.

const naira0 = new Intl.NumberFormat("en-NG", { maximumFractionDigits: 0 });
const naira2 = new Intl.NumberFormat("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₦605,000 for whole naira, ₦60,500.50 when there are kobo. Never a float in between. */
export function naira(kobo: number): string {
  const sign = kobo < 0 ? "−" : "";
  const abs = Math.abs(kobo);
  const whole = Math.trunc(abs / 100);
  const rest = abs % 100;
  return `${sign}₦${rest ? naira2.format(whole + rest / 100) : naira0.format(whole)}`;
}

const TZ = "Africa/Lagos"; // the bank's time zone, as the API's bank_today()

const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const stampFmt = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: TZ,
});

/** A value date (no time, no zone): "7 Oct". */
export function day(isoDate: string): string {
  return dayFmt.format(new Date(`${isoDate}T00:00:00Z`));
}

/** A moment, shown in the bank's time: "9 Oct, 14:03:22". */
export function stamp(iso: string): string {
  return stampFmt.format(new Date(iso));
}

export const CHANNEL_LABEL: Record<string, string> = {
  bank_csv: "Bank transfer (CSV)",
  bank_mt940: "Bank transfer (MT940)",
  virtual_account: "Virtual account",
  pos: "POS agent",
  branch_cash: "Branch cash",
};

export const STATUS_LABEL: Record<string, string> = {
  new: "waiting",
  proposed: "awaiting review",
  approved: "approved, to post",
  posted: "posted",
  suspense: "suspense",
  excluded: "excluded",
  duplicate: "duplicate",
};

/** "rule:virtual_account" → "Rule: virtual account"; a person's id is left as it is. */
export function actorLabel(actor: string): string {
  const [kind, name] = actor.includes(":") ? actor.split(/:(.*)/s) : ["", actor];
  if (!kind) return actor;
  const pretty = name.replace(/_/g, " ");
  return { rule: `Rule: ${pretty}`, agent: `Agent: ${pretty}`, service: `Service: ${pretty}` }[kind] ?? actor;
}
