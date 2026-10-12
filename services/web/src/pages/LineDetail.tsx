import { Link, useParams } from "react-router";
import { api, type LineDetail as Line } from "../api";
import { ExternalLink, Failed, Loading, Pill, useLoad, useOnChange } from "../components/common";
import { LoanLink } from "../components/records";
import { Checks } from "../components/Checks";
import { RetryPosting } from "../components/RetryPosting";
import { actorLabel, CHANNEL_LABEL, day, naira, stamp } from "../format";
import { useSession } from "../session";
import { lineHref } from "./Payments";

const ACTION: Record<string, string> = {
  "payment.received": "Received",
  "proposal.created": "Proposed",
  "proposal.first_approval": "First approval",
  "proposal.approved": "Approved",
  "proposal.rejected": "Rejected",
  "posting.queued": "Handed to the posting gateway",
  "posting.posted": "Posted to Fineract and the ledger",
  "posting.retry_scheduled": "Posting retried later",
  "posting.failed": "Posting failed; needs a person",
  "posting.refused": "Posting refused by the gateway",
  "posting.retry_requested": "Sent back to the posting gateway",
};

export function LineDetail() {
  const { id = "" } = useParams();
  const { data, error, reload } = useLoad(() => api.line(id), [id]);
  useOnChange(reload); // a retry from the posting panel

  return (
    <>
      <div className="crumbs">
        <Link to="/payments">Payments</Link>
        <span>/</span>
        <span className="mono">{id}</span>
      </div>
      {error ? <Failed error={error} retry={reload} /> : !data ? <Loading what="the payment" /> : <CaseFile line={data} />}
    </>
  );
}

function CaseFile({ line }: { line: Line }) {
  const { config } = useSession();
  const live = line.proposals.find((p) => p.status === "pending" || p.status === "approved");
  const history = line.proposals.filter((p) => p !== live);

  return (
    <>
      <div className="head">
        <div>
          <h1>
            {naira(line.amount_kobo)} · {CHANNEL_LABEL[line.channel] ?? line.channel}
          </h1>
          <p className="sub mono">{line.narration}</p>
        </div>
        <div className="actions">
          <Pill status={line.status} />
          {line.status === "proposed" && (
            <Link className="btn primary" to="/review" style={{ textDecoration: "none" }}>
              Decide in the review queue
            </Link>
          )}
        </div>
      </div>

      {line.posting?.status === "failed" && <Stopped line={line} posting={line.posting} />}

      {line.duplicate_of && (
        <div className="later-box">
          A second sighting of a line already held, kept so it's on record that it arrived. The original is{" "}
          <Link to={lineHref(line.duplicate_of)} className="mono">
            {line.duplicate_of}
          </Link>
          . Nothing is proposed or posted for a copy.
        </div>
      )}

      <div className="split">
        <section className="panel">
          <header>
            <h2>As received</h2>
            <span className="note">{arrival(line)}</span>
          </header>
          <div className="body">
            <dl className="kv">
              <dt>Value date</dt>
              <dd>{day(line.value_date)}</dd>
              <dt>Amount</dt>
              <dd className="mono">{naira(line.amount_kobo)}</dd>
              <dt>Narration</dt>
              <dd className="mono">{line.narration}</dd>
              {line.payer_name && (
                <>
                  <dt>Payer</dt>
                  <dd>{line.payer_name}</dd>
                </>
              )}
              {line.payer_account && (
                <>
                  <dt>Payer's account</dt>
                  <dd className="mono">{line.payer_account}</dd>
                </>
              )}
              <dt>Into</dt>
              <dd className="mono">{line.virtual_account ?? line.source_account}</dd>
              {line.terminal_id && (
                <>
                  <dt>Terminal</dt>
                  <dd className="mono">{line.terminal_id}</dd>
                </>
              )}
              {line.customer_reference && (
                <>
                  <dt>Reference typed</dt>
                  <dd className="mono">{line.customer_reference}</dd>
                </>
              )}
              <dt>Bank's reference</dt>
              <dd className="mono">{line.bank_reference}</dd>
              <dt>Line id</dt>
              <dd className="mono">{line.id}</dd>
            </dl>
            {line.duplicates.length > 0 && (
              <p className="note">
                Arrived again {line.duplicates.length === 1 ? "once more" : `${line.duplicates.length} more times`}:{" "}
                {line.duplicates.map((d, i) => (
                  <span key={d}>
                    {i > 0 && ", "}
                    <Link to={lineHref(d)} className="mono">
                      {d}
                    </Link>
                  </span>
                ))}
                . Each was recognised and kept as a duplicate.
              </p>
            )}
          </div>
        </section>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="panel">
            <header>
              <h2>Proposal</h2>
              {live && <Pill status={live.status === "pending" ? "proposed" : "approved"} label={live.status === "pending" ? "awaiting review" : "approved"} />}
            </header>
            <div className="body">
              {!live ? (
                <p className="note">
                  {line.duplicate_of
                    ? "None: this is a copy."
                    : line.status === "excluded"
                      ? "Excluded: approved as not a repayment."
                      : "None yet. No rule could place this line with certainty, so it waits for the investigator agent (milestone 3)."}
                </p>
              ) : (
                <ProposalBody p={live} />
              )}
            </div>
          </section>

          {line.posting && (
            <PostingPanel
              posting={line.posting}
              refused={line.trail.filter((t) => t.action.startsWith("posting.")).at(-1)?.action === "posting.refused"}
            />
          )}

          {history.length > 0 && (
            <section className="panel">
              <header>
                <h2>Earlier proposals</h2>
              </header>
              <div className="body">
                {history.map((p) => (
                  <div key={p.id} style={{ marginBottom: 8 }}>
                    <Pill status={p.status} /> <span className="note">{actorLabel(p.proposed_by)}</span>
                    {p.decisions
                      .filter((d) => d.decision === "reject")
                      .map((d) => (
                        <p key={d.staff_id} className="note">
                          {d.name}: “{d.reason}”, {stamp(d.at)}
                        </p>
                      ))}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      <section className="panel">
        <header>
          <h2>Trail</h2>
          <span className="note">every recorded step, from the audit log</span>
        </header>
        <div className="body">
          {line.trail.length === 0 ? (
            <p className="note">Nothing recorded for this line yet.</p>
          ) : (
            <ol className="timeline">
              {line.trail.map((t) => (
                <li key={t.id}>
                  <span className="t">{stamp(t.at)}</span>
                  <span className="dot" />
                  <span>
                    <b>{ACTION[t.action] ?? t.action}</b> by {t.actor_name ?? actorLabel(t.actor)}
                    {t.action === "proposal.rejected" && typeof t.detail.reason === "string" && <>: “{t.detail.reason}”</>}
                    {t.trace_id && config && (
                      <>
                        {" · "}
                        <ExternalLink href={`${config.links.jaeger}/trace/${t.trace_id}`}>trace</ExternalLink>
                      </>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>
    </>
  );
}

function ProposalBody({ p }: { p: Line["proposals"][number] }) {
  const approvals = p.decisions.filter((d) => d.decision === "approve");
  return (
    <>
      <p className="note">
        {actorLabel(p.proposed_by)} · {p.confidence < 1 ? `confidence ${Math.round(p.confidence * 100)}%` : "certain"} ·{" "}
        {stamp(p.proposed_at)}
      </p>
      {p.kind === "exclude" ? (
        <p>
          <b>Not a repayment.</b> Exclude it from matching.
        </p>
      ) : (
        <table>
          <tbody>
            {p.allocations.map((a) => (
              <tr key={a.loan_ref}>
                <td>
                  <LoanLink loanRef={a.loan_ref} />
                </td>
                <td className="num">{naira(a.amount_kobo)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{ marginTop: 8 }}>
        <Checks evidence={p.evidence} />
      </div>
      {approvals.length > 0 && (
        <p className="note">
          Approved by {approvals.map((d) => `${d.name} (${stamp(d.at)})`).join(", then ")}.
        </p>
      )}
    </>
  );
}

function arrival(line: Line): string {
  const a = line.arrived_by;
  if (!a) return "";
  return a.kind === "file" ? `in ${a.file_name}, ${stamp(a.received_at)}` : `by webhook, ${stamp(a.received_at)}`;
}

const POSTING_PILL = {
  pending: ["approved", "waiting to post"],
  posted: ["posted", "posted"],
  failed: ["late", "failed"],
} as const;

/** What the gateway did: Fineract's repayment for each loan, split as Fineract applied it, and the ledger entry. */
/** A posting the books refused, at the top of the case file in coral: it needs a person. */
function Stopped({ line, posting }: { line: Line; posting: NonNullable<Line["posting"]> }) {
  const refused = line.trail.filter((t) => t.action.startsWith("posting.")).at(-1)?.action === "posting.refused";
  return (
    <section className="sig sig-coral" aria-label="Posting stopped" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", alignItems: "center", gap: 24 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <span className="eyebrow">
          {refused ? "Refused by the gateway" : "Posting stopped"} · <span className="mono">{posting.idempotency_key}</span> ·{" "}
          {posting.attempts} attempt{posting.attempts === 1 ? "" : "s"}
        </span>
        <h2 style={{ fontSize: 26 }}>{posting.last_error ?? "It stopped without a reason."}</h2>
        <p>
          {refused
            ? "The decisions don't support this approval, so a retry would be refused the same way. It needs a new proposal and decision."
            : "Put the cause right in core banking or the ledger, then send it back: the gateway rechecks the approval, and the key keeps it from posting twice."}
        </p>
      </div>
      <RetryPosting outboxId={posting.outbox_id} refused={refused} onDark />
    </section>
  );
}

function PostingPanel({ posting, refused }: { posting: NonNullable<Line["posting"]>; refused: boolean }) {
  const [pill, label] = POSTING_PILL[posting.status];
  return (
    <section className="panel">
      <header>
        <h2>Posting</h2>
        <Pill status={pill} label={label} />
      </header>
      <div className="body">
        <dl className="kv">
          <dt>Idempotency key</dt>
          <dd className="mono">{posting.idempotency_key}</dd>
          {posting.posted_at && (
            <>
              <dt>Posted</dt>
              <dd>{stamp(posting.posted_at)}</dd>
            </>
          )}
          {posting.ledger_entry && (
            <>
              <dt>Ledger entry</dt>
              <dd className="mono">{posting.ledger_entry}</dd>
            </>
          )}
          <dt>Attempts</dt>
          <dd>{posting.attempts}</dd>
          {posting.last_error && (
            <>
              <dt>{posting.status === "failed" ? "Why it stopped" : "Last error"}</dt>
              <dd className="mono">{posting.last_error}</dd>
            </>
          )}
          {posting.status === "pending" && posting.next_attempt_at && (
            <>
              <dt>Next attempt</dt>
              <dd>{stamp(posting.next_attempt_at)}</dd>
            </>
          )}
        </dl>
        {posting.core_transactions.length > 0 && (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Fineract repayment</th>
                  <th className="num">Principal</th>
                  <th className="num">Interest</th>
                  <th className="num">Other</th>
                </tr>
              </thead>
              <tbody>
                {posting.core_transactions.map((t) => (
                  <tr key={t.external_id}>
                    <td>
                      <LoanLink loanRef={t.loan_ref} /> <span className="mono note">{t.external_id}</span>
                    </td>
                    <td className="num">{naira(t.principal_kobo)}</td>
                    <td className="num">{naira(t.interest_kobo)}</td>
                    <td className="num">{otherPortions(t.charges_kobo, t.overpayment_kobo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="note">
          {posting.status === "posted"
            ? "The key is the repayment's external id in Fineract and the journal entry's reference in the ledger. The entry credits each loan exactly as Fineract split the payment."
            : posting.status === "failed"
              ? refused
                ? "The gateway refused this approval: the decisions don't support it. It won't try this again on its own, and a retry would be refused the same way."
                : "The gateway stopped and won't try this again on its own. Any Fineract repayment listed above was posted; nothing was written to the ledger. Once the cause is fixed, send it back; the key keeps it from posting twice."
              : posting.core_transactions.length > 0
                ? "Fineract has the repayment; the ledger didn't answer. The next attempt finds Fineract's repayment by this key and writes only the ledger entry."
                : "The posting gateway picks this up within seconds. It looks each write up by this key before making it, so a retry never posts twice."}
        </p>
      </div>
    </section>
  );
}

function otherPortions(charges: number, overpaid: number): string {
  const parts = [charges > 0 && `${naira(charges)} charges`, overpaid > 0 && `${naira(overpaid)} overpaid`].filter(Boolean);
  return parts.length ? parts.join(", ") : "–";
}
