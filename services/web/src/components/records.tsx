import { Link } from "react-router";
import type { DirectoryState, MoneyIn } from "../api";
import { ago, CHANNEL_LABEL, day, naira, stamp } from "../format";
import { Pill } from "./common";

export const loanHref = (ref: string) => `/loans/${encodeURIComponent(ref)}`;
export const borrowerHref = (ref: string) => `/borrowers/${encodeURIComponent(ref)}`;

/** A loan reference that opens the loan. Stops the click reaching a clickable row around it. */
export function LoanLink({ loanRef }: { loanRef: string }) {
  return (
    <Link to={loanHref(loanRef)} className="mono" onClick={(e) => e.stopPropagation()}>
      {loanRef}
    </Link>
  );
}

/** How fresh the directory's copy is, and whether the latest sync failed. */
export function DirectoryNote({ directory }: { directory: DirectoryState | null }) {
  if (!directory || !directory.synced_at) {
    return <span className="note">The directory hasn't been copied from core banking yet.</span>;
  }
  return (
    <span className="note" title={stamp(directory.synced_at)}>
      Copied from core banking {ago(directory.synced_at)}.
      {directory.failed_since && (
        <span className="warn-text"> The latest copy failed ({ago(directory.failed_since)}); this is the one before.</span>
      )}
    </span>
  );
}

/** Shown when a record's own page couldn't reach core banking. */
export function NotLive({ copiedAt }: { copiedAt: string }) {
  return (
    <div className="error" role="status">
      Fineract didn't answer, so this is the directory's copy from {stamp(copiedAt)}. Balances may have changed since.
    </div>
  );
}

const channel = (c: string | null) => (c ? (CHANNEL_LABEL[c] ?? c) : "–");

/** Posted repayments and payments Repaydesk holds, newest first. */
export function MoneyInTable({ rows, showLoan }: { rows: MoneyIn[]; showLoan: boolean }) {
  if (rows.length === 0) return <div className="empty">No payments yet.</div>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Channel</th>
            {showLoan && <th>Loan</th>}
            <th className="num">Amount</th>
            <th>Status</th>
            <th>Reference</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.reference}-${p.loan_ref}-${p.status}`}>
              <td className="mono">{p.on ? day(p.on) : "–"}</td>
              <td className="ch">{channel(p.channel)}</td>
              {showLoan && (
                <td>
                  <LoanLink loanRef={p.loan_ref} />
                </td>
              )}
              <td className="num">{naira(p.amount_kobo)}</td>
              <td>
                <Pill status={p.status} />
              </td>
              <td>
                <Reference p={p} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Opens the payment line when Repaydesk has one; core banking's own reference otherwise. */
export function Reference({ p }: { p: MoneyIn }) {
  return p.line_id ? (
    <Link to={`/payments/${encodeURIComponent(p.line_id)}`} className="mono">
      {p.reference}
    </Link>
  ) : (
    <span className="mono note">{p.reference}</span>
  );
}

/** A loan's lateness as a pill: "3 days late", or "current" / "closed". */
export function LoanState({ status, daysLate }: { status: string; daysLate: number }) {
  if (status === "closed" || status === "overpaid") return <Pill status="excluded" label={status} />;
  if (daysLate > 0) return <Pill status="late" label={`${daysLate} day${daysLate === 1 ? "" : "s"} late`} />;
  return <Pill status="posted" label="current" />;
}
