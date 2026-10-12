import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, type DrafterVerdict, type InvestigatorVerdict, type RunDetail, type RunOutcome } from "../api";
import { Failed, Loading, Pager, Pill, useLoad } from "../components/common";
import { LoanLink } from "../components/records";
import { CHANNEL_LABEL, day, naira, stamp } from "../format";
import { lineHref } from "./Payments";

const PAGE = 50;

export const runHref = (id: number) => `/runs/${id}`;

/** For a proposal the investigator made: a link to the run behind it. Nothing for a rule's. */
export function RunLink({ evidence }: { evidence: Record<string, unknown> | null | undefined }) {
  const id = evidence?.run_id;
  return typeof id === "number" ? <Link to={runHref(id)}>How it decided (run {id})</Link> : null;
}

const OUTCOMES: RunOutcome[] = ["proposed", "excluded", "suspense", "drafted", "failed"];
const OUTCOME_LABEL: Record<string, string> = {
  proposed: "proposed",
  excluded: "not a repayment",
  suspense: "held in suspense",
  drafted: "drafted a message",
  failed: "failed",
};
const OUTCOME_PILL: Record<string, string> = {
  proposed: "proposed",
  excluded: "excluded",
  suspense: "suspense",
  drafted: "two",
  failed: "failed",
};
const MESSAGE_CHANNEL: Record<string, string> = { sms: "SMS", whatsapp: "WhatsApp" };

function OutcomePill({ outcome }: { outcome: RunOutcome | null }) {
  return outcome ? (
    <Pill status={OUTCOME_PILL[outcome]} label={OUTCOME_LABEL[outcome]} />
  ) : (
    <Pill status="superseded" label="overtaken" />
  );
}

function usd(n: number): string {
  return `$${n < 0.1 ? n.toFixed(3) : n.toFixed(2)}`;
}

/** Every investigation, newest first: what it concluded, at what cost, and where its proposal stands. */
export function Runs() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const outcome = params.get("outcome") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const { data, error, reload } = useLoad(() => api.runs({ outcome, limit: PAGE, offset }), [outcome, offset]);

  function set(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next);
  }

  return (
    <>
      <section className="head">
        <div>
          <h1>Agent runs</h1>
          <p className="sub">
            Every run by the agents: the investigator, which places payments, and the collector, which drafts messages
            to late borrowers. What each looked at, what it concluded, and what Repaydesk checked before keeping it.
            Nothing is posted or sent until a person approves it.
          </p>
        </div>
        {data && (
          <div className="stat" style={{ minWidth: 220 }}>
            <span className="k">All runs</span>
            <span className="v">{data.all_runs.toLocaleString()}</span>
            <span className="d">{usd(data.cost_usd)} at list prices</span>
          </div>
        )}
      </section>

      <div className="filters" role="group" aria-label="Filter by outcome">
        <button className="chip" aria-pressed={!outcome} onClick={() => set({ outcome: "", offset: "" })}>
          All{data && ` ${data.all_runs.toLocaleString()}`}
        </button>
        {OUTCOMES.map((o) => (
          <button key={o} className="chip" aria-pressed={outcome === o} onClick={() => set({ outcome: o, offset: "" })}>
            {OUTCOME_LABEL[o]}
            {data && ` ${data.counts[o].toLocaleString()}`}
          </button>
        ))}
      </div>

      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="the runs" />
      ) : (
        <section className="panel">
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>When</th>
                  <th>About</th>
                  <th>Outcome</th>
                  <th className="num">Confidence</th>
                  <th className="num">Lookups</th>
                  <th className="num">Cost</th>
                  <th>Its proposal or message now</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} className="click" onClick={() => navigate(runHref(r.id))}>
                    <td>
                      <Link to={runHref(r.id)} onClick={(e) => e.stopPropagation()}>
                        {r.id}
                      </Link>
                    </td>
                    <td className="ch">{stamp(r.started_at)}</td>
                    <td>
                      <span className="mono" style={{ color: "var(--ink)" }}>
                        {naira(r.amount_kobo)}
                      </span>
                      {r.agent === "collector" && <span className="note"> due on {r.loan_ref}</span>}
                      <br />
                      <span className="narr">{r.narration}</span>
                    </td>
                    <td>
                      <OutcomePill outcome={r.outcome} />
                    </td>
                    <td className="num">{r.confidence !== null ? `${Math.round(r.confidence * 100)}%` : "–"}</td>
                    <td className="num">{r.tool_calls}</td>
                    <td className="num">{usd(r.cost_usd)}</td>
                    <td>
                      {r.proposal_status ? (
                        <Pill status={r.proposal_status} label={r.agent === "collector" ? `message ${r.proposal_status}` : undefined} />
                      ) : (
                        <span className="note">–</span>
                      )}
                    </td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="empty">
                      No runs yet. <span className="mono">python scripts/dev.py investigate</span> sends the open lines to
                      the investigator.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE} total={data.total} onPage={(o) => set({ offset: o ? String(o) : "" })} />
        </section>
      )}
    </>
  );
}

/** One run whole: the payment, the triage, each lookup and its answer, the conclusion, the checks. */
export function RunPage() {
  const { id = "" } = useParams();
  const { data, error, reload } = useLoad(() => api.run(id), [id]);
  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/runs">Agent runs</Link>
        <span>/</span>
        <span>Run {id}</span>
      </nav>
      {error ? (
        <Failed error={error} retry={reload} />
      ) : !data ? (
        <Loading what="the run" />
      ) : data.agent === "collector" ? (
        <CollectorRun run={data} />
      ) : (
        <Run run={data} />
      )}
    </>
  );
}

const BEARING: Record<string, string> = { for: "yes", against: "no", neutral: "na" };
const MARK: Record<string, string> = { yes: "✓", no: "✕", na: "–" };

function Run({ run }: { run: RunDetail }) {
  const v = run.verdict as InvestigatorVerdict | null;
  const line = run.line!; // an investigator run is always about a payment line
  const passed = run.checks.filter((c) => c.ok).length;
  const target = v?.allocations.length
    ? v.allocations.map((a) => a.loan_ref).join(", ")
    : run.outcome === "excluded"
      ? "not a repayment"
      : run.outcome === "suspense"
        ? "suspense"
        : run.outcome === "failed"
          ? "no answer"
          : "";

  return (
    <>
      <section className="sig sig-navy" aria-label={`Run ${run.id}`}>
        <span className="eyebrow">
          Run {run.id} · the {run.agent} · {stamp(run.started_at)}
        </span>
        <h2 style={{ fontSize: 30 }}>
          {naira(line.amount_kobo)} "{line.narration}"{target && ` → ${target}`}
        </h2>
        <div className="figures" style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>
          <div>
            <span className="v">{run.outcome ? OUTCOME_LABEL[run.outcome] : "overtaken"}</span>
            <span className="k">outcome</span>
          </div>
          <div>
            <span className="v">{run.confidence !== null ? `${Math.round(run.confidence * 100)}%` : "–"}</span>
            <span className="k">confidence</span>
          </div>
          <div>
            <span className="v">{run.steps.length}</span>
            <span className="k">lookups</span>
          </div>
          <div>
            <span className="v">{usd(run.cost_usd)}</span>
            <span className="k">at list prices{run.seconds ? `, ${run.seconds}s` : ""}</span>
          </div>
          <div>
            <span className="v">{run.checks.length ? `${passed} of ${run.checks.length}` : "–"}</span>
            <span className="k">checks passed</span>
          </div>
        </div>
      </section>

      {run.error && (
        <section className="sig sig-coral" aria-label="The run failed">
          <span className="eyebrow">The run failed; the payment was left as it was</span>
          <p className="mono">{run.error}</p>
        </section>
      )}

      <div className="split">
        <section aria-labelledby="looked" style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <h2 id="looked" className="section-title">
            What it looked up
          </h2>
          {run.triage && (
            <div className="tile mint" style={{ minHeight: 0 }}>
              <span className="k">Triage{run.classifier_model && `, by ${run.classifier_model}`}</span>
              <span>
                {run.triage.kind.replace(/_/g, " ")}
                {run.triage.business && `: ${run.triage.business}`}
                {run.triage.group && `; group ${run.triage.group}`}
                {run.triage.paid_for && `; paid for ${run.triage.paid_for}`}
                {run.triage.names.length > 0 && `; names ${run.triage.names.join(", ")}`}
                {run.triage.reference && `; reference ${run.triage.reference}`}. {run.triage.language}.
              </span>
            </div>
          )}
          {run.steps.map((s, i) => (
            <div className="step" key={i}>
              <div className="call">
                <span style={{ color: "var(--ink)" }}>
                  {i + 1} · {s.tool}({Object.entries(s.input).map(([k, val]) => `${k}: ${JSON.stringify(val)}`).join(", ")})
                </span>
                <span className="note">
                  {s.error ? "error" : "answered"}
                  {s.ms !== undefined && ` · ${s.ms} ms`}
                </span>
              </div>
              <details>
                <summary style={{ cursor: "pointer", padding: "10px 16px", color: "var(--link)" }}>What came back</summary>
                <pre className="raw" style={{ margin: "0 12px 12px", maxHeight: 360, overflow: "auto" }}>
                  {s.output}
                </pre>
              </details>
            </div>
          ))}
          {run.steps.length === 0 && <p className="note">It looked nothing up.</p>}
        </section>

        <section aria-labelledby="answer" style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <h2 id="answer" className="section-title">
            Its answer, and the checks
          </h2>
          {v ? (
            <div className="cream" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="eyebrow">
                {run.model} · {v.outcome.replace(/_/g, " ")} · {Math.round(v.confidence * 100)}%
              </span>
              <p style={{ margin: 0, fontSize: 18, lineHeight: 1.45 }}>"{v.reason}"</p>
              {v.allocations.map((a) => (
                <div key={a.loan_ref} className="loan-row" style={{ background: "#ffffff", borderRadius: 10, padding: "12px 16px" }}>
                  <LoanLink loanRef={a.loan_ref} />
                  <span className="num">{naira(a.amount_kobo)}</span>
                </div>
              ))}
              <div>
                {v.findings.map((f, i) => (
                  <div key={i} className={`check ${BEARING[f.bearing] ?? "na"}`}>
                    <span className="m" aria-label={f.bearing}>
                      {MARK[BEARING[f.bearing] ?? "na"]}
                    </span>
                    <span>{f.fact}</span>
                  </div>
                ))}
              </div>
              {v.candidates.length > 0 && <span className="note">Considered: {v.candidates.join(", ")}</span>}
            </div>
          ) : (
            <p className="note">No answer: see why it failed, above.</p>
          )}
          {run.checks.length > 0 && (
            <section className="panel" aria-label="Repaydesk's checks">
              <header>
                <h2>Repaydesk checked it before acting</h2>
              </header>
              <div className="body">
                {run.checks.map((c, i) => (
                  <div key={i} className={`check ${c.ok ? "yes" : "no"}`}>
                    <span className="m" aria-label={c.ok ? "passed" : "failed"}>
                      {c.ok ? "✓" : "✕"}
                    </span>
                    <span>{c.detail}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section className="panel" aria-label="What people decided">
            <header>
              <h2>The payment, and what people decided</h2>
            </header>
            <div className="body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span>
                {CHANNEL_LABEL[line.channel] ?? line.channel} · {day(line.value_date)} · now <Pill status={line.status} />
              </span>
              {run.proposal ? (
                <>
                  <span>
                    Proposal {run.proposal.id}: <Pill status={run.proposal.status} />
                  </span>
                  {run.proposal.decisions.map((d, i) => (
                    <span key={i} className="note">
                      {d.decision === "approve" ? "Approved" : "Rejected"} by {d.name}, {stamp(d.at)}
                      {d.reason && `: ${d.reason}`}
                    </span>
                  ))}
                </>
              ) : (
                <span className="note">
                  {run.outcome === "suspense" ? "Held in suspense, for a person to place." : "No proposal was made."}
                </span>
              )}
              <div className="actions">
                {run.proposal?.status === "pending" && (
                  <Link className="btn primary" to="/review">
                    Review the proposal
                  </Link>
                )}
                <Link className="btn" to={lineHref(line.id)}>
                  The payment
                </Link>
              </div>
            </div>
          </section>
          <Tokens run={run} />
        </section>
      </div>
    </>
  );
}

function Tokens({ run }: { run: RunDetail }) {
  if (Object.keys(run.usage).length === 0) return null;
  return (
    <p className="note">
      Tokens:{" "}
      {Object.entries(run.usage)
        .map(([m, u]) => `${m} ${(u.input + u.cache_read + u.cache_write).toLocaleString()} in, ${u.output.toLocaleString()} out`)
        .join("; ")}
      .
    </p>
  );
}

/** A collector run: the facts Repaydesk gave it, the message it drafted, the policy checks, the message now. */
function CollectorRun({ run }: { run: RunDetail }) {
  const v = run.verdict as DrafterVerdict | null;
  const m = run.message;
  const checks = m?.checks ?? run.checks;
  const passed = checks.filter((c) => c.ok).length;
  const brief = v?.brief ?? {};
  const name = typeof brief.first_name === "string" ? brief.first_name : "the borrower";

  return (
    <>
      <section className="sig sig-navy" aria-label={`Run ${run.id}`}>
        <span className="eyebrow">
          Run {run.id} · the {run.agent} · {stamp(run.started_at)}
        </span>
        <h2 style={{ fontSize: 30 }}>
          {m ? `${MESSAGE_CHANNEL[m.channel] ?? m.channel} to ${name}, ${naira(m.amount_due_kobo)} due` : "No message"}
          {run.loan_ref && ` · ${run.loan_ref}`}
        </h2>
        <div className="figures" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          <div>
            <span className="v">{run.outcome ? OUTCOME_LABEL[run.outcome] : "overtaken"}</span>
            <span className="k">outcome</span>
          </div>
          <div>
            <span className="v">{v ? (v.attempts === 1 ? "first draft" : `${v.attempts} drafts`) : "–"}</span>
            <span className="k">{v && v.attempts > 1 ? "repaired after failing a check" : "attempts"}</span>
          </div>
          <div>
            <span className="v">{usd(run.cost_usd)}</span>
            <span className="k">at list prices{run.seconds ? `, ${run.seconds}s` : ""}</span>
          </div>
          <div>
            <span className="v">{checks.length ? `${passed} of ${checks.length}` : "–"}</span>
            <span className="k">policy checks passed</span>
          </div>
        </div>
      </section>

      {run.error && (
        <section className="sig sig-coral" aria-label="The run failed">
          <span className="eyebrow">The run failed; nothing was drafted for the loan</span>
          <p className="mono">{run.error}</p>
        </section>
      )}

      <div className="split">
        <section aria-labelledby="given" style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <h2 id="given" className="section-title">
            What it was told
          </h2>
          <p className="note">
            Repaydesk gathered these facts itself, live from core banking and the directory. The drafter has no lookups.
          </p>
          <pre className="raw" style={{ margin: 0, maxHeight: 420, overflow: "auto" }}>
            {JSON.stringify(brief, null, 1)}
          </pre>
        </section>

        <section aria-labelledby="drafted" style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <h2 id="drafted" className="section-title">
            The message, and the checks
          </h2>
          {m ? (
            <div className="cream" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="eyebrow">
                {run.model} · {MESSAGE_CHANNEL[m.channel] ?? m.channel} · {m.body.length} characters
              </span>
              <p style={{ margin: 0, fontSize: 18, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>{m.body}</p>
              {v?.note && <span className="note">To the manager: {v.note}</span>}
            </div>
          ) : (
            <p className="note">No message: see why it failed, above.</p>
          )}
          {checks.length > 0 && (
            <section className="panel" aria-label="The policy checks">
              <header>
                <h2>
                  {passed === checks.length
                    ? "It passes the collections policy"
                    : "It fails the collections policy: a person must fix it before it can go"}
                </h2>
              </header>
              <div className="body">
                {checks.map((c, i) => (
                  <div key={i} className={`check ${c.ok ? "yes" : "no"}`}>
                    <span className="m" aria-label={c.ok ? "passed" : "failed"}>
                      {c.ok ? "✓" : "✕"}
                    </span>
                    <span>{c.detail}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
          {m && (
            <section className="panel" aria-label="The message now">
              <header>
                <h2>The message now</h2>
              </header>
              <div className="body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <span>
                  Message {m.id}: <Pill status={m.status} />
                  {m.decided_by && <span className="note"> by {m.decided_by}</span>}
                </span>
                {m.reject_reason && <span className="note">Rejected: {m.reject_reason}</span>}
                <div className="actions">
                  {run.loan_ref && <LoanLink loanRef={run.loan_ref} />}
                  <Link className="btn" to="/collections">
                    Collections
                  </Link>
                </div>
              </div>
            </section>
          )}
          <Tokens run={run} />
        </section>
      </div>
    </>
  );
}
