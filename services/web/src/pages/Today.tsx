import { useEffect } from "react";
import { Link } from "react-router";
import { api, type Status, type StoppedPosting, type Today as Morning } from "../api";
import { Failed, Loading, Pill, useLoad, useOnChange } from "../components/common";
import { LoanLink } from "../components/records";
import { RetryPosting } from "../components/RetryPosting";
import { CHANNEL_LABEL, day, naira, stamp, STATUS_LABEL } from "../format";
import { lineHref } from "./Payments";
import { runHref } from "./Runs";

const weekday = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

// Where the week's lines are, left to right in the order a line moves; each state its own tint.
const FLOW: [Exclude<Status, "duplicate">, string][] = [
  ["posted", "var(--forest)"],
  ["approved", "var(--mint)"],
  ["proposed", "var(--peach)"],
  ["new", "var(--line-strong)"],
  ["suspense", "var(--yellow)"],
  ["excluded", "var(--strong)"],
];

/** "₦31.4m" for a headline tile; the exact figure stays in the tables. */
function short(kobo: number): string {
  const n = kobo / 100;
  if (n >= 1_000_000) return `₦${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 10_000) return `₦${Math.round(n / 1000).toLocaleString()}k`;
  return naira(kobo);
}

/** The morning page: the latest statement week at a glance, and the work waiting for a person. */
export function Today() {
  const { data, error, reload } = useLoad(() => api.today(), []);
  useOnChange(reload);
  // Postings land while the page is open; a quiet refresh keeps the counts honest.
  useEffect(() => {
    const timer = window.setInterval(reload, 15_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  if (error) return <Failed error={error} retry={reload} />;
  if (!data) return <Loading what="today" />;
  return <Page t={data} reload={reload} />;
}

function Page({ t, reload }: { t: Morning; reload: () => void }) {
  const ruleShare = t.received.lines ? Math.round((100 * t.matched_by_rule) / t.received.lines) : 0;
  const flowTotal = FLOW.reduce((n, [s]) => n + t.by_status[s].lines, 0);
  const widest = Math.max(1, ...t.unmatched.bands.map((b) => b.lines));
  const failed = t.postings.stopped.filter((s) => s.status === "failed");
  const agent = t.agent;

  return (
    <>
      <section className="head">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="eyebrow">Prototype · every borrower and payment is synthetic</span>
          <h1>{weekday.format(new Date(`${t.as_of}T00:00:00Z`))}</h1>
          <p className="sub">
            Statement week {day(t.week.start)} to {day(t.week.end)} · {t.received.lines.toLocaleString()} lines received
            {t.received.lines > 0 && `; rules placed ${ruleShare}% with certainty`}.
          </p>
        </div>
        <div className="actions">
          <Link className="btn primary big" to="/review">
            Open review queue · {t.review.pending.toLocaleString()}
          </Link>
          <Link className="btn big" to="/payments">
            All payments
          </Link>
        </div>
      </section>

      <section className="sig-row" aria-label="This week">
        <div className="sig sig-navy" style={{ gridColumn: "span 2" }}>
          <span className="eyebrow">Received this week</span>
          <span className="figure">{naira(t.received.amount_kobo)}</span>
          <div className="figures">
            <div>
              <span className="v">{t.received.lines.toLocaleString()}</span>
              <span className="k">lines received</span>
            </div>
            <div>
              <span className="v">{ruleShare}%</span>
              <span className="k">placed by rule, certain cases only</span>
            </div>
            <div>
              <span className="v">{t.received.duplicates.toLocaleString()}</span>
              <span className="k">duplicates set aside</span>
            </div>
          </div>
        </div>
        {failed.length > 0 ? (
          <div className="sig sig-coral">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="eyebrow">Needs a person</span>
              <h2>
                {failed.length} posting{failed.length === 1 ? "" : "s"} stopped
              </h2>
              <p>
                {failed[0].refused ? "The gateway refused " : "The books refused "}
                <span className="mono">{failed[0].idempotency_key}</span>: {failed[0].last_error}
              </p>
            </div>
            <div className="actions">
              <a className="btn on-dark" href="#postings">
                See what stopped
              </a>
              <Link className="btn ghost-dark" to={lineHref(failed[0].line_id)}>
                Open payment
              </Link>
            </div>
          </div>
        ) : (
          <div className="sig sig-forest">
            <span className="eyebrow">Posting to Fineract and the ledger</span>
            <h2>Nothing stuck</h2>
            <p>
              {t.postings.posted_today.toLocaleString()} posted today. Every approved payment is in both books or on its
              way; the gateway posts within seconds of an approval.
            </p>
          </div>
        )}
      </section>

      <section aria-labelledby="stands" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div className="head">
          <h2 id="stands" className="section-title">
            Where the week stands
          </h2>
          <Link to="/payments">All payments</Link>
        </div>
        <div className="tiles">
          <Link className="tile peach" to="/review">
            <span className="k">Awaiting review</span>
            <span className="v">{t.review.pending.toLocaleString()}</span>
            <span>
              {t.review.two_person
                ? `${t.review.two_person} need two people (over ${naira(t.review.two_person_above_kobo)})`
                : naira(t.review.amount_kobo)}
            </span>
          </Link>
          <div className="tile yellow">
            <span className="k">Not matched yet</span>
            <span className="v">{short(t.unmatched.amount_kobo)}</span>
            <span>
              {t.unmatched.lines.toLocaleString()} lines
              {t.unmatched.oldest_days !== null && `, oldest ${t.unmatched.oldest_days} days`}, by age from the value date:
            </span>
            {t.unmatched.bands.map((b) => (
              <div key={b.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{b.label}</span>
                  <span className="mono">{b.lines.toLocaleString()}</span>
                </span>
                <span className="agebar">
                  <span style={{ width: `${(100 * b.lines) / widest}%` }} />
                </span>
              </div>
            ))}
            <Link to="/payments?status=new">Open them</Link>
          </div>
          <div className="tile forest">
            <span className="k">Posted today</span>
            <span className="v">{t.postings.posted_today.toLocaleString()}</span>
            <span>
              In Fineract and the ledger, each under its key. {t.postings.waiting} on the way, {t.postings.retrying}{" "}
              retrying.
            </span>
          </div>
          <div className="tile mint">
            <span className="k">Investigator, last 7 days</span>
            <span className="v">{(agent?.runs ?? 0).toLocaleString()}</span>
            <span>
              {agent && agent.runs > 0
                ? `lines investigated: ${agent.proposed} proposed, ${agent.suspense} held in suspense${agent.failed ? `, ${agent.failed} failed` : ""}. $${agent.cost_usd.toFixed(2)} in all.`
                : "lines investigated. It takes the ones rules can't place."}
            </span>
            <Link to="/runs">All runs</Link>
          </div>
        </div>
      </section>

      {agent?.latest?.reason && (
        <section
          className="cream"
          aria-label="The investigator's latest note"
          style={{ padding: "28px 32px", display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 28, alignItems: "center" }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
            <span className="eyebrow">
              The investigator's latest note · run {agent.latest.run_id} · {naira(agent.latest.amount_kobo)} · "
              {agent.latest.narration}"
            </span>
            <p style={{ margin: 0, fontSize: 22, lineHeight: 1.4 }}>"{agent.latest.reason}"</p>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start" }}>
            {agent.latest.confidence !== null && (
              <span className="pill" style={{ background: "#ffffff" }}>
                {agent.latest.outcome ?? "answered"} · confidence {Math.round(agent.latest.confidence * 100)}%
              </span>
            )}
            <Link className="btn primary" to={runHref(agent.latest.run_id)}>
              See how it decided
            </Link>
            <Link to={lineHref(agent.latest.line_id)}>The payment</Link>
          </div>
        </section>
      )}

      <Postings t={t} reload={reload} />

      <div className="grid2">
        <section className="panel">
          <header>
            <h2>By channel</h2>
          </header>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Channel</th>
                  <th className="num">Lines</th>
                  <th className="num">Placed by rule</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {t.channels.map((c) => (
                  <tr key={c.channel}>
                    <td>
                      <Link to={`/payments?channel=${c.channel}`}>{CHANNEL_LABEL[c.channel] ?? c.channel}</Link>
                    </td>
                    <td className="num">{c.lines.toLocaleString()}</td>
                    <td className="num">{c.lines ? Math.round((100 * c.by_rule) / c.lines) : 0}%</td>
                    <td className="num">{naira(c.amount_kobo)}</td>
                  </tr>
                ))}
                {t.channels.length === 0 && (
                  <tr>
                    <td colSpan={4} className="empty">
                      No statements yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <header>
            <h2>Where this week's lines are</h2>
            <span className="note">{flowTotal.toLocaleString()} lines</span>
          </header>
          <div className="body">
            <div className="flow" role="img" aria-label={FLOW.map(([s]) => `${STATUS_LABEL[s]} ${t.by_status[s].lines}`).join(", ")}>
              {FLOW.map(([s, colour]) =>
                t.by_status[s].lines ? <span key={s} style={{ flex: t.by_status[s].lines, background: colour }} /> : null,
              )}
            </div>
            <div className="legend">
              {FLOW.map(([s, colour]) => (
                <Link key={s} to={`/payments?status=${s}`} style={{ color: "inherit" }}>
                  <i style={{ background: colour }} />
                  {STATUS_LABEL[s]} {t.by_status[s].lines.toLocaleString()}
                </Link>
              ))}
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

/** Approved payments on their way to the books, and the ones that stopped and need a person. */
function Postings({ t, reload }: { t: Morning; reload: () => void }) {
  const p = t.postings;
  if (p.stopped.length === 0) return null;
  const failed = p.stopped.filter((s) => s.status === "failed");
  const retrying = p.stopped.filter((s) => s.status === "pending");
  return (
    <section className="panel" id="postings">
      <header>
        <h2>Posting to Fineract and the ledger</h2>
        <span className="note">
          {p.posted_today.toLocaleString()} posted today · {p.waiting} on the way · {p.retrying} retrying · {p.failed}{" "}
          stopped
        </span>
      </header>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Payment</th>
              <th>Loan</th>
              <th className="num">Amount</th>
              <th>State</th>
              <th>Why</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {[...failed, ...retrying].map((s) => (
              <StoppedRow key={s.outbox_id} s={s} reload={reload} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StoppedRow({ s, reload }: { s: StoppedPosting; reload: () => void }) {
  return (
    <tr>
      <td>
        <Link to={lineHref(s.line_id)} className="mono">
          {s.idempotency_key}
        </Link>
        <br />
        <span className="ch">{CHANNEL_LABEL[s.channel] ?? s.channel}</span>
      </td>
      <td>
        {s.loan_refs.map((ref) => (
          <div key={ref}>
            <LoanLink loanRef={ref} />
          </div>
        ))}
      </td>
      <td className="num">{naira(s.amount_kobo)}</td>
      <td>
        {s.status === "failed" ? (
          <Pill status="rejected" label={s.refused ? "refused" : "stopped"} />
        ) : (
          <Pill status="suspense" label={`retrying, attempt ${s.attempts + 1}`} />
        )}
        <br />
        <span className="note">
          {s.status === "pending" && s.next_attempt_at
            ? `next ${stamp(s.next_attempt_at)}`
            : s.last_attempt_at && `since ${stamp(s.last_attempt_at)}`}
        </span>
        {s.posted_in_core > 0 && (
          <>
            <br />
            <span className="note">in Fineract, not yet the ledger</span>
          </>
        )}
      </td>
      <td className="narr">{s.last_error}</td>
      <td>
        {s.status === "failed" ? (
          <RetryPosting outboxId={s.outbox_id} refused={s.refused} onDone={reload} />
        ) : (
          <span className="note">The gateway retries on its own</span>
        )}
      </td>
    </tr>
  );
}
