import { Link, useParams } from "react-router";
import { api, type LineDetail as Line } from "../api";
import { ExternalLink, Failed, Loading, Pill, useLoad } from "../components/common";
import { LoanLink } from "../components/records";
import { Checks } from "../components/Checks";
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
};

export function LineDetail() {
  const { id = "" } = useParams();
  const { data, error, reload } = useLoad(() => api.line(id), [id]);

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
            <section className="panel">
              <header>
                <h2>Posting</h2>
                <Pill status={line.posting.status === "posted" ? "posted" : "approved"} label={line.posting.status} />
              </header>
              <div className="body">
                <dl className="kv">
                  <dt>Idempotency key</dt>
                  <dd className="mono">{line.posting.idempotency_key}</dd>
                  <dt>Attempts</dt>
                  <dd>{line.posting.attempts}</dd>
                  {line.posting.posted_at && (
                    <>
                      <dt>Posted</dt>
                      <dd>{stamp(line.posting.posted_at)}</dd>
                    </>
                  )}
                  {line.posting.last_error && (
                    <>
                      <dt>Last error</dt>
                      <dd className="mono">{line.posting.last_error}</dd>
                    </>
                  )}
                </dl>
                <p className="note">
                  {line.posting.status === "posted"
                    ? "The key is the repayment's external id in Fineract and the journal entry's reference in Odoo."
                    : "Waiting for the posting gateway (chunk 8). It posts under this key, so a retry can never post twice."}
                </p>
              </div>
            </section>
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
