import { useEffect } from "react";
import { Link } from "react-router";
import { api, type Status, type StoppedPosting, type Today as Morning } from "../api";
import { Failed, Loading, Pill, useLoad, useOnChange } from "../components/common";
import { LoanLink } from "../components/records";
import { RetryPosting } from "../components/RetryPosting";
import { CHANNEL_LABEL, day, naira, stamp, STATUS_LABEL } from "../format";
import { lineHref } from "./Payments";

const weekday = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

// Where the week's lines are, left to right in the order a line moves.
const FLOW: [Exclude<Status, "duplicate">, string][] = [
  ["posted", "var(--ok)"],
  ["approved", "color-mix(in srgb, var(--ok) 55%, var(--panel))"],
  ["proposed", "var(--info)"],
  ["new", "var(--quiet)"],
  ["suspense", "var(--warn)"],
  ["excluded", "var(--line)"],
];

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

  return (
    <>
      <div className="head">
        <div>
          <h1>{weekday.format(new Date(`${t.as_of}T00:00:00Z`))}</h1>
          <p className="sub">
            Statement week {day(t.week.start)} to {day(t.week.end)} · {t.received.lines.toLocaleString()} lines received
          </p>
        </div>
        <div className="actions">
          <Link className="btn primary" to="/review" style={{ textDecoration: "none" }}>
            Open review queue ({t.review.pending.toLocaleString()})
          </Link>
        </div>
      </div>

      <section className="stats">
        <div className="panel stat">
          <span className="k">Received this week</span>
          <span className="v">{naira(t.received.amount_kobo)}</span>
          <span className="d">
            {t.received.lines.toLocaleString()} lines · {t.received.duplicates.toLocaleString()} duplicates set aside
          </span>
        </div>
        <div className="panel stat">
          <span className="k">Matched by rule</span>
          <span className="v">{ruleShare}%</span>
          <span className="d">{t.matched_by_rule.toLocaleString()} lines, certain cases only</span>
        </div>
        <div className="panel stat">
          <span className="k">Waiting for review</span>
          <span className="v">{t.review.pending.toLocaleString()}</span>
          <span className="d">
            {t.review.two_person
              ? `${t.review.two_person} need two people (over ${naira(t.review.two_person_above_kobo)})`
              : naira(t.review.amount_kobo)}
          </span>
        </div>
        <div className="panel stat">
          <span className="k">Not matched yet</span>
          <span className="v">{naira(t.unmatched.amount_kobo)}</span>
          <span className="d">
            {t.unmatched.lines.toLocaleString()} lines
            {t.unmatched.oldest_days !== null && ` · oldest ${t.unmatched.oldest_days} days`}
          </span>
        </div>
      </section>

      <Postings t={t} reload={reload} />

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
              <Link key={s} to={`/payments?status=${s}`} style={{ color: "inherit", textDecoration: "none" }}>
                <i style={{ background: colour }} />
                {STATUS_LABEL[s]} {t.by_status[s].lines.toLocaleString()}
              </Link>
            ))}
          </div>
        </div>
      </section>

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
                  <th className="num">By rule</th>
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
            <h2>Not matched yet, by age</h2>
            <Link className="btn" to="/payments?status=new" style={{ textDecoration: "none" }}>
              Open them
            </Link>
          </header>
          <div className="body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {t.unmatched.bands.map((b) => (
              <div key={b.label}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span>{b.label}</span>
                  <span className="num">
                    {b.lines.toLocaleString()} lines · {naira(b.amount_kobo)}
                  </span>
                </div>
                <div className="bar">
                  <span style={{ width: `${(100 * b.lines) / widest}%`, background: "var(--warn)" }} />
                </div>
              </div>
            ))}
            <p className="note">
              Counted from the value date, the day the money reached the bank. Rules found nothing certain for these; the
              investigator agent works through them from milestone 3.
            </p>
          </div>
        </section>
      </div>

      <div className="later-box">The latest agent runs show here with the investigator agent, in milestone 3.</div>
    </>
  );
}

/** Approved payments on their way to the books, and the ones that stopped and need a person. */
function Postings({ t, reload }: { t: Morning; reload: () => void }) {
  const p = t.postings;
  const failed = p.stopped.filter((s) => s.status === "failed");
  const retrying = p.stopped.filter((s) => s.status === "pending");
  return (
    <section className="panel">
      <header>
        <h2>Posting to Fineract and the ledger</h2>
        <span className="note">
          {p.posted_today.toLocaleString()} posted today · {p.waiting} on the way · {p.retrying} retrying ·{" "}
          {p.failed} stopped
        </span>
      </header>
      {p.stopped.length === 0 ? (
        <div className="body">
          <p className="note">
            Nothing stuck. Every approved payment is in both books, or on its way: the gateway posts within seconds of an
            approval.
          </p>
        </div>
      ) : (
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
      )}
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
